from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskStore:
    base_dir: Path
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def tasks_dir(self) -> Path:
        return self.base_dir / "data" / "tasks"

    @property
    def reports_dir(self) -> Path:
        return self.base_dir / "data" / "reports"

    async def ensure_store(self) -> None:
        await asyncio.to_thread(self.tasks_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self.reports_dir.mkdir, parents=True, exist_ok=True)

    async def load_stored_tasks(self) -> None:
        if not self.tasks_dir.exists():
            return

        for file_path in self.tasks_dir.glob("*.json"):
            try:
                raw = await asyncio.to_thread(file_path.read_text, "utf-8")
                task = json.loads(raw)
                task_id = task.get("id")
                if task_id:
                    self.tasks[task_id] = task
            except Exception:
                continue

    async def create_task(self, *, topic: str, provider: str, search_provider: str, agent_mode: str) -> dict[str, Any]:
        async with self.lock:
            task_id = str(uuid4())
            task = {
                "id": task_id,
                "topic": topic,
                "provider": provider,
                "searchProvider": search_provider,
                "agentMode": agent_mode,
                "status": "queued",
                "createdAt": now_iso(),
                "updatedAt": now_iso(),
                "progress": 0,
                "events": [],
                "result": None,
                "error": None,
                "reportPath": None,
            }
            self.tasks[task_id] = task
            return deepcopy(task)

    async def list_tasks(self) -> list[dict[str, Any]]:
        async with self.lock:
            values = list(self.tasks.values())

        values.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        out = []
        for task in values:
            out.append(
                {
                    "id": task.get("id"),
                    "topic": task.get("topic"),
                    "provider": task.get("provider"),
                    "searchProvider": task.get("searchProvider"),
                    "agentMode": task.get("agentMode", "multi"),
                    "status": task.get("status"),
                    "createdAt": task.get("createdAt"),
                    "updatedAt": task.get("updatedAt"),
                    "progress": task.get("progress", 0),
                    "reportPath": task.get("reportPath"),
                    "error": task.get("error"),
                }
            )
        return out

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with self.lock:
            task = self.tasks.get(task_id)
            return deepcopy(task) if task else None

    async def append_task_event(self, task_id: str, event: dict[str, Any]) -> dict[str, Any] | None:
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return None

            normalized = {
                "time": now_iso(),
                "stage": event.get("stage", "info"),
                "message": event.get("message", ""),
                "progress": event.get("progress", task.get("progress", 0)),
                "payload": event.get("payload"),
            }
            task["progress"] = normalized["progress"]
            task["updatedAt"] = now_iso()
            task.setdefault("events", []).append(normalized)
            task["events"] = task["events"][-500:]
            return deepcopy(normalized)

    async def update_task_status(self, task_id: str, status: str) -> None:
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task["status"] = status
            task["updatedAt"] = now_iso()

    async def set_task_result(self, task_id: str, result: dict[str, Any]) -> None:
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task["result"] = result
            task["updatedAt"] = now_iso()

    async def set_task_error(self, task_id: str, error_message: str) -> None:
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task["error"] = error_message
            task["updatedAt"] = now_iso()

    async def persist_task(self, task_id: str) -> None:
        task = await self.get_task(task_id)
        if not task:
            return
        file_path = self.tasks_dir / f"{task_id}.json"
        content = json.dumps(task, ensure_ascii=False, indent=2)
        await asyncio.to_thread(file_path.write_text, content, "utf-8")

    async def persist_report(self, task_id: str, markdown: str) -> Path | None:
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return None
            report_path = self.reports_dir / f"{task_id}.md"
            task["reportPath"] = str(report_path)
            task["updatedAt"] = now_iso()

        await asyncio.to_thread(report_path.write_text, markdown, "utf-8")
        return report_path
