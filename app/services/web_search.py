"""web_search.py — live web search for questions the ingested corpus can't answer.

Tries Exa, then Tavily, then Serper — whichever are configured, in that order — and
returns results shaped like a RAG `parent_documents` entry so they can flow through
the same citation-building path as a corpus hit, but tagged `kind: "web"` so the
caller can keep them out of the vetted "Manbalar" list and render them as a visibly
separate, quieter block instead.

Exa is tried first: it's built specifically for LLM-grounding use cases (clean
content extracts rather than raw SERP snippets) and has the most generous free tier
of the three (no card required). Tavily and Serper remain as fallbacks if Exa is
unset or a request fails.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


async def _search_exa(query: str, api_key: str, max_results: int) -> Optional[List[Dict[str, Any]]]:
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "query": query,
        "type": "auto",
        "numResults": max_results,
        "contents": {"text": {"maxCharacters": 2000}},
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post("https://api.exa.ai/search", headers=headers, json=payload)
        if resp.status_code != 200:
            logger.warning(f"Exa search returned {resp.status_code}: {resp.text[:200]}")
            return None
        return resp.json().get("results", [])
    except Exception:
        logger.exception("Exa search failed")
        return None


async def _search_tavily(query: str, api_key: str, max_results: int) -> Optional[List[Dict[str, Any]]]:
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "include_answer": False,
        "max_results": max_results,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post("https://api.tavily.com/search", json=payload)
        if resp.status_code != 200:
            logger.warning(f"Tavily search returned {resp.status_code}: {resp.text[:200]}")
            return None
        return resp.json().get("results", [])
    except Exception:
        logger.exception("Tavily search failed")
        return None


async def _search_serper(query: str, api_key: str, max_results: int) -> Optional[List[Dict[str, Any]]]:
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "gl": "uz", "hl": "uz", "num": max_results}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post("https://google.serper.dev/search", headers=headers, json=payload)
        if resp.status_code != 200:
            logger.warning(f"Serper search returned {resp.status_code}: {resp.text[:200]}")
            return None
        return resp.json().get("organic", [])
    except Exception:
        logger.exception("Serper search failed")
        return None


def _to_parent_documents(query: str, raw_results: List[Dict[str, Any]], provider: str) -> List[Dict[str, Any]]:
    """Shapes raw provider results into the `parent_documents` contract `_build_citations`
    (chat.py) already expects, with each result getting a unique `id` — reusing the same
    `id` across results collapses every one but the first in `_build_citations`'s
    seen-part-id dedup.
    """
    out = []
    for idx, r in enumerate(raw_results, start=1):
        title = r.get("title") or "Web result"
        link = r.get("url") or r.get("link") or "#"
        content = r.get("content") or r.get("snippet") or r.get("text") or ""
        out.append({
            "id": f"web-{provider}-{idx}",
            "source_doc_id": f"web-{provider}-{idx}",
            "root_title": title,
            "title": title,
            "source_url": link,
            "full_markdown": content,
            "part_index": 0,
            "kind": "web",
        })
    return out


async def perform_web_search(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Returns web results shaped as `parent_documents`, or an empty list.

    Tries Exa, then Tavily, then Serper — whichever are configured — and degrades
    silently if none are, or all fail. The toggle should never turn a working
    conversational answer into a hard error.
    """
    if settings.EXA_API_KEY:
        results = await _search_exa(query, settings.EXA_API_KEY, max_results)
        if results:
            return _to_parent_documents(query, results, "exa")

    if settings.TAVILY_API_KEY:
        results = await _search_tavily(query, settings.TAVILY_API_KEY, max_results)
        if results:
            return _to_parent_documents(query, results, "tavily")

    if settings.SERPER_API_KEY:
        results = await _search_serper(query, settings.SERPER_API_KEY, max_results)
        if results:
            return _to_parent_documents(query, results, "serper")

    if not settings.EXA_API_KEY and not settings.TAVILY_API_KEY and not settings.SERPER_API_KEY:
        logger.warning("Web search requested but no provider (EXA/TAVILY/SERPER) API key is configured.")
    return []
