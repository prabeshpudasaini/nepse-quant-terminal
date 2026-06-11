from __future__ import annotations

from typing import Optional


def _stock_watchlist_entry(symbol: str) -> dict:
    sym = str(symbol or "").strip().upper()
    return {
        "kind": "stock",
        "key": f"stock:{sym}",
        "label": sym,
        "symbol": sym,
    }


def _normalize_watchlist_entry(item) -> Optional[dict]:
    if isinstance(item, str):
        sym = item.strip().upper()
        return _stock_watchlist_entry(sym) if sym else None
    if not isinstance(item, dict):
        return None

    kind = str(item.get("kind") or "stock").strip().lower()
    if kind == "stock":
        sym = str(item.get("symbol") or item.get("label") or "").strip().upper()
        return _stock_watchlist_entry(sym) if sym else None

    label = str(item.get("label") or item.get("symbol") or item.get("currency_code") or "").strip()
    if not label:
        return None
    key = str(item.get("key") or f"{kind}:{label}").strip()
    normalized = dict(item)
    normalized["kind"] = kind
    normalized["label"] = label
    normalized["key"] = key
    return normalized


def _watchlist_entry_key(item: dict) -> str:
    return str((item or {}).get("key") or "")


def _dedupe_watchlist_entries(entries: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for item in entries or []:
        normalized = _normalize_watchlist_entry(item)
        if not normalized:
            continue
        key = _watchlist_entry_key(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped
