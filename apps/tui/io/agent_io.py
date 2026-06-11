from __future__ import annotations


def _split_agent_messages_by_cutoff(items: list[dict] | None, cutoff_ts: float) -> tuple[list[dict], list[dict]]:
    visible: list[dict] = []
    hidden: list[dict] = []
    cutoff = float(cutoff_ts or 0.0)
    for item in list(items or []):
        ts = float((item or {}).get("ts") or 0.0)
        if ts >= cutoff:
            visible.append(item)
        else:
            hidden.append(item)
    return visible, hidden
