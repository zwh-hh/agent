from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_TIMEOUT = 120


@dataclass
class ProviderConfig:
    provider: str
    openai: dict[str, Any]
    local: dict[str, Any]


def _normalize_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


async def _chat_completion(
    *,
    base_url: str,
    api_key: str | None,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.post(
            url,
            headers=headers,
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
            },
        )

    if response.status_code >= 400:
        raise RuntimeError(f"Model request failed: {response.status_code} {response.text}")

    data = response.json()
    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return _normalize_text(text)


def make_model_invoker(config: ProviderConfig):
    provider = config.provider

    if provider == "openai":
        api_key = config.openai.get("apiKey")
        if not api_key:
            raise RuntimeError("Missing OPENAI_API_KEY. Please configure it in .env or UI.")

        async def invoke(*, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
            return await _chat_completion(
                base_url=config.openai.get("baseUrl", "https://api.openai.com/v1"),
                api_key=api_key,
                model=config.openai.get("model", "gpt-4.1"),
                messages=messages,
                temperature=temperature,
            )

        return invoke

    if provider == "local":

        async def invoke(*, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
            return await _chat_completion(
                base_url=config.local.get("baseUrl", "http://127.0.0.1:11434/v1"),
                api_key=config.local.get("apiKey"),
                model=config.local.get("model", "llama3.1"),
                messages=messages,
                temperature=temperature,
            )

        return invoke

    raise RuntimeError(f"Unsupported provider: {provider}")
