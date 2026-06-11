from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from backend.quant_pro.dashboard_data import _db


def _nst_today_str() -> str:
    """Return today's Nepal trading date as YYYY-MM-DD."""
    return (datetime.utcnow() + timedelta(hours=5, minutes=45)).strftime("%Y-%m-%d")


def _format_nst_hm(ts: float | None) -> str:
    """Render an epoch timestamp in Nepal time as HH:MM."""
    if not ts:
        return ""
    try:
        return (datetime.utcfromtimestamp(float(ts)) + timedelta(hours=5, minutes=45)).strftime("%H:%M")
    except Exception:
        return ""


def _load_intraday_ohlcv(
    symbol: str,
    *,
    preferred_session_date: Optional[str] = None,
    bucket_minutes: int = 5,
) -> tuple[pd.DataFrame, Optional[str], int]:
    """Build intraday OHLCV bars from stored quote snapshots."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return pd.DataFrame(), None, 0

    conn = _db()
    try:
        target_date = preferred_session_date
        if target_date:
            probe = pd.read_sql_query(
                """
                SELECT COUNT(*) AS cnt
                FROM market_quotes
                WHERE symbol = ?
                  AND date(datetime(fetched_at_utc, '+5 hours', '+45 minutes')) = ?
                """,
                conn,
                params=(sym, target_date),
            )
            if int(probe["cnt"].iloc[0] or 0) <= 0:
                target_date = None

        if not target_date:
            latest = pd.read_sql_query(
                """
                SELECT MAX(date(datetime(fetched_at_utc, '+5 hours', '+45 minutes'))) AS session_date
                FROM market_quotes
                WHERE symbol = ?
                """,
                conn,
                params=(sym,),
            )
            target_date = latest["session_date"].iloc[0]

        if not target_date:
            return pd.DataFrame(), None, 0

        raw = pd.read_sql_query(
            """
            SELECT fetched_at_utc, last_traded_price, close_price, total_trade_quantity
            FROM market_quotes
            WHERE symbol = ?
              AND date(datetime(fetched_at_utc, '+5 hours', '+45 minutes')) = ?
            ORDER BY fetched_at_utc ASC
            """,
            conn,
            params=(sym, target_date),
        )
    finally:
        conn.close()

    if raw.empty:
        return pd.DataFrame(), str(target_date), 0

    rows = raw.copy()
    rows["price"] = pd.to_numeric(rows["last_traded_price"], errors="coerce")
    rows["close_price"] = pd.to_numeric(rows["close_price"], errors="coerce")
    rows["price"] = rows["price"].fillna(rows["close_price"])
    rows = rows[rows["price"] > 0].copy()
    if rows.empty:
        return pd.DataFrame(), str(target_date), 0

    rows["cum_qty"] = pd.to_numeric(rows["total_trade_quantity"], errors="coerce").ffill().fillna(0.0)
    rows["volume"] = rows["cum_qty"].diff().clip(lower=0).fillna(0.0)
    rows["ts_nst"] = pd.to_datetime(rows["fetched_at_utc"], utc=True) + pd.Timedelta(hours=5, minutes=45)
    rows["bucket"] = rows["ts_nst"].dt.floor(f"{max(1, int(bucket_minutes))}min").dt.tz_localize(None)

    bars = (
        rows.groupby("bucket", sort=True)
        .agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("volume", "sum"),
        )
        .reset_index()
        .rename(columns={"bucket": "date"})
    )

    # If a coarse bucket collapses sparse snapshots into one bar, fall back to raw points
    # so the user still gets a visible same-session chart.
    if len(bars) < 2 and len(rows) >= 2:
        bars = pd.DataFrame(
            {
                "date": rows["ts_nst"].dt.tz_localize(None),
                "open": rows["price"],
                "high": rows["price"],
                "low": rows["price"],
                "close": rows["price"],
                "volume": rows["volume"],
            }
        )

    return bars, str(target_date), int(len(raw))


def _ensure_lookup_history(symbol: str, *, min_sessions: int = 2, history_days: int = 3650) -> int:
    """Backfill local daily OHLCV when lookup history is missing or too sparse."""
    sym = str(symbol or "").strip().upper()
    if not sym or sym == "NEPSE":
        return 0

    conn = _db()
    try:
        existing = pd.read_sql_query(
            "SELECT COUNT(*) AS cnt FROM stock_prices WHERE symbol = ?",
            conn,
            params=(sym,),
        )
        current_count = int(existing["cnt"].iloc[0] or 0)
    finally:
        conn.close()

    if current_count >= max(1, int(min_sessions)):
        return current_count

    try:
        from backend.quant_pro.database import save_to_db
        from backend.quant_pro.vendor_api import fetch_ohlcv_chunk

        end_ts = int(time.time())
        start_ts = int((datetime.now() - timedelta(days=max(30, int(history_days)))).timestamp())
        fetched = fetch_ohlcv_chunk(sym, start_ts=start_ts, end_ts=end_ts)
        if fetched is not None and not fetched.empty:
            save_to_db(fetched, sym, on_rebase="abort")
            return int(len(fetched))
    except Exception:
        return current_count
    return current_count


def _resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample daily OHLCV data to weekly, monthly, or yearly candles."""
    if timeframe in ("D", "I") or df.empty:
        return df
    rows = df.copy()
    rows["date"] = pd.to_datetime(rows["date"])
    rows = rows.sort_values("date").set_index("date")
    rule = {"W": "W", "M": "ME", "Y": "YE"}.get(timeframe, "W")
    agg = rows.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna(subset=["open"])
    agg = agg.reset_index()
    return agg
