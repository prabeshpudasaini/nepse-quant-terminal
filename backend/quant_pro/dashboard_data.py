from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import pandas as pd

from backend.quant_pro.database import get_db_path
from backend.quant_pro.paths import ensure_dir, get_trading_runtime_dir

PAPER_PORTFOLIO_FILE = ensure_dir(get_trading_runtime_dir(__file__)) / "paper_portfolio.csv"


def _db() -> sqlite3.Connection:
    return sqlite3.connect(str(get_db_path()))


class MD:
    def __init__(self, top_n: int = 50):
        self.top_n = top_n
        self.err: Optional[str] = None
        self.latest = self.prev_d = "—"
        self.df = self.gainers = self.losers = self.vol_top = pd.DataFrame()
        self.near_hi = self.near_lo = self.quotes = self.nepse = self.corp = pd.DataFrame()
        self.adv = self.dec = self.unch = 0
        self.ts = datetime.now()
        self.tms = None
        self.tms_balance: Optional[Dict] = None
        self.tms_indices: Dict[str, object] = {}
        self.refresh()

    def refresh(self):
        try:
            c = _db()
            quotes = pd.read_sql_query(
                "SELECT symbol,last_traded_price ltp,previous_close prev_close,percentage_change pc,"
                "total_trade_quantity vol,fetched_at_utc ts FROM market_quotes "
                "ORDER BY fetched_at_utc DESC",
                c,
            ).drop_duplicates("symbol")
            for col in ("ltp", "prev_close", "pc", "vol"):
                quotes[col] = pd.to_numeric(quotes[col], errors="coerce").fillna(0.0)

            lat_d = pd.read_sql_query(
                "SELECT MAX(date) d FROM stock_prices WHERE symbol!='NEPSE'", c)["d"].iloc[0]
            prv_d = None
            if pd.notna(lat_d):
                prv_d = pd.read_sql_query(
                    "SELECT MAX(date) d FROM stock_prices WHERE symbol!='NEPSE' AND date<?",
                    c, params=(lat_d,))["d"].iloc[0]

            if pd.notna(lat_d):
                lat = pd.read_sql_query(
                    "SELECT symbol,open,high,low,close,volume FROM stock_prices WHERE date=?",
                    c, params=(lat_d,))
            else:
                lat = pd.DataFrame(columns=["symbol", "open", "high", "low", "close", "volume"])

            lat = lat[lat["symbol"] != "NEPSE"].copy()
            lat = lat.drop_duplicates("symbol", keep="last")
            if not quotes.empty:
                quote_fallback = quotes.rename(
                    columns={"ltp": "quote_ltp", "prev_close": "quote_prev", "vol": "quote_vol"}
                )[["symbol", "quote_ltp", "quote_prev", "quote_vol"]]
            else:
                quote_fallback = pd.DataFrame(columns=["symbol", "quote_ltp", "quote_prev", "quote_vol"])

            if lat.empty and not quote_fallback.empty:
                df = pd.DataFrame({
                    "symbol": quote_fallback["symbol"],
                    "open": quote_fallback["quote_ltp"],
                    "high": quote_fallback["quote_ltp"],
                    "low": quote_fallback["quote_ltp"],
                    "close": quote_fallback["quote_ltp"],
                    "volume": quote_fallback["quote_vol"],
                })
            else:
                df = lat.copy()

            if not quote_fallback.empty:
                df = df.merge(quote_fallback, on="symbol", how="left")
            else:
                df["quote_ltp"] = pd.NA
                df["quote_prev"] = pd.NA
                df["quote_vol"] = pd.NA

            if pd.notna(prv_d):
                prv = pd.read_sql_query(
                    "SELECT symbol,close prev FROM stock_prices WHERE date=?",
                    c, params=(prv_d,))
                prv = prv.drop_duplicates("symbol", keep="last")
                df = df.merge(prv, on="symbol", how="left")
            else:
                df["prev"] = pd.NA

            df = df.drop_duplicates("symbol", keep="last")
            for col in ("open", "high", "low", "close", "volume", "quote_ltp", "quote_prev", "quote_vol", "prev"):
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df["close"] = df["close"].fillna(df["quote_ltp"])
            df["open"] = df["open"].fillna(df["close"])
            df["high"] = df["high"].fillna(df["close"])
            df["low"] = df["low"].fillna(df["close"])
            df["volume"] = df["volume"].fillna(df["quote_vol"]).fillna(0.0)
            df["prev"] = df["prev"].fillna(df["quote_prev"])
            prev_mask = df["prev"].fillna(0) > 0
            df["chg"] = 0.0
            df.loc[prev_mask, "chg"] = (df.loc[prev_mask, "close"] - df.loc[prev_mask, "prev"]) / df.loc[prev_mask, "prev"] * 100
            df["turn"] = df["close"].fillna(0.0) * df["volume"].fillna(0.0)
            df = df.drop(columns=["quote_ltp", "quote_prev", "quote_vol"], errors="ignore")

            display_session = lat_d
            if pd.isna(display_session) and not quotes.empty and "ts" in quotes.columns:
                try:
                    display_session = pd.to_datetime(quotes["ts"].iloc[0]).strftime("%Y-%m-%d")
                except Exception:
                    display_session = "—"

            self.latest = display_session if pd.notna(display_session) else "—"
            self.prev_d = prv_d if pd.notna(prv_d) else "—"
            self.df = df
            filt = df[df["chg"].abs() <= 12]
            self.gainers = filt.nlargest(self.top_n, "chg")
            self.losers  = filt.nsmallest(self.top_n, "chg")
            self.vol_top = df.nlargest(self.top_n, "volume")
            self.adv  = int((df["chg"] > 0).sum())
            self.dec  = int((df["chg"] < 0).sum())
            self.unch = int((df["chg"] == 0).sum())

            yr = pd.read_sql_query(
                "SELECT symbol,MAX(high) h,MIN(low) l FROM stock_prices "
                "WHERE date>=date(?,'-365 days') AND symbol!='NEPSE' GROUP BY symbol",
                c, params=(lat_d,))
            d52 = df.merge(yr, on="symbol", how="left")
            self.near_hi = d52[d52["close"] >= d52["h"] * 0.97].nlargest(self.top_n, "chg")
            self.near_lo = d52[d52["close"] <= d52["l"] * 1.03].nsmallest(self.top_n, "chg")

            self.quotes = quotes

            self.nepse = pd.read_sql_query(
                "SELECT close FROM stock_prices WHERE symbol='NEPSE' "
                "ORDER BY date DESC LIMIT 2", c)

            try:
                corp = pd.read_sql_query(
                    "SELECT symbol,bookclose_date,cash_dividend_pct,bonus_share_pct "
                    "FROM corporate_actions WHERE bookclose_date>? AND bookclose_date<=? "
                    "ORDER BY bookclose_date", c,
                    params=(datetime.now().strftime("%Y-%m-%d"),
                            (datetime.now()+timedelta(days=30)).strftime("%Y-%m-%d")))
                corp["bookclose_date"] = pd.to_datetime(corp["bookclose_date"])
                self.corp = corp
            except Exception:
                self.corp = pd.DataFrame()

            c.close(); self.ts = datetime.now(); self.err = None
        except Exception as e:
            self.err = str(e)

        # Pull cached TMS snapshots if a source is attached — silent on failure.
        if self.tms is not None:
            try:
                self.tms_balance = self.tms.balance()
                self.tms_indices = self.tms.indices()
            except Exception:
                pass

    def ltps(self) -> Dict[str, float]:
        base: Dict[str, float] = {
            str(r.symbol): float(r.ltp)
            for r in self.quotes.itertuples() if float(r.ltp) > 0
        }
        # Live TMS ticker overlays DB quotes whenever the WS has fresh frames.
        if self.tms is not None:
            try:
                live = self.tms.ltps() if self.tms.is_live() else {}
            except Exception:
                live = {}
            if live:
                base.update(live)
        return base

    def detail(self, sym: str, limit: int = 60) -> Optional[Dict]:
        c = _db()
        h = pd.read_sql_query(
            "SELECT date,open,high,low,close,volume FROM stock_prices "
            "WHERE symbol=? ORDER BY date DESC LIMIT ?", c, params=(sym, limit))
        ca = pd.read_sql_query(
            "SELECT bookclose_date,cash_dividend_pct,bonus_share_pct "
            "FROM corporate_actions WHERE symbol=? ORDER BY bookclose_date DESC LIMIT 5",
            c, params=(sym,))
        c.close()
        if h.empty: return None
        lat = h.iloc[0]; prv = h.iloc[1] if len(h) > 1 else lat
        chg = (lat["close"]-prv["close"])/prv["close"]*100 if prv["close"] else 0
        return {"h": h, "lat": lat, "chg": chg, "ca": ca}


def load_port() -> pd.DataFrame:
    return pd.read_csv(PAPER_PORTFOLIO_FILE) if PAPER_PORTFOLIO_FILE.exists() else pd.DataFrame()


def save_port(df: pd.DataFrame):
    ensure_dir(PAPER_PORTFOLIO_FILE.parent)
    df.to_csv(PAPER_PORTFOLIO_FILE, index=False)


def _tms_dry_submit(side: str, sym: str, qty: int, price: float) -> str:
    """TMS order routing not available in public release."""
    return ""


def exec_buy(sym: str, sh: str, pr: str) -> str:
    try: shares = int(sh)
    except ValueError: return f"Invalid shares: {sh}"
    try:
        c = _db()
        r = pd.read_sql_query(
            "SELECT close FROM stock_prices WHERE symbol=? ORDER BY date DESC LIMIT 1",
            c, params=(sym,))
        c.close()
        if r.empty: return f"Symbol {sym} not found"
        price = float(pr) if pr else float(r.iloc[0]["close"])
    except Exception as e:
        return f"Price lookup: {e}"
    amt = price * shares; fees = round(amt * 0.004, 4); cost = round(amt + fees, 4)
    tms_tail = _tms_dry_submit("BUY", sym, shares, price)
    port = load_port()
    port = pd.concat([port, pd.DataFrame([{
        "Entry_Date": datetime.now().strftime("%Y-%m-%d"),
        "Symbol": sym, "Quantity": shares, "Buy_Price": price,
        "Buy_Amount": round(amt, 4), "Buy_Fees": fees, "Total_Cost_Basis": cost,
        "Signal_Type": "manual", "High_Watermark": price, "Last_LTP": price,
        "Last_LTP_Source": "manual",
        "Last_LTP_Time_UTC": datetime.now(timezone.utc).isoformat(),
    }])], ignore_index=True)
    save_port(port)
    return f"BUY  {shares}x{sym} @ {price:.1f}  cost {cost:,.0f} NPR  fees {fees:.0f}{tms_tail}"


def exec_sell(sym: str, sh: str, pr: str) -> str:
    port = load_port()
    if port.empty: return "Portfolio empty"
    mask = port["Symbol"] == sym
    if not mask.any(): return f"{sym} not in portfolio"
    total = int(port[mask]["Quantity"].sum())
    try: sell = total if sh.lower() == "all" else int(sh)
    except ValueError: return "Invalid qty"
    if sell <= 0 or sell > total: return f"Invalid qty — holding {total}"
    try:
        price = float(pr) if pr else None
        if not price:
            c = _db()
            r = pd.read_sql_query(
                "SELECT close FROM stock_prices WHERE symbol=? ORDER BY date DESC LIMIT 1",
                c, params=(sym,)); c.close()
            price = float(r.iloc[0]["close"]) if not r.empty else float(port[mask].iloc[0]["Buy_Price"])
    except Exception:
        price = float(port[mask].iloc[0]["Buy_Price"])
    if sell == total:
        port = port[~mask]
    else:
        port.at[port[mask].index[0], "Quantity"] = total - sell
    save_port(port)
    tms_tail = _tms_dry_submit("SELL", sym, sell, price)
    return f"SELL  {sell}x{sym} @ {price:.1f}  proceeds {price*sell:,.0f} NPR{tms_tail}"
