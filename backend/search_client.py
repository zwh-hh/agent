from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

DEFAULT_TIMEOUT = 45


def normalize_url(raw_url: str) -> str:
    try:
        parsed = urlparse(raw_url)
        clean_query: list[tuple[str, str]] = []
        drop = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "gclid",
            "fbclid",
        }
        for key, val in parse_qsl(parsed.query, keep_blank_values=True):
            if key in drop:
                continue
            clean_query.append((key, val))

        clean_query.sort(key=lambda x: x[0])
        path = parsed.path[:-1] if parsed.path.endswith("/") and parsed.path != "/" else parsed.path

        return urlunparse((parsed.scheme, parsed.netloc, path, "", urlencode(clean_query), ""))
    except Exception:
        return (raw_url or "").strip()


def dedupe_source_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls or []:
        normalized = normalize_url(url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _dedupe_items(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        normalized = normalize_url(item.get("url") or item.get("link") or "")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        copied = dict(item)
        copied["normalizedUrl"] = normalized
        out.append(copied)
    return out


async def search_with_tavily(*, api_key: str, query: str, max_results: int = 8) -> list[dict]:
    if not api_key:
        raise RuntimeError("Missing TAVILY_API_KEY.")

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "advanced",
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
            },
        )

    if response.status_code >= 400:
        raise RuntimeError(f"Tavily search failed: {response.status_code} {response.text}")

    data = response.json()
    items = [
        {
            "title": item.get("title") or item.get("url"),
            "url": item.get("url"),
            "snippet": item.get("content") or "",
            "source": "tavily",
            "score": item.get("score"),
        }
        for item in data.get("results", [])
    ]
    return _dedupe_items(items)


async def search_with_serpapi(*, api_key: str, query: str, max_results: int = 8) -> list[dict]:
    if not api_key:
        raise RuntimeError("Missing SERPAPI_API_KEY.")

    params = {
        "q": query,
        "api_key": api_key,
        "engine": "google",
        "num": max(1, min(max_results, 10)),
    }

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.get("https://serpapi.com/search.json", params=params)

    if response.status_code >= 400:
        raise RuntimeError(f"SerpAPI search failed: {response.status_code} {response.text}")

    data = response.json()
    items = [
        {
            "title": item.get("title") or item.get("link"),
            "url": item.get("link"),
            "snippet": item.get("snippet") or "",
            "source": "serpapi",
            "score": item.get("position"),
        }
        for item in data.get("organic_results", [])
    ]
    return _dedupe_items(items)


async def search_web(
    *,
    provider: str,
    query: str,
    max_results: int,
    tavily_api_key: str | None,
    serp_api_key: str | None,
) -> list[dict]:
    if provider == "none":
        return []
    if provider == "tavily":
        return await search_with_tavily(api_key=tavily_api_key or "", query=query, max_results=max_results)
    if provider == "serpapi":
        return await search_with_serpapi(api_key=serp_api_key or "", query=query, max_results=max_results)
    raise RuntimeError(f"Unsupported search provider: {provider}")
