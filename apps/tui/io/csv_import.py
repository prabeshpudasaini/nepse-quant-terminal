from __future__ import annotations

import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from backend.quant_pro.paths import get_project_root
from backend.trading.live_trader import (
    NAV_LOG_COLS,
    PORTFOLIO_COLS,
    TRADE_LOG_COLS,
)

from apps.tui.io.watchlist_io import (
    _normalize_watchlist_entry,
    _stock_watchlist_entry,
    _watchlist_entry_key,
)

PROJECT_ROOT = get_project_root(__file__)


def _coerce_dragdrop_path(raw_value: str) -> Optional[Path]:
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    try:
        parts = shlex.split(raw)
        if len(parts) == 1:
            raw = parts[0]
    except Exception:
        raw = raw.strip("\"'")
    raw = raw.replace("\\ ", " ")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    else:
        path = path.resolve()
    return path


def _normalize_frame_columns(df: pd.DataFrame) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for col in df.columns:
        key = re.sub(r"[^a-z0-9]+", "", str(col).strip().lower())
        normalized[key] = str(col)
    return normalized


def _pick_column(df: pd.DataFrame, *aliases: str) -> Optional[str]:
    normalized = _normalize_frame_columns(df)
    for alias in aliases:
        key = re.sub(r"[^a-z0-9]+", "", str(alias).strip().lower())
        match = normalized.get(key)
        if match:
            return match
    return None


def _is_meroshare_csv(df: pd.DataFrame) -> bool:
    """Detect MeroShare 'My Shares Values' export format."""
    cols_lower = {str(c).strip().lower() for c in df.columns}
    return "scrip" in cols_lower and "current balance" in cols_lower


def _normalize_meroshare_portfolio(df: pd.DataFrame) -> pd.DataFrame:
    """Convert MeroShare 'My Shares Values' CSV to internal portfolio format.

    MeroShare columns:
      S.N | Scrip | Current Balance | Last Closing Price | Value as of Last Closing Price
        | Last Transaction Price (LTP) | Value as of LTP
    The last row is a 'Total :' summary — skip it.
    Since MeroShare doesn't export actual buy prices, we use LTP as the
    entry price (mark-to-market seed) and flag entries as 'meroshare_import'.
    """
    # Identify columns by loose matching
    scrip_col = _pick_column(df, "Scrip")
    qty_col = _pick_column(df, "Current Balance", "Balance")
    ltp_col = _pick_column(df, "Last Transaction Price (LTP)", "LTP", "Last Closing Price")
    close_col = _pick_column(df, "Last Closing Price")

    if not scrip_col or not qty_col:
        raise ValueError("Not a valid MeroShare CSV — expected 'Scrip' and 'Current Balance' columns")

    today = datetime.now().strftime("%Y-%m-%d")
    rows: list[dict] = []

    for _, row in df.iterrows():
        symbol = str(row.get(scrip_col) or "").strip().upper()
        # Skip blank rows and the 'Total :' footer row
        if not symbol or symbol in ("TOTAL :", "TOTAL:", " ", ""):
            continue
        # Skip non-scrip rows (S.N cell is not numeric)
        sn = str(row.get(_pick_column(df, "S.N", "SN", "S N") or "", "")).strip()
        if sn and not sn.replace(".", "").isdigit():
            continue

        raw_qty = row.get(qty_col)
        qty = 0
        try:
            qty = int(round(float(str(raw_qty).replace(",", "") or 0)))
        except (ValueError, TypeError):
            pass
        if qty <= 0:
            continue

        # Price: prefer LTP, fall back to last close
        price = 0.0
        for col in [ltp_col, close_col]:
            if col:
                try:
                    price = float(str(row.get(col) or "0").replace(",", ""))
                    if price > 0:
                        break
                except (ValueError, TypeError):
                    pass
        if price <= 0:
            continue

        amount = round(qty * price, 2)
        rows.append({
            "Entry_Date": today,
            "Symbol": symbol,
            "Quantity": qty,
            "Buy_Price": price,
            "Buy_Amount": amount,
            "Buy_Fees": 0.0,
            "Total_Cost_Basis": amount,
            "Signal_Type": "meroshare_import",
            "High_Watermark": price,
            "Last_LTP": price,
            "Last_LTP_Source": "meroshare",
            "Last_LTP_Time_UTC": None,
        })

    result = pd.DataFrame(rows, columns=PORTFOLIO_COLS) if rows else pd.DataFrame(columns=PORTFOLIO_COLS)
    return result.reset_index(drop=True)


def _normalize_import_portfolio(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=PORTFOLIO_COLS)
    # Auto-detect MeroShare 'My Shares Values' CSV
    if _is_meroshare_csv(df):
        return _normalize_meroshare_portfolio(df)
    if set(PORTFOLIO_COLS).issubset(df.columns):
        normalized = df.copy()
    else:
        symbol_col = _pick_column(df, "Symbol", "Ticker", "Item", "Stock")
        qty_col = _pick_column(df, "Quantity", "Qty", "Shares", "Units")
        price_col = _pick_column(df, "Buy_Price", "Avg_Price", "Average_Price", "Entry_Price", "Price", "Cost")
        if not symbol_col or not qty_col or not price_col:
            raise ValueError("Portfolio CSV needs symbol, quantity, and buy price columns")
        entry_date_col = _pick_column(df, "Entry_Date", "Buy_Date", "Date", "Purchase_Date")
        signal_col = _pick_column(df, "Signal_Type", "Signal", "Strategy", "Reason")
        watermark_col = _pick_column(df, "High_Watermark", "High", "Peak")
        last_ltp_col = _pick_column(df, "Last_LTP", "LTP", "Current_Price", "Market_Price")
        last_ltp_source_col = _pick_column(df, "Last_LTP_Source", "Price_Source", "Source")
        last_ltp_time_col = _pick_column(df, "Last_LTP_Time_UTC", "Price_Time", "Updated_At")
        rows: list[dict] = []
        today = datetime.now().strftime("%Y-%m-%d")
        for _, row in df.iterrows():
            symbol = str(row.get(symbol_col) or "").strip().upper()
            if not symbol:
                continue
            qty = int(float(row.get(qty_col) or 0))
            price = float(row.get(price_col) or 0)
            if qty <= 0 or price <= 0:
                continue
            fees = float(row.get(_pick_column(df, "Buy_Fees", "Fees") or "", 0) or 0)
            amount = float(row.get(_pick_column(df, "Buy_Amount", "Amount", "Gross_Amount") or "", qty * price) or (qty * price))
            total_cost = float(
                row.get(_pick_column(df, "Total_Cost_Basis", "Cost_Basis", "Net_Amount") or "", amount + fees)
                or (amount + fees)
            )
            entry_date = str(row.get(entry_date_col) or today)[:10]
            signal_type = str(row.get(signal_col) or "imported").strip().lower() or "imported"
            high_watermark = float(row.get(watermark_col) or price)
            last_ltp = row.get(last_ltp_col) if last_ltp_col else None
            last_ltp_source = row.get(last_ltp_source_col) if last_ltp_source_col else None
            last_ltp_time = row.get(last_ltp_time_col) if last_ltp_time_col else None
            rows.append(
                {
                    "Entry_Date": entry_date,
                    "Symbol": symbol,
                    "Quantity": qty,
                    "Buy_Price": price,
                    "Buy_Amount": amount,
                    "Buy_Fees": fees,
                    "Total_Cost_Basis": total_cost,
                    "Signal_Type": signal_type,
                    "High_Watermark": high_watermark,
                    "Last_LTP": last_ltp,
                    "Last_LTP_Source": last_ltp_source,
                    "Last_LTP_Time_UTC": last_ltp_time,
                }
            )
        normalized = pd.DataFrame(rows, columns=PORTFOLIO_COLS)
    normalized = normalized.reindex(columns=PORTFOLIO_COLS)
    if normalized.empty:
        return pd.DataFrame(columns=PORTFOLIO_COLS)
    normalized["Symbol"] = normalized["Symbol"].astype(str).str.strip().str.upper()
    normalized["Quantity"] = pd.to_numeric(normalized["Quantity"], errors="coerce").fillna(0).astype(int)
    normalized["Buy_Price"] = pd.to_numeric(normalized["Buy_Price"], errors="coerce").fillna(0.0)
    normalized["Buy_Amount"] = pd.to_numeric(normalized["Buy_Amount"], errors="coerce").fillna(
        normalized["Quantity"] * normalized["Buy_Price"]
    )
    normalized["Buy_Fees"] = pd.to_numeric(normalized["Buy_Fees"], errors="coerce").fillna(0.0)
    normalized["Total_Cost_Basis"] = pd.to_numeric(normalized["Total_Cost_Basis"], errors="coerce").fillna(
        normalized["Buy_Amount"] + normalized["Buy_Fees"]
    )
    normalized["High_Watermark"] = pd.to_numeric(normalized["High_Watermark"], errors="coerce").fillna(normalized["Buy_Price"])
    return normalized[normalized["Symbol"] != ""].reset_index(drop=True)


def _normalize_import_trade_log(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=TRADE_LOG_COLS)
    if set(TRADE_LOG_COLS).issubset(df.columns):
        normalized = df.copy()
    else:
        date_col = _pick_column(df, "Date", "Trade_Date")
        action_col = _pick_column(df, "Action", "Side")
        symbol_col = _pick_column(df, "Symbol", "Ticker", "Item")
        shares_col = _pick_column(df, "Shares", "Quantity", "Qty")
        price_col = _pick_column(df, "Price", "Rate", "Execution_Price")
        if not date_col or not action_col or not symbol_col or not shares_col or not price_col:
            raise ValueError("Trade log CSV needs date, action, symbol, shares, and price columns")
        fees_col = _pick_column(df, "Fees", "Fee", "Commission")
        reason_col = _pick_column(df, "Reason", "Signal", "Strategy", "Note")
        pnl_col = _pick_column(df, "PnL", "P&L", "Profit")
        pnl_pct_col = _pick_column(df, "PnL_Pct", "Return_Pct", "P&L_Pct")
        rows: list[dict] = []
        for _, row in df.iterrows():
            symbol = str(row.get(symbol_col) or "").strip().upper()
            action = str(row.get(action_col) or "").strip().upper()
            if not symbol or action not in {"BUY", "SELL"}:
                continue
            rows.append(
                {
                    "Date": str(row.get(date_col) or "")[:10],
                    "Action": action,
                    "Symbol": symbol,
                    "Shares": int(float(row.get(shares_col) or 0)),
                    "Price": float(row.get(price_col) or 0),
                    "Fees": float(row.get(fees_col) or 0) if fees_col else 0.0,
                    "Reason": str(row.get(reason_col) or "imported").strip() or "imported",
                    "PnL": float(row.get(pnl_col) or 0) if pnl_col else 0.0,
                    "PnL_Pct": float(row.get(pnl_pct_col) or 0) if pnl_pct_col else 0.0,
                }
            )
        normalized = pd.DataFrame(rows, columns=TRADE_LOG_COLS)
    normalized = normalized.reindex(columns=TRADE_LOG_COLS)
    if normalized.empty:
        return pd.DataFrame(columns=TRADE_LOG_COLS)
    normalized["Symbol"] = normalized["Symbol"].astype(str).str.strip().str.upper()
    return normalized[normalized["Symbol"] != ""].reset_index(drop=True)


def _normalize_import_nav_log(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=NAV_LOG_COLS)
    if set(NAV_LOG_COLS).issubset(df.columns):
        normalized = df.copy()
    else:
        date_col = _pick_column(df, "Date", "Session_Date")
        nav_col = _pick_column(df, "NAV", "Net_Asset_Value")
        if not date_col or not nav_col:
            raise ValueError("NAV CSV needs at least date and NAV columns")
        cash_col = _pick_column(df, "Cash")
        pos_col = _pick_column(df, "Positions_Value", "Invested", "Positions")
        num_col = _pick_column(df, "Num_Positions", "Positions_Count", "Holdings")
        rows: list[dict] = []
        for _, row in df.iterrows():
            nav = float(row.get(nav_col) or 0)
            if nav <= 0:
                continue
            rows.append(
                {
                    "Date": str(row.get(date_col) or "")[:10],
                    "Cash": float(row.get(cash_col) or 0) if cash_col else 0.0,
                    "Positions_Value": float(row.get(pos_col) or 0) if pos_col else 0.0,
                    "NAV": nav,
                    "Num_Positions": int(float(row.get(num_col) or 0)) if num_col else 0,
                }
            )
        normalized = pd.DataFrame(rows, columns=NAV_LOG_COLS)
    normalized = normalized.reindex(columns=NAV_LOG_COLS)
    return normalized.reset_index(drop=True)


def _build_holdings_watchlist_entries(port: pd.DataFrame, ltps: Optional[dict[str, float]] = None) -> list[dict]:
    if port is None or port.empty or "Symbol" not in port.columns:
        return []
    rows: list[tuple[float, str]] = []
    last_ltp_map = {}
    if "Last_LTP" in port.columns:
        last_ltp_map = {
            str(row.get("Symbol") or "").strip().upper(): float(row.get("Last_LTP") or 0)
            for _, row in port.iterrows()
        }
    for _, row in port.iterrows():
        sym = str(row.get("Symbol") or "").strip().upper()
        qty = int(float(row.get("Quantity") or 0))
        if not sym or qty <= 0:
            continue
        price = float((ltps or {}).get(sym) or last_ltp_map.get(sym) or row.get("Buy_Price") or 0)
        rows.append((qty * price, sym))
    rows.sort(reverse=True)
    return [_stock_watchlist_entry(sym) for _, sym in rows]


def _merge_watchlist_entries(*groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or []:
            normalized = _normalize_watchlist_entry(item)
            if not normalized:
                continue
            key = _watchlist_entry_key(normalized)
            if key in seen:
                continue
            seen.add(key)
            merged.append(normalized)
    return merged


def _positive_float(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _dedupe_symbol_rows(rows: list | tuple) -> list:
    """Return one row per symbol, keeping the last row seen for duplicate symbols."""
    deduped: dict[str, Any] = {}
    for row in rows or []:
        try:
            symbol = str(row[0] or "").strip().upper()
        except Exception:
            continue
        if not symbol:
            continue
        deduped[symbol] = row
    return list(deduped.values())
