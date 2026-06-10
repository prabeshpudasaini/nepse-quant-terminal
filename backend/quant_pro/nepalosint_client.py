"""
NepalOSINT API client for semantic and unified search.

NOTE: This OSINT enrichment service is OPTIONAL and self-hosted. It is disabled
by default in the public build — no base URL is configured, so every function
degrades gracefully (empty result, no network call) until you point it at your
own endpoint. Configure it via the NEPALOSINT_BASE_URL or NEPALOSINT_API_BASE_URL
environment variable (or pass an explicit base_url argument). An optional
NEPALOSINT_API_KEY enables authenticated access.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

import requests
from requests import Timeout

DEFAULT_OSINT_BASE_URL = ""
DEFAULT_TIMEOUT_SECONDS = 8
_TOKEN_CACHE: dict[str, Any] = {"base_url": "", "token": "", "expires_at": 0.0}
_TOKEN_LOCK = threading.Lock()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def resolve_osint_base_url(base_url: str | None = None) -> str:
    raw = (
        str(base_url or "").strip()
        or str(os.environ.get("NEPALOSINT_BASE_URL", "")).strip()
        or str(os.environ.get("NEPALOSINT_API_BASE_URL", "")).strip()
        or DEFAULT_OSINT_BASE_URL
    )
    cleaned = raw.rstrip("/")
    lowered = cleaned.lower()
    if lowered.endswith("/api/v1"):
        return cleaned
    if "/api/v1/" not in lowered and re.match(r"^https?://[^/]+$", cleaned):
        return f"{cleaned}/api/v1"
    return cleaned


def _auth_headers(*, bearer_token: str = "", api_key: str = "", json_request: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "NEPSE-Quant-NepalOSINT/1.0",
    }
    if json_request:
        headers["Content-Type"] = "application/json"
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if api_key:
        headers["X-API-Key"] = api_key
        if not bearer_token:
            headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _cache_token(api_root: str, token: str) -> str:
    if not token:
        return ""
    with _TOKEN_LOCK:
        _TOKEN_CACHE["base_url"] = api_root
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["expires_at"] = time.time() + 3300
    return token


def _cached_token(api_root: str) -> str:
    with _TOKEN_LOCK:
        if (
            str(_TOKEN_CACHE.get("base_url") or "") == api_root
            and float(_TOKEN_CACHE.get("expires_at") or 0.0) > time.time() + 5
        ):
            return str(_TOKEN_CACHE.get("token") or "")
    return ""


def _login_nepalosint(api_root: str, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    api_key = str(os.environ.get("NEPALOSINT_API_KEY", "")).strip()
    if api_key:
        return api_key

    cached = _cached_token(api_root)
    if cached:
        return cached

    headers = _auth_headers(json_request=True)

    try:
        response = requests.post(
            f"{api_root}/auth/public",
            headers=headers,
            json={},
            timeout=timeout,
        )
        response.raise_for_status()
        token = str(dict(response.json() or {}).get("access_token") or "").strip()
        if token:
            return _cache_token(api_root, token)
    except Exception:
        pass

    return ""


def _request_json(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    base_url: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    api_root = resolve_osint_base_url(base_url)
    if not api_root:
        # Service is not configured (default in the public build): no base URL
        # means no network call. Callers catch this and return their neutral
        # empty result.
        raise RuntimeError("NepalOSINT base URL is not configured")
    api_key = str(os.environ.get("NEPALOSINT_API_KEY", "")).strip()
    request = requests.get if method.upper() == "GET" else requests.post

    def _send(*, refresh_token: bool = False):
        if refresh_token:
            with _TOKEN_LOCK:
                _TOKEN_CACHE["base_url"] = ""
                _TOKEN_CACHE["token"] = ""
                _TOKEN_CACHE["expires_at"] = 0.0
        bearer_token = "" if api_key else _login_nepalosint(api_root, timeout=timeout)
        headers = _auth_headers(
            bearer_token=bearer_token,
            api_key=api_key,
            json_request=method.upper() != "GET",
        )
        return request(
            f"{api_root}{path}",
            headers=headers,
            params=params or None,
            json=payload if method.upper() != "GET" else None,
            timeout=timeout,
        )

    try:
        response = _send()
    except Timeout:
        response = _send()
    if response.status_code == 401 and not api_key:
        response = _send(refresh_token=True)
    elif response.status_code in {502, 503, 504}:
        response = _send()
    response.raise_for_status()
    return response.json()


def semantic_story_search(
    query: str,
    *,
    hours: int = 720,
    top_k: int = 10,
    min_similarity: float = 0.45,
    base_url: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload = {
        "query": str(query or "").strip(),
        "hours": int(hours),
        "top_k": int(top_k),
        "min_similarity": float(min_similarity),
    }
    try:
        data = dict(
            _request_json(
                "POST",
                "/embeddings/search",
                payload=payload,
                base_url=base_url,
                timeout=timeout,
            )
            or {}
        )
    except Exception as exc:
        return {
            "query": payload["query"],
            "results": [],
            "total_found": 0,
            "error": str(exc),
        }
    data.setdefault("query", payload["query"])
    data["results"] = list(data.get("results") or [])
    data["total_found"] = int(data.get("total_found") or len(data["results"]))
    return data


def unified_search(
    query: str,
    *,
    limit: int = 10,
    election_year: int | None = None,
    extra_params: dict[str, Any] | None = None,
    base_url: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "q": str(query or "").strip(),
        "limit": int(limit),
    }
    if extra_params:
        params.update({str(key): value for key, value in dict(extra_params).items() if value is not None})
    if election_year is not None:
        params["election_year"] = int(election_year)
    try:
        data = dict(
            _request_json(
                "GET",
                "/search/unified",
                params=params,
                base_url=base_url,
                timeout=timeout,
            )
            or {}
        )
    except Exception as exc:
        return {
            "query": params["q"],
            "total": 0,
            "categories": {},
            "error": str(exc),
        }
    categories = dict(data.get("categories") or {})
    for name, payload in list(categories.items()):
        item_block = dict(payload or {})
        item_block["items"] = list(item_block.get("items") or [])
        item_block["total"] = int(item_block.get("total") or len(item_block["items"]))
        categories[name] = item_block
    data.setdefault("query", params["q"])
    data["total"] = int(data.get("total") or 0)
    data["categories"] = categories
    return data


def consolidated_stories_history(
    *,
    start_date: str,
    end_date: str,
    limit: int = 100,
    offset: int = 0,
    category: str | None = None,
    story_type: str | None = None,
    severity: str | None = None,
    districts: str | None = None,
    source: str | None = None,
    base_url: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "start_date": str(start_date),
        "end_date": str(end_date),
        "limit": int(limit),
        "offset": int(offset),
    }
    for key, value in {
        "category": category,
        "story_type": story_type,
        "severity": severity,
        "districts": districts,
        "source": source,
    }.items():
        if value:
            params[key] = str(value)
    try:
        payload = _request_json(
            "GET",
            "/analytics/consolidated-stories/history",
            params=params,
            base_url=base_url,
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "start_date": params["start_date"],
            "end_date": params["end_date"],
            "items": [],
            "total_count": 0,
            "item_count": 0,
            "limit": params["limit"],
            "offset": params["offset"],
            "has_more": False,
            "error": str(exc),
        }
    data = dict(payload or {}) if isinstance(payload, dict) else {"items": payload if isinstance(payload, list) else []}
    data.setdefault("start_date", params["start_date"])
    data.setdefault("end_date", params["end_date"])
    data["items"] = [dict(item or {}) for item in list(data.get("items") or []) if isinstance(item, dict)]
    data["item_count"] = int(data.get("item_count") or len(data["items"]))
    data["total_count"] = int(data.get("total_count") or data["item_count"])
    data["limit"] = int(data.get("limit") or params["limit"])
    data["offset"] = int(data.get("offset") or params["offset"])
    data["has_more"] = bool(data.get("has_more"))
    return data


def related_stories(
    story_id: str,
    *,
    top_k: int = 8,
    min_similarity: float = 0.55,
    hours: int = 8760,
    base_url: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    clean_id = str(story_id or "").strip()
    if not clean_id:
        return {"source_story_id": "", "similar_stories": [], "total_found": 0}
    try:
        data = dict(
            _request_json(
                "GET",
                f"/stories/{clean_id}/related",
                params={
                    "top_k": int(top_k),
                    "min_similarity": float(min_similarity),
                    "hours": int(hours),
                },
                base_url=base_url,
                timeout=timeout,
            )
            or {}
        )
    except Exception as exc:
        return {
            "source_story_id": clean_id,
            "similar_stories": [],
            "total_found": 0,
            "error": str(exc),
        }
    data.setdefault("source_story_id", clean_id)
    data["similar_stories"] = list(data.get("similar_stories") or [])
    data["total_found"] = int(data.get("total_found") or len(data["similar_stories"]))
    return data


def symbol_intelligence(
    query: str,
    *,
    hours: int = 720,
    top_k: int = 6,
    min_similarity: float = 0.45,
    base_url: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    semantic = semantic_story_search(
        query,
        hours=hours,
        top_k=top_k,
        min_similarity=min_similarity,
        base_url=base_url,
        timeout=timeout,
    )
    unified = unified_search(
        query,
        limit=max(int(top_k), 6),
        base_url=base_url,
        timeout=timeout,
    )
    categories = dict(unified.get("categories") or {})
    story_items = list(dict(categories.get("stories") or {}).get("items") or [])
    social_items = list(dict(categories.get("social_signals") or {}).get("items") or [])

    lead_story_id = ""
    if story_items:
        lead_story_id = str(story_items[0].get("id") or "").strip()
    elif semantic.get("results"):
        lead_story_id = str(semantic["results"][0].get("story_id") or "").strip()

    related = related_stories(
        lead_story_id,
        top_k=min(max(int(top_k), 3), 8),
        base_url=base_url,
        timeout=timeout,
    ) if lead_story_id else {"source_story_id": "", "similar_stories": [], "total_found": 0}

    return {
        "query": str(query or "").strip(),
        "semantic": semantic,
        "unified": unified,
        "related": related,
        "lead_story_id": lead_story_id,
        "story_items": story_items,
        "social_items": social_items,
        "related_items": list(related.get("similar_stories") or []),
        "story_count": len(story_items) or int(semantic.get("total_found") or 0),
        "social_count": len(social_items),
        "related_count": int(related.get("total_found") or 0),
    }


def consolidated_stories(
    *,
    limit: int = 30,
    base_url: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    try:
        payload = _request_json(
            "GET",
            "/analytics/consolidated-stories",
            params={"limit": int(limit)},
            base_url=base_url,
            timeout=timeout,
        )
    except Exception:
        return []

    if isinstance(payload, list):
        return [dict(item or {}) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        stories = payload.get("stories") or payload.get("items") or []
        return [dict(item or {}) for item in list(stories) if isinstance(item, dict)]
    return []
