from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CLEAR_SENTINEL = "__CLEAR__"


@dataclass
class SettingsStore:
    base_dir: Path

    @property
    def settings_dir(self) -> Path:
        return self.base_dir / "data" / "settings"

    @property
    def secrets_file(self) -> Path:
        return self.settings_dir / "secrets.json"

    async def ensure_store(self) -> None:
        await asyncio.to_thread(self.settings_dir.mkdir, parents=True, exist_ok=True)

    async def _read_raw(self) -> dict[str, Any]:
        if not self.secrets_file.exists():
            return {}
        try:
            raw = await asyncio.to_thread(self.secrets_file.read_text, "utf-8")
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    async def _write_raw(self, data: dict[str, Any]) -> None:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self.secrets_file.write_text, content, "utf-8")
        try:
            await asyncio.to_thread(os.chmod, self.secrets_file, 0o600)
        except Exception:
            # Best effort on systems that do not support chmod as expected.
            pass

    async def get_runtime_config(self) -> dict[str, Any]:
        saved = await self._read_raw()

        runtime = {
            "openai": {
                "apiKey": os.getenv("OPENAI_API_KEY") or "",
                "baseUrl": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                "model": os.getenv("OPENAI_MODEL", "gpt-4.1"),
            },
            "local": {
                "apiKey": os.getenv("LOCAL_MODEL_API_KEY", "local-not-required"),
                "baseUrl": os.getenv("LOCAL_MODEL_BASE_URL", "http://127.0.0.1:11434/v1"),
                "model": os.getenv("LOCAL_MODEL_NAME", "llama3.1"),
            },
            "search": {
                "tavilyApiKey": os.getenv("TAVILY_API_KEY") or "",
                "serpApiKey": os.getenv("SERPAPI_API_KEY") or "",
            },
        }

        for section in ("openai", "local", "search"):
            patch = saved.get(section)
            if isinstance(patch, dict):
                for key, value in patch.items():
                    if isinstance(value, str):
                        runtime[section][key] = value

        return runtime

    async def get_public_settings(self) -> dict[str, Any]:
        runtime = await self.get_runtime_config()

        return {
            "openaiBaseUrl": runtime["openai"]["baseUrl"],
            "openaiModel": runtime["openai"]["model"],
            "localBaseUrl": runtime["local"]["baseUrl"],
            "localModel": runtime["local"]["model"],
            "hasOpenaiApiKey": bool(runtime["openai"]["apiKey"]),
            "hasLocalApiKey": bool(runtime["local"]["apiKey"]),
            "hasTavilyApiKey": bool(runtime["search"]["tavilyApiKey"]),
            "hasSerpApiKey": bool(runtime["search"]["serpApiKey"]),
        }

    async def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        await self.ensure_store()
        saved = await self._read_raw()

        def apply(section: str, key: str, value: Any) -> None:
            if value is None:
                return
            if section not in saved or not isinstance(saved[section], dict):
                saved[section] = {}

            if isinstance(value, str) and value == CLEAR_SENTINEL:
                saved[section][key] = ""
                return

            if isinstance(value, str):
                trimmed = value.strip()
                if trimmed == "":
                    return
                saved[section][key] = trimmed

        apply("openai", "apiKey", updates.get("openaiApiKey"))
        apply("openai", "baseUrl", updates.get("openaiBaseUrl"))
        apply("openai", "model", updates.get("openaiModel"))

        apply("local", "apiKey", updates.get("localApiKey"))
        apply("local", "baseUrl", updates.get("localBaseUrl"))
        apply("local", "model", updates.get("localModel"))

        apply("search", "tavilyApiKey", updates.get("tavilyApiKey"))
        apply("search", "serpApiKey", updates.get("serpApiKey"))

        await self._write_raw(saved)
        return await self.get_public_settings()
