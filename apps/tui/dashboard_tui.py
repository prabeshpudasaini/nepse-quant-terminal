#!/usr/bin/env python3
"""
NEPSE Bloomberg-Style Terminal Dashboard — Textual TUI

Run:  python3 dashboard_tui.py
Keys: 1-9 tabs │ / search │ L lookup │ R refresh │ Q quit
"""
from __future__ import annotations

import copy
import json as _json
import logging
import os
import re
import shutil
import shlex
import subprocess
import sys
import time
import unicodedata
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests as _requests
from rich.markup import escape as _escape_markup
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import ContentSwitcher, DataTable, Input, Static, Button, Label, OptionList
from textual.widgets.option_list import Option


from apps.tui.widgets.signal_defs import (  # re-export
    _silence_tui_noisy_loggers,
    _sig_btn_id,
    _sig_type_from_id,
    _SIGNAL_DEFS,
    _SIG_ID_PREFIX,
)


_silence_tui_noisy_loggers()





# ── Data layer + theme ────────────────────────────────────────────────────────
from backend.quant_pro.dashboard_data import (
    MD, _db, load_port, save_port, exec_buy, exec_sell,
)
from apps.tui.theme import (
    _vol, _pct, _npr,
    AMBER, WHITE, DIM, LABEL, GAIN_HI, GAIN, LOSS_HI, LOSS, CYAN, YELLOW, PURPLE, BLUE,
)
from configs.long_term import LONG_TERM_CONFIG

from apps.tui.render import charts as _charts
from apps.tui.render.cells import (  # re-export
    _chg_text,
    _sym_text,
    _dim_text,
    _vol_text,
    _price_text,
    _pnl_color,
    _npr_k,
    _format_compact_npr,
    _contains_non_ascii,
    _truncate_text,
    _news_display_headline,
)
from apps.tui.render.text_blocks import (  # re-export
    _render_stock_report,
    _render_lookup_intelligence,
    _headline_fallback_from_url,
)
from apps.tui.render.charts import _render_candlestick_chart, _render_sparkline  # re-export

from apps.tui.io.watchlist_io import (  # re-export
    _stock_watchlist_entry,
    _normalize_watchlist_entry,
    _watchlist_entry_key,
    _dedupe_watchlist_entries,
)
from apps.tui.io.csv_import import (  # re-export
    _normalize_import_portfolio,
    _merge_watchlist_entries,
    _dedupe_symbol_rows,
    _coerce_dragdrop_path,
    _normalize_frame_columns,
    _pick_column,
    _is_meroshare_csv,
    _normalize_meroshare_portfolio,
    _normalize_import_trade_log,
    _normalize_import_nav_log,
    _build_holdings_watchlist_entries,
    _positive_float,
)
from apps.tui.io.agent_io import _split_agent_messages_by_cutoff  # re-export
from apps.tui.io.orders_io import (  # re-export
    _paper_filled_orders_for_day,
    _build_sell_holdings_map,
    _format_sell_holdings_summary,
    _resolve_sell_qty,
)
from apps.tui.io import stats as _stats_io
from apps.tui.io.stats import (  # re-export
    _compute_portfolio_stats,
    _compute_account_portfolio_stats,
    _get_regime,
    _signal_label,
    _signal_code,
)
from apps.tui.io import intraday as _intraday
from apps.tui.io.intraday import (  # re-export
    _load_intraday_ohlcv,
    _ensure_lookup_history,
    _resample_ohlcv,
    _nst_today_str,
    _format_nst_hm,
)
from apps.tui.io.fetchers import (  # re-export
    _fetch_nrb_forex_rates,
    _fetch_gold_silver_prices,
    _fetch_yahoo_futures_price,
    _fetch_noc_fuel_prices,
    _extract_decimal_price,
    _translate_nepali,
    _translate_batch,
)
from apps.tui.io import persistence as _persistence
from apps.tui.io.persistence import (  # re-export
    INITIAL_CAPITAL,
    PROJECT_ROOT,
    RUNTIME_DIR,
    TRADING_RUNTIME_DIR,
    HEDGE_TRADE_LOG_FILE,
    WATCHLIST_FILE,
    PAPER_NAV_LOG_FILE,
    PAPER_TRADE_LOG_FILE,
    PAPER_STATE_FILE,
    PAPER_PORTFOLIO_FILE,
    TUI_PAPER_PORTFOLIO_FILE,
    TUI_PAPER_NAV_LOG_FILE,
    TUI_PAPER_TRADE_LOG_FILE,
    TUI_PAPER_STATE_FILE,
    PAPER_PROFILE_FILE,
    PAPER_IMPORT_BACKUP_DIR,
    PAPER_ACCOUNTS_DIR,
    PAPER_ACCOUNTS_REGISTRY_FILE,
    MACRO_INDICATOR_HISTORY_FILE,
    TUI_PAPER_ORDERS_FILE,
    TUI_PAPER_ORDER_HISTORY_FILE,
    ACTIVE_ACCOUNT_FILES,
    DEFAULT_WATCHLIST,
    _load_watchlist,
    _save_watchlist,
    _ensure_csv_file,
    _ensure_paper_runtime_files,
    _lock_file_exclusive,
    _unlock_file,
    _write_json_locked,
    _load_macro_indicator_history,
    _save_macro_indicator_history,
    _apply_indicator_history_change,
    _load_accounts_registry,
    _save_accounts_registry,
    _account_dir,
    _copy_file_if_exists,
    _blank_account_files,
    _next_account_id,
    _portfolio_mark_value,
    _build_account_seed_state,
    _load_profile_config,
    _save_profile_config,
    _bootstrap_paper_accounts,
    _load_manual_paper_cash,
    _account_initial_capital_from_files,
    _tms_health_flag,
    _load_cached_tms_bundle,
    _merge_tms_bundle_with_cache,
    _load_nav_log,
    _load_trade_log,
    _load_hedge_trade_log,
    _save_hedge_trade_log,
)

from backend.quant_pro.paths import (
    ensure_dir,
)
from backend.agents.agent_analyst import (
    analyze as agent_analyze,
    append_external_agent_chat_message,
    build_algo_shortlist_snapshot,
    publish_agent_signal_snapshot,
    check_trade_approval,
    load_agent_analysis,
    load_agent_archive_history,
    load_agent_history,
)
from backend.agents.runtime_config import (
    ACTIVE_AGENT_FILE,
    list_agent_backends,
    load_active_agent_config,
    set_active_agent,
)
from backend.quant_pro.stock_report import build_stock_report
from backend.trading.tui_trading_engine import TUITradingEngine
from backend.trading.paper_execution import PaperExecutionService
from backend.market.kalimati_market import init_kalimati_db, refresh_kalimati, get_kalimati_display_rows
from backend.trading.live_trader import (
    NAV_LOG_COLS,
    PORTFOLIO_COLS,
    TRADE_LOG_COLS,
    calculate_cash_from_trade_log,
    load_runtime_state,
    save_runtime_state,
)
from backend.trading import strategy_registry

MAX_ACCOUNTS = 5
AGENT_ARCHIVE_RENDER_LIMIT = 60
AGENT_CHAT_TIMEOUT_SECS = 90

STRATEGY_DISPLAY_NAMES = {
    "default_c5": "C5 Baseline",
    "temp_forward_winner": "TFW",
    "strat_3_p2r25": "P2R25",
    "strat_4_p3r32": "P3R32",
    "strat_6_r83": "R83",
    "strat_7_h53": "H53",
    
    
    
}

_TMS_AUDIT_SNAPSHOT_MAP = {
    "health": "tms_health",
    "account": "tms_account",
    "watchlist": "tms_watchlist",
    "funds": "tms_funds",
    "holdings": "tms_holdings",
    "orders_daily": "tms_orders_daily",
    "orders_historic": "tms_orders_historic",
    "trades_daily": "tms_trades_daily",
    "trades_historic": "tms_trades_historic",
}


def _display_live_override_enabled() -> bool:
    return False


# ── Ticker scroll speed ─────────────────────────────────────────────────────
TICKER_SPEED = 0.15  # seconds between scroll steps

# ── OSINT API ────────────────────────────────────────────────────────────────
# Optional, self-hosted OSINT enrichment service. Disabled by default in the
# public build — set NEPSE_OSINT_BASE to your own endpoint to enable it.
OSINT_BASE = os.environ.get("NEPSE_OSINT_BASE", "").rstrip("/")
OSINT_TIMEOUT = 8

# ── Unicode display-width helpers (Devanagari-aware) ─────────────────────────

def _disp_width(text: str) -> int:
    """Visual column width matching the patched Rich cell_len.

    Mn (non-spacing marks like virama) = 0 cells.
    Mc (spacing combining marks like ा ि ो) = 1 cell (macOS terminals).
    """
    return sum(
        0 if unicodedata.category(c) in ('Mn', 'Me', 'Cf') else 1
        for c in text
    )


def _truncate_display(text: str, max_cols: int, suffix: str = "…") -> str:
    """Truncate to max_cols display columns without splitting combining sequences."""
    text = unicodedata.normalize('NFC', text)
    w = 0
    suffix_w = len(suffix)
    for i, c in enumerate(text):
        cw = 0 if unicodedata.category(c) in ('Mn', 'Me', 'Cf') else 1
        if w + cw > max_cols - suffix_w:
            return text[:i] + suffix
        w += cw
    return text


def _wrap_display(text: str, width: int) -> list[str]:
    """Word-wrap by display column width (handles Devanagari combining chars)."""
    text = unicodedata.normalize('NFC', text)
    result: list[str] = []
    for para in text.splitlines():
        para = para.strip()
        if not para:
            if result and result[-1]:
                result.append('')
            continue
        words = para.split(' ')
        line = ''
        line_w = 0
        for word in words:
            word_w = _disp_width(word)
            if line_w == 0:
                line, line_w = word, word_w
            elif line_w + 1 + word_w <= width:
                line += ' ' + word
                line_w += 1 + word_w
            else:
                if line:
                    result.append(line)
                line, line_w = word, word_w
        if line:
            result.append(line)
    return result

def _news_display_summary(story: dict) -> str:
    """Return the best summary — prefer English, fall back to Nepali."""
    summary = str(story.get("summary") or "").strip()
    if summary and not _contains_non_ascii(summary):
        return summary
    translated_summary = str(story.get("_translated_summary") or "").strip()
    if translated_summary:
        return translated_summary
    translated = str(story.get("_translated") or "").strip()
    if translated:
        return translated
    url = str(story.get("url") or "").strip()
    if url:
        slug = _headline_fallback_from_url(url)
        if slug:
            return slug
    if summary:
        return summary
    return "No summary available."


def _fetch_osint_stories(limit: int = 40) -> list[dict]:
    """Fetch latest stories from Nepal OSINT API (disabled unless configured)."""
    if not OSINT_BASE:
        return []
    try:
        r = _requests.get(f"{OSINT_BASE}/analytics/consolidated-stories",
                          params={"limit": limit}, timeout=OSINT_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []

def _fetch_osint_brief() -> dict:
    """Fetch latest intelligence brief (disabled unless configured)."""
    if not OSINT_BASE:
        return {}
    try:
        r = _requests.get(f"{OSINT_BASE}/briefs/latest", timeout=OSINT_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}

# ── Data helpers ──────────────────────────────────────────────────────────────

def _fetch_usd_npr_rate() -> Optional[dict]:
    """Fetch latest USD/NPR rates from NRB."""
    api_url = "https://www.nrb.org.np/api/forex/v1/rates"
    try:
        today = datetime.utcnow().date()
        from_date = today - timedelta(days=7)
        response = _requests.get(
            api_url,
            params={
                "from": from_date.strftime("%Y-%m-%d"),
                "to": today.strftime("%Y-%m-%d"),
                "per_page": 50,
                "page": 1,
            },
            headers={
                "Accept": "application/json",
                "User-Agent": "Nepse-TUI/1.0",
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status", {}).get("code") != 200:
            return None

        latest_match = None
        for rate_data in data.get("data", {}).get("payload", []):
            date_str = rate_data.get("date")
            try:
                rate_date = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
            except Exception:
                rate_date = datetime.utcnow()
            for rate in rate_data.get("rates", []):
                currency = rate.get("currency", {})
                if currency.get("iso3") != "USD":
                    continue
                candidate = {
                    "currency_code": "USD",
                    "currency_name": currency.get("name", "US Dollar"),
                    "buy_rate": float(rate.get("buy", 0) or 0),
                    "sell_rate": float(rate.get("sell", 0) or 0),
                    "unit": int(currency.get("unit", 1) or 1),
                    "date": rate_date,
                    "source": "NRB",
                }
                if latest_match is None or candidate["date"] > latest_match["date"]:
                    latest_match = candidate
        return latest_match
    except Exception:
        return None


# _render_candlestick_chart resolves the resampler from its own module namespace.
_charts._resample_ohlcv = _resample_ohlcv


def _render_volume_chart(df: pd.DataFrame, width: int = 120, height: int = 6) -> str:
    """Render volume bar chart using plotext. Returns ANSI string."""
    import plotext as plt
    if df.empty:
        return ""

    rows = df.sort_values("date").reset_index(drop=True)
    dates = [str(d)[:10] for d in rows["date"]]
    vols = rows["volume"].tolist()
    colors = [
        "green" if float(rows.iloc[i]["close"]) >= float(rows.iloc[i]["open"]) else "red"
        for i in range(len(rows))
    ]

    plt.clear_figure()
    plt.date_form("Y-m-d")
    plt.bar(dates, vols, color=colors, width=0.8)
    plt.plotsize(max(width, 40), max(height, 4))
    plt.theme("dark")
    plt.canvas_color("black")
    plt.axes_color("black")
    plt.ticks_color("yellow")
    plt.title("Volume")
    return plt.build()


# Bind the persistence helpers onto the stats module so its moved compute
# functions resolve them in their own namespace.
_stats_io._load_nav_log = _persistence._load_nav_log
_stats_io._load_trade_log = _persistence._load_trade_log
_stats_io._load_manual_paper_cash = _persistence._load_manual_paper_cash
_stats_io._account_initial_capital_from_files = _persistence._account_initial_capital_from_files


# ═══════════════════════════════════════════════════════════════════════════════
# WIDGETS
# ═══════════════════════════════════════════════════════════════════════════════

from apps.tui.widgets.market_panel import MarketPanel  # re-export


# ═══════════════════════════════════════════════════════════════════════════════
# MODAL DIALOGS
# ═══════════════════════════════════════════════════════════════════════════════

from apps.tui.screens.dialog import ModalDialog  # re-export


# ═══════════════════════════════════════════════════════════════════════════════
# MODE SELECT SCREEN — shown on startup
# ═══════════════════════════════════════════════════════════════════════════════

from apps.tui.screens.mode_select import ModeSelectScreen  # re-export
from apps.tui.screens.tms_login import TMSLoginScreen, load_tms_settings  # re-export
from apps.tui.screens.command_palette import CommandPalette  # re-export
from apps.tui.screens.watchlist_add import WatchlistAddScreen  # re-export
from apps.tui.screens.lookup import LookupScreen  # re-export

from apps.tui.state.mixins.lifecycle import LifecycleMixin  # re-export
from apps.tui.state.mixins.header_status import HeaderStatusMixin  # re-export
from apps.tui.state.mixins.trading_engine import TradingEngineMixin  # re-export
from apps.tui.state.mixins.order_book import OrderBookMixin  # re-export
from apps.tui.state.mixins.tab_refresh import TabRefreshMixin  # re-export
from apps.tui.state.mixins.agent_chat import AgentChatMixin  # re-export
from apps.tui.state.mixins.accounts_strategies import AccountsStrategiesMixin  # re-export
from apps.tui.state.mixins.events_actions import EventsActionsMixin  # re-export
from apps.tui.state.mixins.live_tms import LiveTMSMixin  # re-export


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ═══════════════════════════════════════════════════════════════════════════════

TAB_NAMES = {
    "market": "1", "portfolio": "2", "signals": "3",
    "lookup": "4", "agents": "5", "orders": "6",
    "watchlist": "7", "kalimati": "8", "account": "9", "strategies": "0",
}

TAB_LABELS = {
    "market": "MARKET",
    "portfolio": "PORTFOLIO",
    "signals": "SIGNALS",
    "lookup": "LOOKUP",
    "agents": "AGENTS",
    "orders": "ORDERS",
    "watchlist": "WATCHLIST",
    "kalimati": "RATES & COMMODITIES",
    "account": "ACCOUNT",
    "strategies": "STRATEGIES",
}


class NepseDashboard(
    LifecycleMixin,
    HeaderStatusMixin,
    TradingEngineMixin,
    OrderBookMixin,
    TabRefreshMixin,
    AgentChatMixin,
    AccountsStrategiesMixin,
    EventsActionsMixin,
    LiveTMSMixin,
    App,
):
    CSS_PATH = Path(__file__).with_name("dashboard_tui.tcss")

    BINDINGS = [
        Binding("1", "tab('market')", "Market", show=False),
        Binding("2", "tab('portfolio')", "Portfolio", show=False),
        Binding("3", "tab('signals')", "Signals", show=False),
        Binding("4", "tab('lookup')", "Lookup", show=False),
        Binding("5", "tab('agents')", "Agents", show=False),
        Binding("6", "tab('orders')", "Orders", show=False),
        Binding("7", "tab('watchlist')", "Watchlist", show=False),
        Binding("8", "tab('kalimati')", "Rates & Commodities", show=False),
        Binding("9", "tab('account')", "Account", show=False),
        Binding("0", "tab('strategies')", "Strategies", show=False),
        # B/S hotkeys removed — orders placed via order book only
        Binding("l", "lookup", "Lookup", show=False),
        Binding("a", "run_agent", "Agent", show=False),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("f", "force_signals_reload", "Force Signals", show=False),
        Binding("q", "quit", "Quit", show=False),
        Binding("slash", "command_palette", "GO", show=False),
        Binding("ctrl+p", "command_palette", "GO", show=False),
        Binding("equals_sign", "watchlist_add", "=Watch", show=False),
        Binding("minus", "watchlist_remove", "-Watch", show=False),
        Binding("d", "tf('D')", "Daily", show=False),
        Binding("w", "tf('W')", "Weekly", show=False),
        Binding("m", "tf('M')", "Monthly", show=False),
        Binding("y", "tf('Y')", "Yearly", show=False),
        Binding("i", "tf('I')", "Intraday", show=False),
    ]

    active_tab: str = "market"
    lookup_sym: str = ""
    lookup_tf: str = "D"  # D=Daily, W=Weekly, M=Monthly, I=Intraday
    trade_mode: str = "paper"
    tms_service = None
    _tms_bundle = None   # cached fetch_monitor_bundle result
    _last_tms_watchlist_fetch_at: float = 0.0
    _tms_watchlist_refresh_inflight: bool = False
    _trading_engine: Optional[TUITradingEngine] = None
    _account_engines: dict = {}  # account_id -> TUITradingEngine
    _news_search_query: str = ""  # current news search filter
    _vector_search_results: list = []  # semantic search results from OSINT API
    _order_action: str = "BUY"   # current selected action in order form
    _paper_orders: list = []     # paper mode order book
    _paper_order_history: list = []  # filled/cancelled orders
    _paper_trades_today: list = []   # today's filled trades
    _paper_watchlist: list[dict] = []
    _live_watchlist: list[dict] = []
    _watchlist: list[dict] = []   # user watchlist entries
    _paper_accounts: list[dict] = []
    _current_account_id: str = "account_1"
    _selected_account_id: str = "account_1"
    _account_help_visible: bool = False
    _strategies: list[dict] = []
    _selected_strategy_id: str = "default_c5"
    _strategy_backtest_result: dict = {}
    _strategy_backtest_statuses: dict = {}
    _agent_chat_process: Optional[subprocess.Popen] = None
    _agent_chat_request_id: int = 0
    _agent_chat_stop_requested: bool = False
    _screener_sort: str = "chg"  # screener sort column
    _screener_filter: str = ""   # screener sector filter
    _refresh_inflight: bool = False

    PAPER_ORDERS_FILE = TUI_PAPER_ORDERS_FILE
    PAPER_ORDER_HISTORY_FILE = TUI_PAPER_ORDER_HISTORY_FILE

    def compose(self) -> ComposeResult:
        with Vertical(id="top-bars"):
            yield Static(id="header-bar")
            yield Static(id="ticker-bar")
            yield Static(id="index-bar")

        with ContentSwitcher(id="content", initial="market"):
            # ── 1 MARKET: 3-panel top + 2-panel bottom ──
            with Vertical(id="market", classes="tab-pane"):
                with Horizontal(id="market-top"):
                    yield MarketPanel(panel_title="1) GAINERS", title_color=GAIN, id="p-gainers")
                    yield MarketPanel(panel_title="2) LOSERS", title_color=LOSS, id="p-losers")
                    yield MarketPanel(panel_title="3) VOLUME LEADERS", title_color=CYAN, id="p-volume")
                with Horizontal(id="market-bot"):
                    yield MarketPanel(panel_title="4) 52-WEEK EXTREMES", title_color=YELLOW, id="p-52wk")
                    yield MarketPanel(panel_title="5) LIVE QUOTES", title_color=PURPLE, id="p-quotes")

            # ── 2 PORTFOLIO: NAV + risk bar + holdings/risk split + trades ──
            with Vertical(id="portfolio", classes="tab-pane"):
                yield Static("", id="nav-summary", classes="panel-title")
                yield Static("", id="risk-summary", classes="panel-title")
                with Horizontal(id="port-split"):
                    with Vertical(id="port-left", classes="full-pane"):
                        yield Static("HOLDINGS", classes="panel-title")
                        yield DataTable(id="dt-portfolio", zebra_stripes=True, cursor_type="row")
                    with Vertical(id="port-right", classes="full-pane"):
                        yield Static("CONCENTRATION & SECTOR", classes="panel-title")
                        yield DataTable(id="dt-concentration", zebra_stripes=True, cursor_type="row")
                        yield Static("WINNERS / LOSERS", classes="panel-title")
                        yield DataTable(id="dt-winloss", zebra_stripes=True, cursor_type="row")
                with Horizontal(id="port-bottom"):
                    with Vertical(id="port-hedge", classes="full-pane"):
                        with Horizontal(id="hedge-header"):
                            yield Static("GOLD / SILVER HEDGE", classes="panel-title", id="hedge-title")
                            yield Button("● HEDGE ON", id="hedge-toggle-btn", classes="hedge-btn-on")
                        yield Static("", id="hedge-info-bar")
                        yield Static("", id="hedge-rec-bar")
                        yield Static("", id="hedge-trade-bar")

            # ── 3 SIGNALS WORKSPACE ──
            with Vertical(id="signals", classes="tab-pane"):
                yield Static("", id="screener-status-bar")
                with Horizontal(id="signals-main"):
                    with Vertical(id="signals-table-pane", classes="full-pane"):
                        with Horizontal(id="signals-header-row"):
                            yield Static("SIGNALS", classes="panel-title", id="signals-table-title")
                            yield Input(value="0.0", placeholder="min score", id="signal-min-score", classes="signal-threshold-input")
                        yield DataTable(id="dt-signals", zebra_stripes=True, cursor_type="row")
                    with Vertical(id="signals-screener-pane", classes="full-pane"):
                        yield Static("ACTIVE STOCKS", classes="panel-title", id="screener-list-title")
                        yield DataTable(id="dt-screener", zebra_stripes=True, cursor_type="row")
                with Horizontal(id="signals-broker"):
                    with Vertical(id="broker-floor-left"):
                        with Horizontal(id="broker-floor-header"):
                            yield Static("BROKER FLOOR SIGNALS", classes="panel-title", id="broker-floor-title")
                            yield Button("ALL",      id="bf-filter-all",   classes="bf-filter-btn bf-active")
                            yield Button("CIRCULAR", id="bf-filter-circ",  classes="bf-filter-btn")
                            yield Button("PUMP",     id="bf-filter-pump",  classes="bf-filter-btn")
                            yield Button("SMART $",  id="bf-filter-smart", classes="bf-filter-btn")
                        yield DataTable(id="dt-broker-floor", zebra_stripes=True, cursor_type="row")
                    with Vertical(id="broker-floor-right"):
                        yield Static("TOP BROKERS", classes="panel-title", id="broker-top-title")
                        yield DataTable(id="dt-broker-top", zebra_stripes=True, cursor_type="row")
                with Horizontal(id="signals-bottom"):
                    with Vertical(id="signals-calendar", classes="full-pane"):
                        yield Static("CORPORATE ACTIONS — Next 30 Days", classes="panel-title")
                        yield DataTable(id="dt-calendar", zebra_stripes=True, cursor_type="row")
                    with Vertical(id="screener-heatmap-pane", classes="full-pane"):
                        yield Static("SECTOR PERFORMANCE", classes="panel-title")
                        yield Static("", id="heatmap-content")

            # ── 4 LOOKUP ──

            with Vertical(id="lookup", classes="tab-pane"):
                yield Static("Press L to look up any stock", classes="panel-title", id="lookup-title")
                yield Static("", id="lookup-header")
                yield Static("", id="lookup-chart")
                with Horizontal(id="lookup-split"):
                    with Vertical(id="lookup-table-pane"):
                        yield Static("OHLCV", classes="panel-title", id="lookup-ohlcv-title")
                        yield DataTable(id="dt-lookup", zebra_stripes=True, cursor_type="row")
                    with VerticalScroll(id="lookup-summary-pane"):
                        yield Static("", id="lookup-stats")
                        yield Static("", id="lookup-report")
                    with VerticalScroll(id="lookup-side-pane"):
                        yield Static("", id="lookup-fin-title")
                        yield DataTable(id="dt-lookup-fin", zebra_stripes=True, cursor_type="row")
                        yield Static("", id="lookup-ca-title")
                        yield DataTable(id="dt-lookup-ca", zebra_stripes=True, cursor_type="row")
                        yield Static("", id="lookup-intel-title")
                        yield Static("", id="lookup-intel")

            # ── 5 AGENTS ──
            with Vertical(id="agents", classes="tab-pane"):
                yield Static("", id="agent-status-bar")
                yield Static("", id="agent-market-view")
                with Horizontal(id="agent-split"):
                    with Vertical(id="agent-left", classes="full-pane"):
                        yield Static("TOP 10 AGENT PICKS", classes="panel-title")
                        yield Static("", id="agent-picks-subtitle")
                        yield DataTable(id="dt-agent-verdicts", zebra_stripes=True, cursor_type="row")
                        with Vertical(id="agent-left-bottom"):
                            yield Static("FOCUS", classes="panel-title", id="agent-detail-title")
                            with VerticalScroll(id="agent-detail-pane"):
                                yield Static("", id="agent-detail")
                    with Vertical(id="agent-right", classes="full-pane"):
                        yield Static("CHAT", classes="panel-title")
                        yield Static("", id="agent-chat-subtitle")
                        yield VerticalScroll(id="agent-chat-scroll")
                        with Vertical(id="agent-right-bottom"):
                            yield Static("COMPOSER", classes="panel-title", id="agent-compose-title")
                            with Vertical(id="agent-chat-footer"):
                                yield Static("", id="agent-chat-hint")
                                yield Input(id="agent-input", placeholder="Ask the agent...  (/agent list, /model gemma4:e2b, /stop)")

            # ── 6 ORDERS (Order Management) ──
            with Vertical(id="orders", classes="tab-pane"):
                yield Static("", id="order-status-bar")
                with Horizontal(id="order-form-bar"):
                    yield Static("SIDE", classes="order-chip order-chip-neutral")
                    yield Button("BUY", id="order-btn-buy")
                    yield Button("SELL", id="order-btn-sell")
                    yield Static("•", classes="order-sep")
                    yield Static("TICKET", classes="order-chip order-chip-neutral")
                    yield Static("SYM", classes="order-field-label")
                    yield Input(id="order-inp-symbol", placeholder="NABIL")
                    yield Static("QTY", classes="order-field-label")
                    yield Input(id="order-inp-qty", placeholder="10")
                    yield Static("LIMIT", classes="order-field-label")
                    yield Input(id="order-inp-price", placeholder="LTP")
                    yield Static("SLIP%", classes="order-field-label")
                    yield Input(id="order-inp-slippage", placeholder="2.0")
                    yield Static("•", classes="order-sep")
                    yield Static("EXEC", classes="order-chip order-chip-neutral")
                    yield Button("SUBMIT", id="order-btn-submit")
                    yield Button("CANCEL", id="order-btn-cancel-all")
                with Horizontal(id="order-books-split"):
                    with Vertical(id="order-daily-pane", classes="full-pane"):
                        yield Static("DAILY ORDER BOOK", classes="panel-title", id="order-daily-title")
                        yield DataTable(id="dt-orders-daily", zebra_stripes=True, cursor_type="row")
                    with Vertical(id="order-historic-pane", classes="full-pane"):
                        yield Static("HISTORIC ORDER BOOK", classes="panel-title", id="order-historic-title")
                        yield DataTable(id="dt-orders-historic", zebra_stripes=True, cursor_type="row")
                with Vertical(id="order-trades-pane", classes="full-pane"):
                    yield Static("TODAY'S TRADES (FILLED)", classes="panel-title", id="order-trades-title")
                    yield DataTable(id="dt-orders-trades", zebra_stripes=True, cursor_type="row")

            # ── 7 WATCHLIST ──
            with Vertical(id="watchlist", classes="tab-pane"):
                yield Static("", id="wl-status-bar")
                with Horizontal(id="wl-split"):
                    with Vertical(id="wl-main", classes="full-pane"):
                        yield Static("STOCK WATCHLIST", classes="panel-title", id="wl-main-title")
                        yield DataTable(id="dt-watchlist", zebra_stripes=True, cursor_type="row")
                    with Vertical(id="wl-side", classes="full-pane"):
                        with Vertical(id="wl-rates-pane", classes="full-pane"):
                            yield Static("FOREX & MACRO", classes="panel-title", id="wl-rates-title")
                            yield DataTable(id="dt-watchlist-rates", zebra_stripes=True, cursor_type="row")
                        with Vertical(id="wl-commodities-pane", classes="full-pane"):
                            yield Static("COMMODITIES", classes="panel-title", id="wl-commodities-title")
                            yield DataTable(id="dt-watchlist-commodities", zebra_stripes=True, cursor_type="row")

            # ── 8 RATES & COMMODITIES ───────────────────────────────────────
            with Vertical(id="kalimati", classes="tab-pane"):
                yield Static("", id="kalimati-status-bar")
                with Horizontal(id="kalimati-search-bar"):
                    yield Static("SEARCH", id="kalimati-search-label")
                    yield Input(
                        id="kalimati-search-input",
                        placeholder="Search commodities, gold, silver, crude, petrol, forex..."
                    )
                    yield Button("CLEAR", id="kalimati-search-clear")
                with Horizontal(id="kalimati-split"):
                    with Vertical(id="kalimati-left-pane", classes="full-pane"):
                        yield Static("KALIMATI COMMODITIES", classes="panel-title", id="kalimati-left-title")
                        yield Static("", id="kalimati-movers-bar")
                        with Vertical(id="kalimati-main", classes="full-pane"):
                            yield DataTable(id="dt-kalimati", zebra_stripes=True, cursor_type="row")
                    with Vertical(id="kalimati-right-pane", classes="full-pane"):
                        yield Static("GLOBAL RATES & PRICES", classes="panel-title", id="kalimati-right-title")
                        with Vertical(id="kalimati-macro-pane", classes="full-pane"):
                            yield Static("METALS, ENERGY & NOC", classes="panel-title", id="macro-top-title")
                            yield DataTable(id="dt-macro", zebra_stripes=True, cursor_type="row")
                        with Vertical(id="kalimati-forex-pane", classes="full-pane"):
                            yield Static("FOREX RATES", classes="panel-title", id="macro-forex-title")
                            yield DataTable(id="dt-forex", zebra_stripes=True, cursor_type="row")

            with Vertical(id="account", classes="tab-pane"):
                with Horizontal(id="account-split"):
                  with Vertical(id="account-left", classes="full-pane"):
                    with Vertical(id="account-main", classes="full-pane"):
                      with Vertical(id="profile-pane"):
                        yield Static("PAPER ACCOUNT", classes="panel-title")
                        yield Static("", id="profile-summary")
                        yield Static("ACCOUNTS", classes="panel-title")
                        yield DataTable(id="dt-account-list", zebra_stripes=True, cursor_type="row")
                        yield Static("ACCOUNT SETUP", classes="panel-title")
                        yield Static("", id="profile-shortcuts")
                        with Horizontal(id="profile-primary-row"):
                            yield Static("NAME", classes="profile-inline-label")
                            yield Input(id="profile-inp-account-name", placeholder="Account 2", classes="profile-inline-input profile-name-input")
                            yield Static("NAV", classes="profile-inline-label")
                            yield Input(id="profile-inp-target-nav", placeholder="1000000", classes="profile-inline-input profile-nav-input")
                            with Horizontal(id="profile-primary-actions"):
                                yield Button("N NEW", id="profile-btn-create-account", classes="profile-btn profile-btn-primary")
                                yield Button("A ACTIVATE", id="profile-btn-activate-account", classes="profile-btn")
                        with Horizontal(id="profile-seed-row"):
                            yield Static("SEED", classes="profile-inline-label")
                            yield Input(id="profile-inp-portfolio", placeholder="paper_portfolio.csv or MeroShare CSV  —  or press B BROWSE")
                            yield Button("B BROWSE", id="profile-btn-browse-seed", classes="profile-btn profile-btn-browse")
                        with Horizontal(id="profile-actions"):
                            yield Button("W WATCHLIST", id="profile-btn-sync-watchlist", classes="profile-btn profile-action-button")
                            yield Button("V SET NAV", id="profile-btn-set-nav", classes="profile-btn profile-action-button")
                            yield Button("S SNAPSHOT", id="profile-btn-save-account", classes="profile-btn profile-action-button")
                            yield Button("DEL ACCOUNT", id="profile-btn-delete-account", classes="profile-btn profile-action-button profile-btn-delete-account")
                        yield Static("", id="account-help")
                        yield Static(
                            "Create a blank account with target NAV, or press B BROWSE to pick a SEED file. "
                            "Accepts paper_portfolio.csv or MeroShare 'My Shares Values.csv' (auto-detected). "
                            "Selecting and activating an account swaps the full paper runtime.",
                            id="profile-note",
                        )
                  with Vertical(id="account-right", classes="full-pane"):
                      with Vertical(id="port-trades", classes="full-pane"):
                          yield Static("TRADE HISTORY", classes="panel-title", id="trades-title")
                          yield DataTable(id="dt-trades-full", zebra_stripes=True, cursor_type="row")
                      with Vertical(id="port-activity", classes="full-pane"):
                          yield Static("ENGINE LOG", classes="panel-title", id="activity-title")
                          yield VerticalScroll(id="activity-scroll")

            with Vertical(id="strategies", classes="tab-pane"):
                with Vertical(id="strategies-main", classes="full-pane"):
                    yield Static("STRATEGY REGISTRY", classes="panel-title")
                    yield Static("", id="strategy-summary")
                    with Horizontal(id="strategies-split"):
                        with Vertical(id="strategies-left", classes="full-pane"):
                            yield Static("SAVED STRATEGIES", classes="panel-title")
                            yield DataTable(id="dt-strategy-list", zebra_stripes=True, cursor_type="row")
                            yield Static("", id="strategy-accounts-note")
                        with Vertical(id="strategies-right", classes="full-pane"):
                            yield Static("STRATEGY BUILDER", classes="panel-title")
                            with Horizontal(id="strategy-name-row", classes="strategy-row"):
                                yield Static("NAME", classes="profile-inline-label")
                                yield Input(id="strategy-inp-name", placeholder="My Strategy", classes="profile-inline-input profile-name-input")
                                yield Static("DESC", classes="profile-inline-label")
                                yield Input(id="strategy-inp-description", placeholder="Notes", classes="profile-inline-input")
                            yield Static("SIGNALS", classes="panel-title", id="signal-picker-label")
                            with Vertical(id="signal-picker-area"):
                                with Horizontal(classes="signal-picker-row"):
                                    for _lbl, _sig in _SIGNAL_DEFS[:5]:
                                        yield Button(_lbl, id=_sig_btn_id(_sig), classes="signal-btn")
                                with Horizontal(classes="signal-picker-row"):
                                    for _lbl, _sig in _SIGNAL_DEFS[5:10]:
                                        yield Button(_lbl, id=_sig_btn_id(_sig), classes="signal-btn")
                                with Horizontal(classes="signal-picker-row"):
                                    for _lbl, _sig in _SIGNAL_DEFS[10:]:
                                        yield Button(_lbl, id=_sig_btn_id(_sig), classes="signal-btn")
                                yield Static("", id="signal-picker-active", classes="signal-active-display")
                            yield Static("PARAMETERS", classes="panel-title", id="strategy-params-label")
                            with Horizontal(id="strategy-config-row-a", classes="strategy-row"):
                                yield Static("HOLD", classes="profile-inline-label")
                                yield Input(id="strategy-inp-holding-days", placeholder="40", classes="profile-inline-input strategy-small-input")
                                yield Static("REBAL", classes="profile-inline-label")
                                yield Input(id="strategy-inp-rebalance", placeholder="5", classes="profile-inline-input strategy-small-input")
                                yield Static("MAX POS", classes="profile-inline-label")
                                yield Input(id="strategy-inp-max-positions", placeholder="5", classes="profile-inline-input strategy-small-input")
                            with Horizontal(id="strategy-config-row-b", classes="strategy-row"):
                                yield Static("STOP", classes="profile-inline-label")
                                yield Input(id="strategy-inp-stop-loss", placeholder="0.08", classes="profile-inline-input strategy-small-input")
                                yield Static("TRAIL", classes="profile-inline-label")
                                yield Input(id="strategy-inp-trailing-stop", placeholder="0.10", classes="profile-inline-input strategy-small-input")
                                yield Static("SECTOR", classes="profile-inline-label")
                                yield Input(id="strategy-inp-sector-limit", placeholder="0.35", classes="profile-inline-input strategy-small-input")
                            with Horizontal(id="strategy-actions-row", classes="strategy-row"):
                                yield Button("NEW", id="strategy-btn-new", classes="profile-btn")
                                yield Button("SAVE", id="strategy-btn-save", classes="profile-btn profile-btn-primary")
                                yield Button("→ ACTIVE ACCT", id="strategy-btn-assign-current", classes="profile-btn")
                                yield Button("→ SELECTED ACCT", id="strategy-btn-assign-selected", classes="profile-btn")
                                yield Button("DELETE", id="strategy-btn-delete", classes="profile-btn strategy-btn-delete")
                            yield Static("BACKTEST", classes="panel-title", id="strategy-backtest-label")
                            with Horizontal(id="strategy-backtest-row", classes="strategy-row"):
                                yield Static("START", classes="profile-inline-label")
                                yield Input(id="strategy-inp-backtest-start", placeholder="2025-01-01", classes="profile-inline-input strategy-date-input")
                                yield Static("END", classes="profile-inline-label")
                                yield Input(id="strategy-inp-backtest-end", placeholder="2026-04-11", classes="profile-inline-input strategy-date-input")
                                yield Static("CAP", classes="profile-inline-label")
                                yield Input(id="strategy-inp-backtest-capital", placeholder="1000000", classes="profile-inline-input strategy-capital-input")
                                yield Button("RUN", id="strategy-btn-backtest", classes="profile-btn profile-action-button")
                                yield Button("CHART", id="strategy-btn-chart", classes="profile-btn strategy-btn-chart")
                            yield Static("", id="strategy-backtest-summary")

        yield Static(id="status-bar")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.push_screen(ModeSelectScreen(), callback=self._on_mode_selected)

    def _on_mode_selected(self, mode: str | None) -> None:
        if not mode:
            self.exit()
            return
        self.trade_mode = "paper"
        self._init_dashboard()

    def _on_tms_credentials(self, result: dict | None) -> None:
        if not result:
            # User pressed ESC — fall back to mode select
            self.push_screen(ModeSelectScreen(), callback=self._on_mode_selected)
            return
        self._tms_credentials = result
        self._init_dashboard()
        self._init_tms()

    def _init_dashboard(self) -> None:
        _ensure_paper_runtime_files()
        self._paper_accounts, self._current_account_id = _bootstrap_paper_accounts()
        self._selected_account_id = self._current_account_id
        self._load_strategies_registry()
        self._apply_active_strategy_runtime()
        self._sync_agent_account_context_env()
        try:
            from backend.quant_pro.database import init_db
            init_db()
        except Exception:
            pass
        # Pull live quotes into DB before loading market data
        try:
            from backend.quant_pro.realtime_market import get_market_data_provider
            snap = get_market_data_provider().fetch_snapshot(force=True)
            if snap and snap.quotes:
                self._upsert_live_prices(snap)
        except Exception:
            pass
        self.md = MD(top_n=25)
        self._regime = "loading..."
        self._stats: dict = {}
        self._lookup_cache: dict[tuple[str, str, str], dict] = {}
        self._lookup_request_key: Optional[tuple[str, str, str]] = None
        self._signals_table_cache_key: str = ""
        self._signals_table_cache_payload: Optional[tuple[list[tuple[str, str]], list[list[Text]], int]] = None
        self._signals_workspace_cache_key: str = ""
        self._signals_workspace_cache_payload: Optional[dict] = None
        self._signals_last_loaded_at: str = ""
        self._signals_last_strategy_name: str = ""
        self._signals_last_count: int = 0
        self._signal_min_score: float = 0.0
        self._broker_floor_filter: str = "all"  # all | circ | pump | smart
        self._ticker_text = ""
        self._ticker_offset = 0
        self._rates_search_query: str = ""
        self._kalimati_rows: list[dict] = []
        self._kalimati_status: str = "Loading..."
        self._macro_rates: dict = {}
        self._hedge_enabled: bool = True    # ON/OFF toggle for gold/silver hedge overlay
        self._hedge_trade_log: list = _load_hedge_trade_log()
        self._active_signals: set[str] = set()  # signal picker state
        self._new_strategy_mode: bool = False   # True after NEW pressed, False after list selection or SAVE
        self._watchlist_stock_rows: list[dict] = []
        self._watchlist_rates_rows: list[dict] = []
        self._watchlist_commodity_rows: list[dict] = []
        self._last_macro_rates_fetch_at: float = 0.0
        self._build_ticker()
        self._update_header()
        self._update_index()
        self._load_profile_inputs()
        self._populate_strategies_tab()
        self._populate_market()
        self._populate_portfolio_and_risk()
        self._populate_trades_full()
        self._render_hedge_panel()
        self._osint_stories: list[dict] = []
        self._agent_analysis: dict = {}
        self._agent_history: list[dict] = []
        self._agent_archived_history: list[dict] = []
        self._agent_archive_count: int = 0
        self._agent_show_archived = False
        self._agent_hidden_recent_history: list[dict] = []
        self._agent_preview_override: Optional[dict] = None
        self._last_agent_auto_order_key: Optional[str] = None
        self._agent_typing_visible = False
        self._agent_typing_frame = 0
        self._agent_session_started_at = time.time()
        self._agent_visible_since = self._agent_session_started_at
        self._agent_chat_loaded = False
        self._load_agent_runtime_state()
        self._populate_agent_tab()
        if not list((self._agent_analysis or {}).get("stocks") or []):
            self._run_agent_analysis(force=False)
        self._load_paper_orders()
        self._populate_orders_tab()
        self._refresh_order_action_buttons()
        self._paper_watchlist = _load_watchlist()
        self._watchlist = list(self._paper_watchlist)
        self._populate_watchlist()
        self._populate_signals_workspace()
        self._load_signals_async()
        self._load_regime_async()
        self._load_osint_async()
        init_kalimati_db()
        self._load_kalimati_async()
        self._load_macro_rates_async(force=True)
        mode_label = self._display_mode_label()
        self._set_status(
            f"Mode: {mode_label}  │  Session: {self.md.latest}  │  "
            f"▲{self.md.adv} ▼{self.md.dec}  │  Auto-refresh 30s"
        )
        self.set_interval(30, self._auto_refresh)
        self.set_interval(TICKER_SPEED, self._scroll_ticker)
        self.set_interval(0.45, self._animate_agent_typing)

        # Start one auto-trading engine per account in background threads
        if self.trade_mode == "paper":
            self._start_all_account_engines()

    # ── News ticker ──────────────────────────────────────────────────────────



    # ── Tab switching ─────────────────────────────────────────────────────────

    def action_tab(self, name: str) -> None:
        if name == "screener":
            name = "signals"
        if name == "news":
            name = "agents"
        self.active_tab = name
        self.query_one("#content", ContentSwitcher).current = name
        self._update_header()
        self._refresh_active_tab_view(force_watchlist_sync=True)

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_lookup(self) -> None:
        self.push_screen(LookupScreen(), callback=self._on_lookup)

    def action_run_agent(self) -> None:
        if self.active_tab == "account":
            try:
                self._set_status(self._account_activate_selected())
            except Exception as exc:
                self._set_status(f"Account activate failed: {exc}")
            return
        self.action_tab("agents")
        self._run_agent_analysis(force=True)

    def action_tf(self, tf: str) -> None:
        """Switch lookup chart timeframe: D/W/M/I."""
        if self.active_tab == "account":
            if tf == "W":
                try:
                    self._set_status(self._account_sync_watchlist())
                except Exception as exc:
                    self._set_status(f"Watchlist sync failed: {exc}")
            return
        if self.active_tab != "lookup":
            return
        self.lookup_tf = tf
        self._populate_lookup()
        tf_names = {"D": "Daily", "W": "Weekly", "M": "Monthly", "Y": "Yearly", "I": "Intraday"}
        self._set_status(f"Chart: {tf_names.get(tf, tf)}  │  D=Daily  W=Weekly  M=Monthly  Y=Yearly  I=Intraday")

    def action_refresh(self) -> None:
        if self.active_tab == "signals":
            self.action_force_signals_reload()
            self._do_refresh()
            return
        if self.active_tab == "watchlist" and self.trade_mode == "live" and self.tms_service:
            self._refresh_watchlist_live(force=True)
        self._do_refresh()

    def action_force_signals_reload(self) -> None:
        if self.active_tab != "signals":
            self._set_status("Force signals reload is available on the Signals tab")
            return
        self._reload_account_bindings_from_disk()
        self._signals_table_cache_key = ""
        self._signals_table_cache_payload = None
        self._set_signals_table_loading()
        self._load_signals_async(force=True)

    def _on_lookup(self, result: dict | None) -> None:
        if not result: return
        self.lookup_sym = result["symbol"]
        self.action_tab("lookup")

    # ── Order Management ─────────────────────────────────────────────────────

    def on_unmount(self) -> None:
        """Graceful shutdown — stop trading engine."""
        try:
            if self.trade_mode == "paper":
                self._persist_active_account_snapshot()
        except Exception:
            pass
        for _eng in list(getattr(self, "_account_engines", {}).values()):
            try:
                _eng.stop()
            except Exception:
                pass
        try:
            src = getattr(self, "_tms_live_src", None)
            if src is not None:
                src.stop()
        except Exception:
            pass


    # ── Auto-refresh ──────────────────────────────────────────────────────────







    # ── Signal generation ─────────────────────────────────────────────────────






    # ── Rates & Commodities ──────────────────────────────────────────────────





    # ── OSINT news feed ───────────────────────────────────────────────────────




    # ── Agent tab ─────────────────────────────────────────────────────────────

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection across tabs."""
        # Watchlist: Enter → lookup
        if event.data_table.id == "dt-watchlist":
            idx = event.cursor_row
            stock_rows = getattr(self, "_watchlist_stock_rows", [])
            if 0 <= idx < len(stock_rows):
                entry = stock_rows[idx]
                if str(entry.get("kind") or "stock") == "stock":
                    self.lookup_sym = str(entry.get("symbol") or entry.get("label") or "").upper()
                    if self.lookup_sym:
                        self.action_tab("lookup")
                else:
                    self._rates_search_query = str(entry.get("label") or "").strip()
                    self.action_tab("kalimati")
                    try:
                        self.query_one("#kalimati-search-input", Input).value = self._rates_search_query
                    except Exception:
                        pass
            return
        if event.data_table.id == "dt-watchlist-rates":
            idx = event.cursor_row
            if 0 <= idx < len(getattr(self, "_watchlist_rates_rows", [])):
                item = self._watchlist_rates_rows[idx]
                self._rates_search_query = str(item.get("label") or "").strip()
                self.action_tab("kalimati")
                try:
                    self.query_one("#kalimati-search-input", Input).value = self._rates_search_query
                except Exception:
                    pass
            return
        if event.data_table.id == "dt-watchlist-commodities":
            idx = event.cursor_row
            if 0 <= idx < len(getattr(self, "_watchlist_commodity_rows", [])):
                item = self._watchlist_commodity_rows[idx]
                self._rates_search_query = str(item.get("label") or "").strip()
                self.action_tab("kalimati")
                try:
                    self.query_one("#kalimati-search-input", Input).value = self._rates_search_query
                except Exception:
                    pass
            return
        if event.data_table.id == "dt-account-list":
            idx = event.cursor_row
            accounts = list(getattr(self, "_paper_accounts", []) or [])
            if 0 <= idx < len(accounts):
                selected = accounts[idx]
                self._selected_account_id = str(selected.get("id") or self._current_account_id)
                try:
                    self.query_one("#profile-inp-account-name", Input).value = str(selected.get("name") or "")
                except Exception:
                    pass
                try:
                    snap = self._account_snapshot(selected)
                    self.query_one("#profile-inp-target-nav", Input).value = f"{float(snap['nav']):,.2f}".replace(",", "")
                except Exception:
                    pass
                self._populate_account_list()
                self._populate_strategies_tab()
                selected_name = str(selected.get("name") or self._selected_account_id)
                self._set_status(f"Selected {selected_name} | press ACTIVATE to switch")
            return
        if event.data_table.id == "dt-strategy-list":
            idx = event.cursor_row
            strategies = list(getattr(self, "_strategies", []) or [])
            if 0 <= idx < len(strategies):
                selected = strategies[idx]
                self._selected_strategy_id = str(selected.get("id") or self._selected_strategy_id)
                self._set_strategy_form_from_payload(selected)
                self._populate_strategies_tab()
                self._set_status(f"Selected strategy {selected.get('name')}")
            return
        # dt-news replaced by OptionList — handled in on_option_list_option_highlighted

        # Screener: Enter → lookup
        if event.data_table.id == "dt-screener":
            try:
                row_data = event.data_table.get_row_at(event.cursor_row)
                sym = str(row_data[0]).strip()
                if sym and sym != "—":
                    self.lookup_sym = sym
                    self.action_tab("lookup")
            except Exception:
                pass
            return
        # Agent verdicts
        if event.data_table.id != "dt-agent-verdicts":
            return
        a = getattr(self, '_agent_analysis', {})
        stocks = a.get("stocks", [])
        idx = event.cursor_row
        self._show_agent_focus_row(idx)
        if 0 <= idx < len(stocks):
            symbol = str(stocks[idx].get("symbol") or "").strip().upper()
            if symbol:
                self.lookup_sym = symbol

        # Market view
        mv = self.query_one("#agent-market-view", Static)
        market_view = a.get("market_view", "")
        trade_reason = a.get("trade_today_reason", "")
        risks = a.get("risks", [])
        parts = []
        if market_view:
            parts.append(f"[bold #bb88ff]VIEW:[/] [{WHITE}]{market_view}[/]")
        if trade_reason:
            parts.append(f"[bold #bb88ff]TRADE:[/] [{YELLOW}]{trade_reason}[/]")
        if risks:
            parts.append(f"[bold {LOSS_HI}]RISKS:[/] [{LABEL}]{' | '.join(risks[:3])}[/]")
        if parts:
            mv.update(Text.from_markup("   ".join(parts)))
        else:
            mv.update(Text("Press A to run agent analysis, or type a question below", style=LABEL))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "dt-strategy-list":
            idx = event.cursor_row
            strategies = list(getattr(self, "_strategies", []) or [])
            if 0 <= idx < len(strategies):
                selected = strategies[idx]
                self._selected_strategy_id = str(selected.get("id") or self._selected_strategy_id)
                # Selecting from the list exits new-strategy mode
                self._new_strategy_mode = False
                self._set_strategy_form_from_payload(selected)
            return
        if event.data_table.id != "dt-agent-verdicts":
            return
        self._show_agent_focus_row(event.cursor_row)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        bid = event.button.id
        if bid == "hedge-toggle-btn":
            self._hedge_enabled = not self._hedge_enabled
            try:
                btn = self.query_one("#hedge-toggle-btn", Button)
                if self._hedge_enabled:
                    btn.label = "● HEDGE ON"
                    btn.remove_class("hedge-btn-off")
                    btn.add_class("hedge-btn-on")
                else:
                    btn.label = "○ HEDGE OFF"
                    btn.remove_class("hedge-btn-on")
                    btn.add_class("hedge-btn-off")
            except Exception:
                pass
            # Propagate to all running engines
            for _eng in list(getattr(self, "_account_engines", {}).values()):
                try:
                    _eng.set_hedge_enabled(self._hedge_enabled)
                except Exception:
                    pass
            self._render_hedge_panel()
            return
        if bid in ("bf-filter-all", "bf-filter-circ", "bf-filter-pump", "bf-filter-smart"):
            self._broker_floor_filter = {"bf-filter-all": "all", "bf-filter-circ": "circ",
                                         "bf-filter-pump": "pump", "bf-filter-smart": "smart"}[bid]
            for btn_id in ("bf-filter-all", "bf-filter-circ", "bf-filter-pump", "bf-filter-smart"):
                try:
                    btn = self.query_one(f"#{btn_id}", Button)
                    if btn_id == bid:
                        btn.add_class("bf-active")
                    else:
                        btn.remove_class("bf-active")
                except Exception:
                    pass
            self._apply_broker_floor_filter()
            return
        if bid == "news-search-clear":
            self._news_search_query = ""
            self._vector_search_results = []
            try:
                self.query_one("#news-search-input", Input).value = ""
            except Exception:
                pass
            self._populate_news()
            return
        if bid == "kalimati-search-clear":
            self._rates_search_query = ""
            try:
                self.query_one("#kalimati-search-input", Input).value = ""
            except Exception:
                pass
            self._populate_kalimati()
            return
        if bid == "order-btn-buy":
            self._order_action = "BUY"
            self._refresh_order_action_buttons()
            self._set_status("Order action: BUY")
        elif bid == "order-btn-sell":
            self._order_action = "SELL"
            self._refresh_order_action_buttons()
            self._set_status("Order action: SELL")
        elif bid == "order-btn-submit":
            self._on_order_submit()
        elif bid == "order-btn-cancel-all":
            if self.trade_mode == "paper":
                msg = self._cancel_all_paper_orders()
                self._set_status(msg)
            else:
                self._set_status("Cancel all is only available for paper orders")
        elif bid == "profile-btn-browse-seed":
            self._browse_seed_file()
        elif bid == "profile-btn-create-account":
            try:
                self._set_status(self._account_create_from_form())
            except Exception as exc:
                self._set_status(f"Account create failed: {exc}")
        elif bid == "profile-btn-activate-account":
            try:
                self._set_status(self._account_activate_selected())
            except Exception as exc:
                self._set_status(f"Account activate failed: {exc}")
        elif bid == "profile-btn-delete-account":
            try:
                self._set_status(self._delete_paper_account(self._selected_account_id))
            except Exception as exc:
                self._set_status(f"Account delete failed: {exc}")
        elif bid == "profile-btn-set-nav":
            try:
                self._set_status(self._account_set_nav_from_form())
            except Exception as exc:
                self._set_status(f"NAV update failed: {exc}")
        elif bid == "profile-btn-sync-watchlist":
            try:
                self._set_status(self._account_sync_watchlist())
            except Exception as exc:
                self._set_status(f"Watchlist sync failed: {exc}")
        elif bid == "profile-btn-save-account":
            try:
                self._set_status(self._save_current_account_snapshot())
            except Exception as exc:
                self._set_status(f"Snapshot save failed: {exc}")
        elif bid.startswith(_SIG_ID_PREFIX):
            sig_type = _sig_type_from_id(bid)
            active = getattr(self, "_active_signals", set())
            if sig_type in active:
                active.discard(sig_type)
            else:
                active.add(sig_type)
            self._active_signals = active
            self._sync_signal_buttons()
            return
        elif bid == "strategy-btn-new":
            self._new_strategy_mode = True
            self._active_signals = set()
            self._sync_signal_buttons()
            # Pre-fill sensible defaults — user only needs to enter name, description, and signals
            _new_defaults = {
                "strategy-inp-name": "",
                "strategy-inp-description": "",
                "strategy-inp-holding-days": "40",
                "strategy-inp-rebalance": "5",
                "strategy-inp-max-positions": "5",
                "strategy-inp-stop-loss": "0.08",
                "strategy-inp-trailing-stop": "0.15",
                "strategy-inp-sector-limit": "0.35",
            }
            for _wid, _val in _new_defaults.items():
                try:
                    self.query_one(f"#{_wid}", Input).value = _val
                except Exception:
                    pass
            try:
                self.query_one("#strategy-inp-name", Input).focus()
            except Exception:
                pass
            self._set_status("New strategy — enter name, pick signals, then SAVE  (parameters pre-filled with defaults)")
            return
        elif bid == "strategy-btn-save":
            try:
                self._set_status(self._save_strategy_from_form())
            except Exception as exc:
                self._set_status(f"Strategy save failed: {exc}")
        elif bid == "strategy-btn-delete":
            try:
                self._set_status(self._delete_custom_strategy())
            except Exception as exc:
                self._set_status(f"Strategy delete failed: {exc}")
        elif bid == "strategy-btn-assign-current":
            try:
                self._set_status(self._assign_strategy_to_account(self._selected_strategy_id, self._current_account_id))
            except Exception as exc:
                self._set_status(f"Strategy assign failed: {exc}")
        elif bid == "strategy-btn-assign-selected":
            try:
                self._set_status(self._assign_strategy_to_account(self._selected_strategy_id, self._selected_account_id))
            except Exception as exc:
                self._set_status(f"Strategy assign failed: {exc}")
        elif bid == "strategy-btn-backtest":
            try:
                self._set_status(self._start_strategy_backtest())
            except Exception as exc:
                self._set_status(f"Strategy backtest failed: {exc}")
        elif bid == "strategy-btn-chart":
            selected = self._selected_strategy_payload()
            selected_id = str((selected or {}).get("id") or getattr(self, "_selected_strategy_id", "") or "").strip()
            result = self._selected_strategy_chart_result(selected_id)
            if not result:
                self._set_status("No chartable saved backtest artifact for this strategy")
            else:
                try:
                    from validation.quick_chart import generate_quick_chart
                    from backend.quant_pro.database import get_db_path as _get_db_path
                    name   = str((selected or {}).get("name") or (result.get("strategy") or {}).get("name") or "Strategy")
                    window = dict(result.get("window") or {})
                    start  = str(window.get("start") or self.query_one("#strategy-inp-backtest-start", Input).value or "").strip()
                    end    = str(window.get("end") or self.query_one("#strategy-inp-backtest-end", Input).value or "").strip()
                    path   = generate_quick_chart(
                        result,
                        strategy_name=name,
                        start_date=start or "2025-01-01",
                        end_date=end or "2026-04-11",
                        db_path=str(_get_db_path()),
                        auto_open=True,
                    )
                    self._set_status(f"Chart saved → {path}" if path else "Chart generation failed")
                except Exception as exc:
                    self._set_status(f"Chart failed: {exc}")
        elif bid == "order-btn-cancel":
            # Cancel selected order from daily order book
            try:
                dt = self.query_one("#dt-orders-daily", DataTable)
                row_key = dt.cursor_row
                if row_key is not None:
                    row_data = dt.get_row_at(row_key)
                    order_id = str(row_data[0]).strip()
                    if order_id and order_id != "—":
                        msg = self._cancel_paper_order(order_id)
                        self._set_status(msg)
            except Exception:
                pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle agent chat input + order form + news search."""
        if event.input.id == "news-search-input":
            q = event.value.strip()
            self._news_search_query = q
            if q:
                self._do_vector_search(q)
            else:
                self._vector_search_results = []
                self._populate_news()
            return
        if event.input.id == "profile-inp-portfolio":
            self._persist_profile_paths(portfolio_path=event.value.strip())
            self._set_status("Portfolio seed path updated")
            return
        if event.input.id == "profile-inp-account-name":
            self._set_status("Account name updated")
            return
        if event.input.id == "profile-inp-target-nav":
            try:
                self._set_status(self._seed_manual_nav(event.value))
                self._populate_portfolio_and_risk()
            except Exception as exc:
                self._set_status(f"NAV update failed: {exc}")
            return
        if event.input.id in ("order-inp-price", "order-inp-slippage"):
            self._on_order_submit()
            return
        if event.input.id in ("order-inp-symbol", "order-inp-qty"):
            return
        if event.input.id != "agent-input":
            return
        question = event.value.strip()
        if not question:
            return
        event.input.value = ""
        if question.upper() == "A":
            self._run_agent_analysis()
        elif question.startswith("/"):
            if not self._handle_agent_chat_command(question):
                self._set_status(f"Unknown agent command: {question}")
        else:
            self._agent_ask_async(question)

    def on_input_changed(self, event: Input.Changed) -> None:
        input_id = event.input.id or ""
        if input_id == "kalimati-search-input":
            self._rates_search_query = event.value.strip()
            self._populate_kalimati()
            return
        if input_id == "signal-min-score":
            try:
                self._signal_min_score = float(event.value.strip() or "0")
            except ValueError:
                self._signal_min_score = 0.0
            # Invalidate cache so next load respects new threshold
            self._signals_table_cache_key = ""
            self._signals_table_cache_payload = None
            return

    def on_key(self, event: events.Key) -> None:
        if str(self.active_tab or "") != "account":
            return
        if isinstance(self.focused, Input):
            return
        key = str(event.key or "").lower()
        try:
            if key == "n":
                self._set_status(self._account_create_from_form())
                event.stop()
            elif key == "v":
                self._set_status(self._account_set_nav_from_form())
                event.stop()
            elif key == "h":
                self._set_status(self._toggle_account_help())
                event.stop()
        except Exception as exc:
            event.stop()
            if key == "n":
                self._set_status(f"Account create failed: {exc}")
            elif key == "v":
                self._set_status(f"NAV update failed: {exc}")
            elif key == "h":
                self._set_status(f"Help failed: {exc}")

    def on_option_list_option_highlighted(self, event) -> None:
        if getattr(getattr(event, "option_list", None), "id", "") != "news-list":
            return
        highlighted = getattr(event.option_list, "highlighted", None)
        if highlighted is None:
            return
        stories = getattr(self, "_news_visible_stories", [])
        if 0 <= highlighted < len(stories):
            self._news_selected_index = highlighted

    def on_option_list_option_selected(self, event) -> None:
        if getattr(getattr(event, "option_list", None), "id", "") != "news-list":
            return
        self.on_option_list_option_highlighted(event)

    # ── Header / Index / Status bars ──────────────────────────────────────────

    @staticmethod
    def _summarize_tms_watchlist_error(exc: Exception, action: str = "sync") -> str:
        detail = " ".join(str(exc).split())
        lowered = detail.lower()
        if "profile is already in use" in lowered or "processsingleton" in lowered or "singletonlock" in lowered:
            return f"TMS watchlist {action} paused: browser profile already in use"
        if "login required" in lowered:
            return f"TMS watchlist {action} paused: login required"
        if "member market watch page not ready" in lowered:
            return f"TMS watchlist {action} paused: member market watch not ready"
        if len(detail) > 140:
            detail = detail[:137] + "..."
        return f"TMS watchlist {action} failed: {detail}"

    # ── Market tab ────────────────────────────────────────────────────────────


    # ── Portfolio + Risk tabs ─────────────────────────────────────────────────




    def _reload_account_bindings_from_disk(self) -> None:
        registry = _load_accounts_registry()
        accounts = strategy_registry.ensure_account_strategy_ids(list(registry.get("accounts") or []))
        if not accounts:
            return
        self._paper_accounts = accounts
        known_ids = {str(account.get("id") or "") for account in accounts}
        profile = _load_profile_config()
        current_account_id = str(
            profile.get("current_account_id")
            or getattr(self, "_current_account_id", "account_1")
            or "account_1"
        ).strip()
        if current_account_id not in known_ids:
            current_account_id = str(accounts[0].get("id") or "account_1")
        self._current_account_id = current_account_id
        selected_account_id = str(getattr(self, "_selected_account_id", current_account_id) or current_account_id).strip()
        if selected_account_id not in known_ids:
            selected_account_id = current_account_id
        self._selected_account_id = selected_account_id
        binding = self._strategy_account_binding(current_account_id) or {}
        self._selected_strategy_id = str(
            binding.get("strategy_id")
            or getattr(self, "_selected_strategy_id", "")
            or strategy_registry.default_strategy_for_account(current_account_id)
        )
        self._sync_agent_account_context_env()









    @work(thread=True)
    def _browse_seed_file(self) -> None:
        """Open a native macOS file picker and populate the seed path input."""
        import subprocess as _sp
        self.call_from_thread(self._set_status, "Opening file picker…")
        try:
            script = (
                'POSIX path of (choose file with prompt '
                '"Select portfolio CSV (paper_portfolio.csv or MeroShare CSV)" '
                'of type {"csv", "CSV"})'
            )
            result = _sp.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=120,
            )
            chosen = result.stdout.strip()
            if not chosen:
                self.call_from_thread(self._set_status, "Browse cancelled")
                return
            # Populate the seed input and show a preview
            self.call_from_thread(
                lambda p=chosen: (
                    setattr(self.query_one("#profile-inp-portfolio", Input), "value", p)
                    or self._set_status(f"Seed file selected: {Path(p).name}")
                )
            )
        except FileNotFoundError:
            self.call_from_thread(
                self._set_status,
                "osascript not found — type the path manually into the SEED field",
            )
        except Exception as exc:
            self.call_from_thread(self._set_status, f"Browse failed: {exc}")




    # ── Signals workspace ─────────────────────────────────────────────────────






    # ── Calendar tab ──────────────────────────────────────────────────────────


    # ── Gold/Silver Hedge Panel ───────────────────────────────────────────────


    # ── Trades tab (full history) ─────────────────────────────────────────────


    # ── Lookup tab ────────────────────────────────────────────────────────────






    # ── Command Palette ────────────────────────────────────────────────────────

    def action_command_palette(self) -> None:
        self.push_screen(CommandPalette(), callback=self._on_command)

    def _on_command(self, result: dict | None) -> None:
        if not result:
            return
        kind = result.get("kind", "")
        target = result.get("target", "")
        if kind == "tab":
            self.action_tab(target)
        elif kind == "action":
            if target == "buy":
                self.action_buy()
            elif target == "sell":
                self.action_sell()
            elif target == "refresh":
                self.action_refresh()
            elif target == "agent":
                self.action_run_agent()
        elif kind == "stock":
            self.lookup_sym = target
            self.action_tab("lookup")

    # ── Watchlist ─────────────────────────────────────────────────────────────

    def action_watchlist_add(self) -> None:
        if self.active_tab == "kalimati":
            entry = self._watchlist_entry_from_rates_selection()
            if not entry:
                self._set_status("Select a commodity, macro price, or forex row to add")
                return
            self._add_local_watchlist_entry(entry)
            return
        self.push_screen(WatchlistAddScreen(), callback=self._on_watchlist_add)

    def _on_watchlist_add(self, item: dict | str | None) -> None:
        if not item:
            return
        if isinstance(item, dict):
            normalized = _normalize_watchlist_entry(item)
            if not normalized:
                self._set_status("Unable to add watchlist item")
                return
            if str(normalized.get("kind") or "stock") == "stock" and self.trade_mode == "live" and self.tms_service:
                sym = str(normalized.get("symbol") or normalized.get("label") or "").upper()
                if sym:
                    self._watchlist_add_live(sym)
                return
            self._add_local_watchlist_entry(normalized)
            return
        sym = str(item).upper()
        if self.trade_mode == "live" and self.tms_service:
            self._watchlist_add_live(sym)
            return
        self._add_local_watchlist_entry(_stock_watchlist_entry(sym))

    def action_watchlist_remove(self) -> None:
        """Remove the currently selected row from watchlist."""
        if self.active_tab != "watchlist":
            return
        try:
            focused = getattr(self, "focused", None)
            focused_id = getattr(focused, "id", "") if focused is not None else ""
            if focused_id == "dt-watchlist-rates":
                dt_rates = self.query_one("#dt-watchlist-rates", DataTable)
                idx = dt_rates.cursor_row
                rows = getattr(self, "_watchlist_rates_rows", [])
                if idx is not None and 0 <= idx < len(rows) and rows[idx].get("tracked"):
                    label = str(rows[idx].get("label") or "")
                    kind = str(rows[idx].get("kind") or "")
                    self._remove_local_watchlist_entry({"key": f"{kind}:{label}", "label": label})
                return
            if focused_id == "dt-watchlist-commodities":
                dt_commodities = self.query_one("#dt-watchlist-commodities", DataTable)
                idx = dt_commodities.cursor_row
                rows = getattr(self, "_watchlist_commodity_rows", [])
                if idx is not None and 0 <= idx < len(rows) and rows[idx].get("tracked"):
                    label = str(rows[idx].get("label") or "")
                    self._remove_local_watchlist_entry({"key": f"commodity:{label}", "label": label})
                return
            dt = self.query_one("#dt-watchlist", DataTable)
            row_idx = dt.cursor_row
            stock_rows = getattr(self, "_watchlist_stock_rows", [])
            if row_idx is not None and 0 <= row_idx < len(stock_rows):
                entry = stock_rows[row_idx]
                if str(entry.get("kind") or "stock") == "stock" and self.trade_mode == "live" and self.tms_service:
                    sym = str(entry.get("symbol") or entry.get("label") or "").upper()
                    if sym:
                        self._watchlist_remove_live(sym)
                    return
                self._remove_local_watchlist_entry(entry)
        except Exception:
            pass



    # ── Screener ──────────────────────────────────────────────────────────────



# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    NepseDashboard().run()


if __name__ == "__main__":
    main()
