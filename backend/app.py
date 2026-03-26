from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .research_agent import run_deep_research
from .settings_store import SettingsStore
from .task_store import TaskStore

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
PUBLIC_DIR = BASE_DIR / "public"

app = FastAPI(title="Deep Research Agent")
store = TaskStore(base_dir=BASE_DIR)
settings_store = SettingsStore(base_dir=BASE_DIR)
subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
subscribers_lock = asyncio.Lock()


class ResearchTaskCreate(BaseModel):
    topic: str
    context: str | None = None
    userSources: list[str] | None = None
    depth: str = "standard"
    agentMode: str = "multi"
    provider: str = "openai"
    searchProvider: str = "none"
    searchMaxResults: int = Field(default=8, ge=3, le=12)


class SettingsUpdateRequest(BaseModel):
    openaiApiKey: str | None = None
    openaiBaseUrl: str | None = None
    openaiModel: str | None = None
    localApiKey: str | None = None
    localBaseUrl: str | None = None
    localModel: str | None = None
    tavilyApiKey: str | None = None
    serpApiKey: str | None = None


class SettingsUpdateResponse(BaseModel):
    openaiBaseUrl: str
    openaiModel: str
    localBaseUrl: str
    localModel: str
    hasOpenaiApiKey: bool
    hasLocalApiKey: bool
    hasTavilyApiKey: bool
    hasSerpApiKey: bool


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def verify_admin_token(x_admin_token: str | None) -> None:
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured on server.")
    if x_admin_token != expected:
        raise HTTPException(status_code=401, detail="Invalid admin token.")


async def broadcast_task_event(task_id: str, event: dict[str, Any]) -> None:
    async with subscribers_lock:
        queues = list(subscribers.get(task_id, []))
    for q in queues:
        await q.put(("progress", {"taskId": task_id, **event}))


async def publish_done(task_id: str, payload: dict[str, Any]) -> None:
    async with subscribers_lock:
        queues = list(subscribers.get(task_id, []))
    for q in queues:
        await q.put(("done", payload))


async def execute_task(task_id: str, payload: ResearchTaskCreate) -> None:
    await store.update_task_status(task_id, "running")
    await store.persist_task(task_id)

    runtime = await settings_store.get_runtime_config()

    provider_config = {
        "provider": payload.provider,
        "openai": {
            "apiKey": runtime["openai"]["apiKey"],
            "model": runtime["openai"]["model"],
            "baseUrl": runtime["openai"]["baseUrl"],
        },
        "local": {
            "baseUrl": runtime["local"]["baseUrl"],
            "apiKey": runtime["local"]["apiKey"],
            "model": runtime["local"]["model"],
        },
    }

    resolved_search_provider = payload.searchProvider if payload.searchProvider in {"none", "tavily", "serpapi"} else "none"
    search_config = {
        "provider": resolved_search_provider,
        "tavilyApiKey": runtime["search"]["tavilyApiKey"],
        "serpApiKey": runtime["search"]["serpApiKey"],
        "maxResults": max(3, min(payload.searchMaxResults, 12)),
    }

    async def on_progress(event: dict[str, Any]) -> None:
        saved = await store.append_task_event(task_id, event)
        if not saved:
            return
        await broadcast_task_event(task_id, saved)
        await store.persist_task(task_id)

    try:
        result = await run_deep_research(
            topic=payload.topic,
            context=payload.context,
            user_sources=[x for x in (payload.userSources or []) if x][:20],
            depth=payload.depth,
            provider_config=provider_config,
            search_config=search_config,
            agent_mode=payload.agentMode,
            on_progress=on_progress,
        )

        report_header = "\n".join(
            [
                "# Deep Research Report",
                "",
                f"- Task ID: {task_id}",
                f"- Topic: {payload.topic}",
                f"- Generated At: {result['meta']['generatedAt']}",
                f"- Provider: {result['meta']['provider']} / {result['meta']['model']}",
                f"- Search: {result['meta']['searchProvider']}",
                f"- Agent Mode: {result['meta']['agentMode']}",
                "",
            ]
        )

        markdown = report_header + result["report"]

        await store.set_task_result(task_id, result)
        await store.update_task_status(task_id, "completed")
        await store.persist_report(task_id, markdown)
        await store.persist_task(task_id)

        done_event = await store.append_task_event(
            task_id,
            {
                "stage": "complete",
                "message": "Task completed and report persisted",
                "progress": 100,
                "payload": {"markdownReady": True},
            },
        )
        if done_event:
            await broadcast_task_event(task_id, done_event)

        await publish_done(task_id, {"taskId": task_id, "status": "completed"})
    except Exception as exc:
        message = str(exc)
        await store.set_task_error(task_id, message)
        await store.update_task_status(task_id, "failed")

        fail_event = await store.append_task_event(
            task_id,
            {"stage": "error", "message": message, "progress": 100},
        )
        await store.persist_task(task_id)

        if fail_event:
            await broadcast_task_event(task_id, fail_event)

        await publish_done(task_id, {"taskId": task_id, "status": "failed", "error": message})


@app.on_event("startup")
async def startup_event() -> None:
    await store.ensure_store()
    await store.load_stored_tasks()
    await settings_store.ensure_store()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    from datetime import datetime, timezone

    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/settings", response_model=SettingsUpdateResponse)
async def get_settings(x_admin_token: str | None = Header(default=None)):
    verify_admin_token(x_admin_token)
    return await settings_store.get_public_settings()


@app.put("/api/settings", response_model=SettingsUpdateResponse)
async def update_settings(payload: SettingsUpdateRequest, x_admin_token: str | None = Header(default=None)):
    verify_admin_token(x_admin_token)
    return await settings_store.update_settings(payload.model_dump())


@app.post("/api/research/tasks")
async def create_research_task(payload: ResearchTaskCreate) -> JSONResponse:
    if not payload.topic or not payload.topic.strip():
        raise HTTPException(status_code=400, detail="`topic` is required.")

    runtime = await settings_store.get_runtime_config()

    if payload.provider == "openai" and not runtime["openai"]["apiKey"]:
        raise HTTPException(status_code=400, detail="OpenAI API Key is missing. Please configure it in /settings.")

    if payload.searchProvider == "tavily" and not runtime["search"]["tavilyApiKey"]:
        raise HTTPException(status_code=400, detail="Tavily API Key is missing. Please configure it in /settings.")

    if payload.searchProvider == "serpapi" and not runtime["search"]["serpApiKey"]:
        raise HTTPException(status_code=400, detail="SerpAPI Key is missing. Please configure it in /settings.")

    mode = "single" if payload.agentMode == "single" else "multi"
    payload.agentMode = mode

    task = await store.create_task(
        topic=payload.topic,
        provider=payload.provider,
        search_provider=payload.searchProvider if payload.searchProvider in {"none", "tavily", "serpapi"} else "none",
        agent_mode=mode,
    )

    await store.append_task_event(
        task["id"],
        {
            "stage": "queued",
            "message": "Task accepted",
            "progress": 0,
            "payload": {
                "topic": payload.topic,
                "depth": payload.depth,
                "provider": payload.provider,
                "searchProvider": payload.searchProvider,
                "agentMode": mode,
            },
        },
    )
    await store.persist_task(task["id"])

    asyncio.create_task(execute_task(task["id"], payload))

    return JSONResponse(
        status_code=202,
        content={
            "taskId": task["id"],
            "status": task["status"],
            "streamUrl": f"/api/research/tasks/{task['id']}/stream",
        },
    )


@app.get("/api/research/tasks")
async def list_research_tasks() -> dict[str, Any]:
    return {"tasks": await store.list_tasks()}


@app.get("/api/research/tasks/{task_id}")
async def get_research_task(task_id: str) -> dict[str, Any]:
    task = await store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": task}


@app.get("/api/research/tasks/{task_id}/markdown")
async def download_markdown(task_id: str):
    task = await store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    report_path = task.get("reportPath")
    if not report_path:
        raise HTTPException(status_code=409, detail="Report is not ready yet")

    return FileResponse(
        path=report_path,
        media_type="text/markdown; charset=utf-8",
        filename=f"report-{task_id}.md",
    )


@app.get("/api/research/tasks/{task_id}/stream")
async def stream_task(task_id: str):
    task = await store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    queue: asyncio.Queue = asyncio.Queue()

    async with subscribers_lock:
        subscribers[task_id].append(queue)

    async def event_generator():
        try:
            snapshot = {
                "taskId": task_id,
                "status": task.get("status"),
                "progress": task.get("progress", 0),
                "events": (task.get("events") or [])[-20:],
            }
            yield sse_event("snapshot", snapshot)

            while True:
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield sse_event(event, data)
                    if event == "done":
                        break
                except asyncio.TimeoutError:
                    from datetime import datetime, timezone

                    yield sse_event("heartbeat", {"time": datetime.now(timezone.utc).isoformat()})
        finally:
            async with subscribers_lock:
                if task_id in subscribers and queue in subscribers[task_id]:
                    subscribers[task_id].remove(queue)
                if task_id in subscribers and not subscribers[task_id]:
                    subscribers.pop(task_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/")
async def index():
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/index.html")
async def index_html():
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/settings")
async def settings_page():
    return FileResponse(PUBLIC_DIR / "settings.html")


@app.get("/settings.html")
async def settings_html():
    return FileResponse(PUBLIC_DIR / "settings.html")


@app.get("/styles.css")
async def styles_css():
    return FileResponse(PUBLIC_DIR / "styles.css", media_type="text/css")


@app.get("/app.js")
async def app_js():
    return FileResponse(PUBLIC_DIR / "app.js", media_type="application/javascript")


@app.get("/settings.js")
async def settings_js():
    return FileResponse(PUBLIC_DIR / "settings.js", media_type="application/javascript")


app.mount("/assets", StaticFiles(directory=str(PUBLIC_DIR)), name="assets")
