from __future__ import annotations


def _build_sell_holdings_map(positions: list[dict] | None) -> dict[str, int]:
    holdings: dict[str, int] = {}
    for pos in positions or []:
        sym = str((pos or {}).get("sym") or "").strip().upper()
        qty = int((pos or {}).get("qty") or 0)
        if sym and qty > 0:
            holdings[sym] = qty
    return holdings


def _format_sell_holdings_summary(positions: list[dict] | None) -> str:
    holdings = _build_sell_holdings_map(positions)
    if not holdings:
        return "No holdings loaded"
    parts = [f"{sym} {qty}" for sym, qty in sorted(holdings.items())]
    lines: list[str] = []
    chunk = 3
    for idx in range(0, len(parts), chunk):
        lines.append("   ".join(parts[idx:idx + chunk]))
    return "\n".join(lines)


def _resolve_sell_qty(symbol: str, raw_shares: str, holdings: dict[str, int]) -> int:
    sym = str(symbol or "").strip().upper()
    total = int(holdings.get(sym) or 0)
    if total <= 0:
        raise ValueError(f"{sym or 'Symbol'} not in holdings")
    token = str(raw_shares or "").strip().lower()
    if not token or token == "all":
        return total
    try:
        qty = int(token)
    except ValueError as exc:
        raise ValueError("Shares must be a number or 'all'") from exc
    if qty <= 0 or qty > total:
        raise ValueError(f"Invalid qty — holding {total}")
    return qty


def _paper_filled_orders_for_day(order_history: list[dict] | None, day: str) -> list[dict]:
    filled: list[dict] = []
    for order in list(order_history or []):
        row = dict(order or {})
        if str(row.get("status") or "").upper() != "FILLED":
            continue
        stamp = str(row.get("updated_at") or row.get("created_at") or "")
        if stamp[:10] != str(day):
            continue
        filled.append(row)
    filled.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""))
    return filled
