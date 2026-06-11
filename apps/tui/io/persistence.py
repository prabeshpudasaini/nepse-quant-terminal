from __future__ import annotations

import json as _json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt
else:
    msvcrt = None

import pandas as pd

from apps.tui.io.csv_import import _positive_float
from apps.tui.io.watchlist_io import _dedupe_watchlist_entries, _stock_watchlist_entry
from apps.tui.render.cells import _npr_k
from backend.quant_pro.paths import (
    ensure_dir,
    get_project_root,
    get_runtime_dir,
    get_trading_runtime_dir,
    migrate_legacy_path,
)
from backend.trading import strategy_registry
from backend.trading.live_trader import (
    NAV_LOG_COLS,
    PORTFOLIO_COLS,
    TRADE_LOG_COLS,
    calculate_cash_from_trade_log,
    load_runtime_state,
    save_runtime_state,
)

INITIAL_CAPITAL = 1_000_000.0
PROJECT_ROOT = get_project_root(__file__)

RUNTIME_DIR = ensure_dir(get_runtime_dir(__file__))
TRADING_RUNTIME_DIR = ensure_dir(get_trading_runtime_dir(__file__))
HEDGE_TRADE_LOG_FILE = TRADING_RUNTIME_DIR / "hedge_trade_log.json"
WATCHLIST_FILE = migrate_legacy_path(RUNTIME_DIR / "watchlist.json", [PROJECT_ROOT / "watchlist.json"])
PAPER_NAV_LOG_FILE = migrate_legacy_path(TRADING_RUNTIME_DIR / "paper_nav_log.csv", [PROJECT_ROOT / "paper_nav_log.csv"])
PAPER_TRADE_LOG_FILE = migrate_legacy_path(TRADING_RUNTIME_DIR / "paper_trade_log.csv", [PROJECT_ROOT / "paper_trade_log.csv"])
PAPER_STATE_FILE = migrate_legacy_path(TRADING_RUNTIME_DIR / "paper_state.json", [PROJECT_ROOT / "paper_state.json"])
PAPER_PORTFOLIO_FILE = migrate_legacy_path(TRADING_RUNTIME_DIR / "paper_portfolio.csv", [PROJECT_ROOT / "paper_portfolio.csv"])
TUI_PAPER_PORTFOLIO_FILE = migrate_legacy_path(
    TRADING_RUNTIME_DIR / "tui_paper_portfolio.csv",
    [PROJECT_ROOT / "tui_paper_portfolio.csv"],
)
TUI_PAPER_NAV_LOG_FILE = migrate_legacy_path(
    TRADING_RUNTIME_DIR / "tui_paper_nav_log.csv",
    [PROJECT_ROOT / "tui_paper_nav_log.csv"],
)
TUI_PAPER_TRADE_LOG_FILE = migrate_legacy_path(
    TRADING_RUNTIME_DIR / "tui_paper_trade_log.csv",
    [PROJECT_ROOT / "tui_paper_trade_log.csv"],
)
TUI_PAPER_STATE_FILE = migrate_legacy_path(
    TRADING_RUNTIME_DIR / "tui_paper_state.json",
    [PROJECT_ROOT / "tui_paper_state.json"],
)
PAPER_PROFILE_FILE = TRADING_RUNTIME_DIR / "paper_profile.json"
PAPER_IMPORT_BACKUP_DIR = RUNTIME_DIR / "imports"
PAPER_ACCOUNTS_DIR = RUNTIME_DIR / "accounts"
PAPER_ACCOUNTS_REGISTRY_FILE = PAPER_ACCOUNTS_DIR / "registry.json"
MACRO_INDICATOR_HISTORY_FILE = migrate_legacy_path(
    RUNTIME_DIR / "macro_indicator_history.json",
    [PROJECT_ROOT / "macro_indicator_history.json"],
)
TUI_PAPER_ORDERS_FILE = migrate_legacy_path(TRADING_RUNTIME_DIR / "tui_paper_orders.json", [PROJECT_ROOT / "tui_paper_orders.json"])
TUI_PAPER_ORDER_HISTORY_FILE = migrate_legacy_path(
    TRADING_RUNTIME_DIR / "tui_paper_order_history.json",
    [PROJECT_ROOT / "tui_paper_order_history.json"],
)

ACTIVE_ACCOUNT_FILES = {
    "paper_portfolio.csv": PAPER_PORTFOLIO_FILE,
    "paper_trade_log.csv": PAPER_TRADE_LOG_FILE,
    "paper_nav_log.csv": PAPER_NAV_LOG_FILE,
    "paper_state.json": PAPER_STATE_FILE,
    "watchlist.json": WATCHLIST_FILE,
    "tui_paper_portfolio.csv": TUI_PAPER_PORTFOLIO_FILE,
    "tui_paper_trade_log.csv": TUI_PAPER_TRADE_LOG_FILE,
    "tui_paper_nav_log.csv": TUI_PAPER_NAV_LOG_FILE,
    "tui_paper_state.json": TUI_PAPER_STATE_FILE,
    "tui_paper_orders.json": TUI_PAPER_ORDERS_FILE,
    "tui_paper_order_history.json": TUI_PAPER_ORDER_HISTORY_FILE,
}


# ── Default watchlist (NEPSE blue chips) ────────────────────────────────────
DEFAULT_WATCHLIST = [
    "NABIL", "NLIC", "UPPER", "CHDC", "SBL", "SHIVM", "NRIC",
    "NTC", "NICA", "GBIME", "KBL", "MEGA", "PRVU", "SBI",
]


def _load_watchlist() -> list[dict]:
    if WATCHLIST_FILE.exists():
        try:
            data = _json.loads(WATCHLIST_FILE.read_text())
            if isinstance(data, list) and data:
                rows = _dedupe_watchlist_entries(data)
                if len(rows) != len(data):
                    _save_watchlist(rows)
                return rows
        except Exception:
            pass
    return _dedupe_watchlist_entries([_stock_watchlist_entry(sym) for sym in DEFAULT_WATCHLIST])

def _save_watchlist(entries: list[dict]) -> None:
    ensure_dir(WATCHLIST_FILE.parent)
    WATCHLIST_FILE.write_text(_json.dumps(_dedupe_watchlist_entries(entries), indent=2))


def _ensure_csv_file(path: Path, columns: list[str]) -> None:
    target = Path(path)
    ensure_dir(target.parent)
    if target.exists():
        return
    pd.DataFrame(columns=columns).to_csv(target, index=False)


def _ensure_paper_runtime_files() -> None:
    _ensure_csv_file(PAPER_PORTFOLIO_FILE, PORTFOLIO_COLS)
    _ensure_csv_file(PAPER_TRADE_LOG_FILE, TRADE_LOG_COLS)
    _ensure_csv_file(PAPER_NAV_LOG_FILE, NAV_LOG_COLS)
    _ensure_csv_file(TUI_PAPER_PORTFOLIO_FILE, PORTFOLIO_COLS)
    _ensure_csv_file(TUI_PAPER_TRADE_LOG_FILE, TRADE_LOG_COLS)
    _ensure_csv_file(TUI_PAPER_NAV_LOG_FILE, NAV_LOG_COLS)
    if not PAPER_STATE_FILE.exists():
        save_runtime_state(
            str(PAPER_STATE_FILE),
            {"cash": float(INITIAL_CAPITAL), "daily_start_nav": float(INITIAL_CAPITAL)},
        )
    if not TUI_PAPER_STATE_FILE.exists():
        save_runtime_state(
            str(TUI_PAPER_STATE_FILE),
            {"cash": float(INITIAL_CAPITAL), "daily_start_nav": float(INITIAL_CAPITAL)},
        )


def _lock_file_exclusive(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    if msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    raise RuntimeError("No file-lock implementation available on this platform")


def _unlock_file(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    raise RuntimeError("No file-lock implementation available on this platform")


def _write_json_locked(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    with path.open("a+", encoding="utf-8") as handle:
        _lock_file_exclusive(handle)
        try:
            handle.seek(0)
            handle.truncate()
            _json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            _unlock_file(handle)


def _load_macro_indicator_history() -> dict[str, dict]:
    path = Path(MACRO_INDICATOR_HISTORY_FILE)
    if not path.exists():
        return {}
    try:
        payload = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_macro_indicator_history(payload: dict[str, dict]) -> None:
    _write_json_locked(Path(MACRO_INDICATOR_HISTORY_FILE), payload)


def _apply_indicator_history_change(
    history: dict[str, dict],
    *,
    key: str,
    value: float,
    timestamp: Optional[str] = None,
) -> tuple[Optional[float], Optional[float]]:
    prev = history.get(str(key)) or {}
    prev_value = float(prev.get("value") or 0.0)
    change = None
    change_pct = None
    if prev_value > 0:
        change = float(value) - prev_value
        change_pct = (change / prev_value) * 100.0
    history[str(key)] = {
        "value": float(value),
        "timestamp": str(timestamp or datetime.utcnow().isoformat()),
    }
    return change, change_pct


def _load_accounts_registry() -> dict:
    if PAPER_ACCOUNTS_REGISTRY_FILE.exists():
        try:
            payload = _json.loads(PAPER_ACCOUNTS_REGISTRY_FILE.read_text())
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    return {"accounts": []}


def _save_accounts_registry(payload: dict) -> None:
    ensure_dir(PAPER_ACCOUNTS_REGISTRY_FILE.parent)
    PAPER_ACCOUNTS_REGISTRY_FILE.write_text(_json.dumps(payload, indent=2, sort_keys=True))


def _account_dir(account_id: str) -> Path:
    return ensure_dir(PAPER_ACCOUNTS_DIR / str(account_id))


def _copy_file_if_exists(source: Path, target: Path) -> None:
    src = Path(source)
    dst = Path(target)
    ensure_dir(dst.parent)
    if src.exists():
        shutil.copy2(src, dst)


def _blank_account_files(target_dir: Path) -> None:
    ensure_dir(target_dir)
    _ensure_csv_file(target_dir / "paper_portfolio.csv", PORTFOLIO_COLS)
    _ensure_csv_file(target_dir / "paper_trade_log.csv", TRADE_LOG_COLS)
    _ensure_csv_file(target_dir / "paper_nav_log.csv", NAV_LOG_COLS)
    _ensure_csv_file(target_dir / "tui_paper_portfolio.csv", PORTFOLIO_COLS)
    _ensure_csv_file(target_dir / "tui_paper_trade_log.csv", TRADE_LOG_COLS)
    _ensure_csv_file(target_dir / "tui_paper_nav_log.csv", NAV_LOG_COLS)
    if not (target_dir / "paper_state.json").exists():
        save_runtime_state(
            str(target_dir / "paper_state.json"),
            {"cash": float(INITIAL_CAPITAL), "daily_start_nav": float(INITIAL_CAPITAL)},
        )
    if not (target_dir / "tui_paper_state.json").exists():
        save_runtime_state(
            str(target_dir / "tui_paper_state.json"),
            {"cash": float(INITIAL_CAPITAL), "daily_start_nav": float(INITIAL_CAPITAL)},
        )
    if not (target_dir / "watchlist.json").exists():
        (target_dir / "watchlist.json").write_text(_json.dumps([_stock_watchlist_entry(sym) for sym in DEFAULT_WATCHLIST], indent=2))
    if not (target_dir / "tui_paper_orders.json").exists():
        (target_dir / "tui_paper_orders.json").write_text("[]")
    if not (target_dir / "tui_paper_order_history.json").exists():
        (target_dir / "tui_paper_order_history.json").write_text("[]")


def _next_account_id(accounts: list[dict]) -> str:
    highest = 0
    for account in accounts or []:
        account_id = str(account.get("id") or "")
        match = re.fullmatch(r"account_(\d+)", account_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"account_{highest + 1}"


def _portfolio_mark_value(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    total = 0.0
    for _, row in df.iterrows():
        qty = int(float(row.get("Quantity") or 0))
        price = float(row.get("Last_LTP") or row.get("Buy_Price") or 0)
        total += qty * price
    return float(total)


def _build_account_seed_state(portfolio_df: pd.DataFrame, target_nav: float) -> tuple[dict, pd.DataFrame]:
    positions_value = round(_portfolio_mark_value(portfolio_df), 2)
    cash = round(float(target_nav) - positions_value, 2)
    if cash < 0:
        raise ValueError(f"Target NAV is below current marked portfolio value {_npr_k(positions_value)}")
    today = datetime.now().strftime("%Y-%m-%d")
    state = {
        "cash": cash,
        "daily_start_nav": round(float(target_nav), 2),
        "initial_capital": round(float(target_nav), 2),
    }
    nav_log = pd.DataFrame(
        [
            {
                "Date": today,
                "Cash": cash,
                "Positions_Value": positions_value,
                "NAV": round(float(target_nav), 2),
                "Num_Positions": len(portfolio_df.index),
            }
        ],
        columns=NAV_LOG_COLS,
    )
    return state, nav_log

def _load_profile_config() -> dict:
    if PAPER_PROFILE_FILE.exists():
        try:
            payload = _json.loads(PAPER_PROFILE_FILE.read_text())
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    return {}


def _save_profile_config(payload: dict) -> None:
    ensure_dir(PAPER_PROFILE_FILE.parent)
    PAPER_PROFILE_FILE.write_text(_json.dumps(payload, indent=2, sort_keys=True))


def _bootstrap_paper_accounts() -> tuple[list[dict], str]:
    ensure_dir(PAPER_ACCOUNTS_DIR)
    strategy_registry.ensure_builtin_strategies()
    registry = _load_accounts_registry()
    accounts = strategy_registry.ensure_account_strategy_ids(list(registry.get("accounts") or []))
    profile = _load_profile_config()
    current_account_id = str(profile.get("current_account_id") or "").strip()
    if not accounts:
        current_account_id = "account_1"
        account = {
            "id": current_account_id,
            "name": "Account 1",
            "strategy_id": strategy_registry.default_strategy_for_account(current_account_id),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        accounts = [account]
    known_ids = {str(account.get("id") or "") for account in accounts}
    if current_account_id not in known_ids:
        current_account_id = str(accounts[0].get("id") or "account_1")
    target_dir = _account_dir(current_account_id)
    has_existing_runtime = any((target_dir / name).exists() for name in ACTIVE_ACCOUNT_FILES)
    if not has_existing_runtime:
        for name, active_path in ACTIVE_ACCOUNT_FILES.items():
            _copy_file_if_exists(Path(active_path), target_dir / name)
    _blank_account_files(target_dir)
    registry["accounts"] = accounts
    _save_accounts_registry(registry)
    profile["current_account_id"] = current_account_id
    _save_profile_config(profile)
    return accounts, current_account_id


def _load_manual_paper_cash(total_cost: float, nav_log: Optional[pd.DataFrame] = None) -> float:
    state = load_runtime_state(str(PAPER_STATE_FILE))
    saved_cash = state.get("cash")
    if isinstance(saved_cash, (int, float)):
        return float(saved_cash)
    rebuilt_cash = calculate_cash_from_trade_log(INITIAL_CAPITAL, str(PAPER_TRADE_LOG_FILE))
    if rebuilt_cash is not None:
        return float(rebuilt_cash)
    if nav_log is not None and not nav_log.empty and "Cash" in nav_log.columns:
        try:
            latest_cash = float(nav_log.iloc[-1]["Cash"])
            return latest_cash
        except Exception:
            pass
    return max(0.0, float(INITIAL_CAPITAL) - float(total_cost))


def _account_initial_capital_from_files(account_dir: Path, fallback: float = INITIAL_CAPITAL) -> float:
    state_path = account_dir / "paper_state.json"
    nav_path = account_dir / "paper_nav_log.csv"
    portfolio_path = account_dir / "paper_portfolio.csv"
    trade_log_path = account_dir / "paper_trade_log.csv"

    state = load_runtime_state(str(state_path))
    if isinstance(state, dict):
        for key in ("initial_capital", "daily_start_nav"):
            value = _positive_float(state.get(key))
            if value is not None:
                return value

    if nav_path.exists():
        try:
            nav_log = pd.read_csv(nav_path)
            if not nav_log.empty and "NAV" in nav_log.columns:
                value = _positive_float(nav_log.iloc[0].get("NAV"))
                if value is not None:
                    return value
        except Exception:
            pass

    cash = _positive_float(state.get("cash") if isinstance(state, dict) else None)
    if cash is not None:
        try:
            portfolio = pd.read_csv(portfolio_path) if portfolio_path.exists() else pd.DataFrame()
            trades = pd.read_csv(trade_log_path) if trade_log_path.exists() else pd.DataFrame()
        except Exception:
            portfolio = trades = pd.DataFrame()
        if portfolio.empty and trades.empty:
            return cash

    return float(fallback)


def _tms_health_flag(health: dict, key: str) -> bool:
    """Support both legacy nested status payloads and the current flat payload."""
    if not isinstance(health, dict):
        return False
    status = health.get("status")
    if isinstance(status, dict) and key in status:
        return bool(status.get(key))
    return bool(health.get(key))


def _load_cached_tms_bundle() -> dict:
    # TMS live brokerage not included in public release — always returns empty.
    return {}


def _merge_tms_bundle_with_cache(bundle: Optional[dict]) -> dict:
    merged = _load_cached_tms_bundle()
    if isinstance(bundle, dict):
        for key, payload in bundle.items():
            if payload:
                merged[key] = payload
    return merged


def _load_nav_log() -> pd.DataFrame:
    return pd.read_csv(PAPER_NAV_LOG_FILE) if PAPER_NAV_LOG_FILE.exists() else pd.DataFrame()

def _load_trade_log() -> pd.DataFrame:
    return pd.read_csv(PAPER_TRADE_LOG_FILE) if PAPER_TRADE_LOG_FILE.exists() else pd.DataFrame()


def _load_hedge_trade_log() -> list:
    """Load persisted hedge trade log from disk."""
    try:
        if HEDGE_TRADE_LOG_FILE.exists():
            import json as _json
            data = _json.loads(HEDGE_TRADE_LOG_FILE.read_text(encoding="utf-8"))
            return list(data) if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _save_hedge_trade_log(trades: list) -> None:
    """Persist hedge trade log to disk. Latent writer paired with _load_hedge_trade_log; no caller wires it yet."""
    try:
        import json as _json
        HEDGE_TRADE_LOG_FILE.write_text(_json.dumps(trades, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
