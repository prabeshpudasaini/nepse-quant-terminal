"""Paper-account profile + multi-account registry + strategy registry CRUD.

Account/strategy orchestration: profile + multi-account registry management,
strategy registry CRUD, CSV/bundle import, manual NAV, and holdings-watchlist
sync. These do NOT construct engines (that lives in TradingEngineMixin); the
account form-shims (_account_create_from_form / _account_activate_selected /
_account_set_nav_from_form) DELEGATE to TradingEngineMixin's
_create_paper_account / _activate_paper_account via self (MRO-resolved).

Worker/reader co-location: the strategy-backtest worker _run_strategy_backtest_async
writes _strategy_backtest_result and marshals to its readers
(_set_strategy_backtest_status / _on_strategy_backtest_progress) which live in
this mixin. _watchlist_entry_from_rates_selection reads _get_filtered_rates_payload
(TabRefreshMixin) via self — a safe cross-mixin marshal seam.
"""

import copy
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from textual import work
from textual.widgets import DataTable, Input

from apps.tui.io.csv_import import (
    _build_holdings_watchlist_entries,
    _coerce_dragdrop_path,
    _merge_watchlist_entries,
    _normalize_import_nav_log,
    _normalize_import_portfolio,
    _normalize_import_trade_log,
)
from apps.tui.io.persistence import (
    ACTIVE_ACCOUNT_FILES,
    INITIAL_CAPITAL,
    PAPER_IMPORT_BACKUP_DIR,
    PAPER_NAV_LOG_FILE,
    PAPER_PORTFOLIO_FILE,
    PAPER_STATE_FILE,
    PAPER_TRADE_LOG_FILE,
    PROJECT_ROOT,
    TUI_PAPER_NAV_LOG_FILE,
    TUI_PAPER_PORTFOLIO_FILE,
    TUI_PAPER_STATE_FILE,
    TUI_PAPER_TRADE_LOG_FILE,
    _account_dir,
    _blank_account_files,
    _copy_file_if_exists,
    _load_accounts_registry,
    _load_nav_log,
    _load_profile_config,
    _save_accounts_registry,
    _save_profile_config,
    _save_watchlist,
)
from apps.tui.io.stats import _compute_portfolio_stats
from apps.tui.io.watchlist_io import _normalize_watchlist_entry, _watchlist_entry_key
from apps.tui.render.cells import _npr_k
from configs.long_term import LONG_TERM_CONFIG
from backend.quant_pro.dashboard_data import load_port
from backend.quant_pro.paths import ensure_dir
from backend.trading import strategy_registry
from backend.trading.live_trader import (
    NAV_LOG_COLS,
    calculate_cash_from_trade_log,
    load_runtime_state,
    save_runtime_state,
)


class AccountsStrategiesMixin:

    def _active_account_name(self) -> str:
        account_id = str(getattr(self, "_current_account_id", "account_1") or "account_1")
        for account in list(getattr(self, "_paper_accounts", []) or []):
            if str(account.get("id") or "") == account_id:
                return str(account.get("name") or account_id)
        return account_id

    def _strategy_account_binding(self, account_id: str) -> Optional[dict]:
        aid = str(account_id or "").strip()
        for account in list(getattr(self, "_paper_accounts", []) or []):
            if str(account.get("id") or "") == aid:
                return account
        return None

    def _strategy_name_for_account(self, account_id: str) -> str:
        account = self._strategy_account_binding(account_id)
        strategy_id = str((account or {}).get("strategy_id") or strategy_registry.default_strategy_for_account(account_id))
        return strategy_registry.strategy_name(strategy_id)

    def _strategy_display_name(self, strategy_id: str, fallback_name: str = "") -> str:
        from apps.tui.dashboard_tui import STRATEGY_DISPLAY_NAMES
        sid = str(strategy_id or "").strip()
        return str(STRATEGY_DISPLAY_NAMES.get(sid) or fallback_name or sid)

    def _active_strategy_payload(self) -> Optional[dict]:
        account_id = str(getattr(self, "_current_account_id", "account_1") or "account_1")
        account = self._strategy_account_binding(account_id)
        strategy_id = str((account or {}).get("strategy_id") or strategy_registry.default_strategy_for_account(account_id))
        return strategy_registry.load_strategy(strategy_id)

    def _active_strategy_config(self) -> dict:
        payload = self._active_strategy_payload() or {}
        config = dict(payload.get("config") or {})
        if not config:
            config = copy.deepcopy(LONG_TERM_CONFIG)
        return config

    def _load_strategies_registry(self) -> None:
        strategy_registry.ensure_builtin_strategies()
        self._strategies = strategy_registry.list_strategies()
        self._strategy_comparison_snapshot = strategy_registry.load_strategy_comparison_snapshot()
        current_account = self._strategy_account_binding(getattr(self, "_current_account_id", "account_1"))
        preferred = str((current_account or {}).get("strategy_id") or "default_c5")
        known = {str(item.get("id") or "") for item in self._strategies}
        self._selected_strategy_id = preferred if preferred in known else (next(iter(known)) if known else "default_c5")

    def _selected_strategy_payload(self) -> Optional[dict]:
        sid = str(getattr(self, "_selected_strategy_id", "") or "").strip()
        for item in list(getattr(self, "_strategies", []) or []):
            if str(item.get("id") or "") == sid:
                return item
        return strategy_registry.load_strategy(sid)

    def _strategy_form_payload(self) -> dict:
        if getattr(self, "_new_strategy_mode", False):
            # New strategy: start from clean defaults, not the currently selected one
            base = {}
            config = {
                "holding_days": 40,
                "max_positions": 5,
                "rebalance_frequency": 5,
                "stop_loss_pct": 0.08,
                "trailing_stop_pct": 0.15,
                "sector_limit": 0.35,
                "use_regime_filter": True,
                "use_trailing_stop": True,
                "signal_types": [],
            }
        else:
            base = self._selected_strategy_payload() or strategy_registry.load_strategy("default_c5") or {}
            config = copy.deepcopy(base.get("config") or {})

        def _read_input(widget_id: str) -> str:
            return str(self.query_one(f"#{widget_id}", Input).value or "").strip()

        name = _read_input("strategy-inp-name") or str(base.get("name") or "Strategy")
        description = _read_input("strategy-inp-description")
        # Signals come from the picker buttons (not a text input)
        active = set(getattr(self, "_active_signals", set()))
        if active:
            # Preserve original ordering where possible, append new ones
            orig_order = list(config.get("signal_types") or [])
            ordered = [s for s in orig_order if s in active]
            ordered += [s for s in active if s not in ordered]
            config["signal_types"] = ordered

        numeric_fields = {
            "holding_days": ("strategy-inp-holding-days", int),
            "rebalance_frequency": ("strategy-inp-rebalance", int),
            "max_positions": ("strategy-inp-max-positions", int),
            "stop_loss_pct": ("strategy-inp-stop-loss", float),
            "trailing_stop_pct": ("strategy-inp-trailing-stop", float),
            "sector_limit": ("strategy-inp-sector-limit", float),
        }
        for key, (widget_id, caster) in numeric_fields.items():
            raw = _read_input(widget_id)
            if raw:
                config[key] = caster(raw)

        return {
            "id": str(base.get("id") or ""),
            "name": name,
            "description": description,
            "runner_mode": str(base.get("runner_mode") or "temp_patched"),
            "execution_mode": str(base.get("execution_mode") or "paper_runtime"),
            "config": config,
            "ranking_overlay": copy.deepcopy(base.get("ranking_overlay") or {"mode": "baseline"}),
            "notes": {
                **dict(base.get("notes") or {}),
                "base_strategy_id": str(base.get("id") or ""),
            },
        }

    def _selected_strategy_chart_result(self, strategy_id: str) -> dict:
        sid = str(strategy_id or "").strip()
        result = dict(getattr(self, "_strategy_backtest_result", {}) or {})
        if result and str((result.get("strategy") or {}).get("id") or "") != sid:
            result = {}
        if result and len(list((result.get("summary") or {}).get("daily_nav") or [])) >= 5:
            return result
        return dict(strategy_registry.load_strategy_chart_result(sid) or {})

    def _strategy_saved_metrics(self, strategy_id: str) -> Optional[dict]:
        return strategy_registry.comparison_metrics_for_strategy(strategy_id)

    def _set_strategy_backtest_status(
        self,
        strategy_id: str,
        status: str,
        message: str = "",
        progress_pct: Optional[int] = None,
    ) -> None:
        sid = str(strategy_id or "").strip()
        if not sid:
            return
        statuses = dict(getattr(self, "_strategy_backtest_statuses", {}) or {})
        current = dict(statuses.get(sid) or {})
        if progress_pct is None:
            progress_pct = current.get("progress_pct")
        statuses[sid] = {
            **current,
            "status": str(status or "").upper(),
            "message": str(message or ""),
            "progress_pct": progress_pct,
        }
        self._strategy_backtest_statuses = statuses
        self._populate_strategy_list()

    def _on_strategy_backtest_progress(self, strategy_id: str, payload: dict) -> None:
        sid = str(strategy_id or "").strip()
        if not sid:
            return
        progress = payload.get("progress_pct")
        try:
            progress_int = max(0, min(100, int(progress)))
        except Exception:
            progress_int = None
        message = str(payload.get("message") or "").strip()
        date_text = str(payload.get("date") or "").strip()
        if date_text:
            message = f"{message} | {date_text}".strip(" |")
        self._set_strategy_backtest_status(sid, "RUN", message, progress_int)
        if message:
            self._set_status(f"Backtest progress | {progress_int if progress_int is not None else 0}% | {message}")

    def _assign_strategy_to_account(self, strategy_id: str, account_id: str) -> str:
        sid = str(strategy_id or "").strip()
        aid = str(account_id or "").strip()
        if not sid:
            raise ValueError("Select a strategy first")
        if not aid:
            raise ValueError("Select an account first")
        registry = _load_accounts_registry()
        accounts = list(registry.get("accounts") or [])
        now_stamp = datetime.now().isoformat(timespec="seconds")
        updated = False
        for account in accounts:
            if str(account.get("id") or "") == aid:
                account["strategy_id"] = sid
                account["updated_at"] = now_stamp
                updated = True
        if not updated:
            raise ValueError("Account not found")
        registry["accounts"] = accounts
        _save_accounts_registry(registry)
        self._paper_accounts = strategy_registry.ensure_account_strategy_ids(accounts)
        if aid == str(getattr(self, "_current_account_id", "") or ""):
            self._apply_active_strategy_runtime()
            self._signals_table_cache_key = ""
            self._signals_table_cache_payload = None
            self._sync_agent_account_context_env()
            self._load_signals_async(force=True)
        self._populate_account_list()
        self._populate_paper_profile_panel(self._stats)
        self._populate_strategies_tab()
        return f"Assigned {strategy_registry.strategy_name(sid)} to {aid}"

    def _delete_custom_strategy(self) -> str:
        selected = self._selected_strategy_payload()
        if not selected:
            return "No strategy selected"
        if str(selected.get("source") or "") != "custom":
            return f"Cannot delete built-in strategy '{selected.get('name')}'"
        sid = str(selected.get("id") or "").strip()
        if not sid:
            return "Invalid strategy ID"
        from backend.trading.strategy_registry import CUSTOM_STRATEGY_DIR
        path = CUSTOM_STRATEGY_DIR / f"{sid}.json"
        if path.exists():
            path.unlink()
        name = str(selected.get("name") or sid)
        self._load_strategies_registry()
        self._populate_strategies_tab()
        # Clear the form
        self._active_signals = set()
        self._sync_signal_buttons()
        return f"Deleted strategy '{name}'"

    def _save_strategy_from_form(self) -> str:
        new_mode = getattr(self, "_new_strategy_mode", False)
        payload = self._strategy_form_payload()
        if not str(payload.get("name") or "").strip():
            raise ValueError("Enter a strategy name")
        selected = self._selected_strategy_payload() or {}
        # Never overwrite an existing strategy when NEW was pressed
        overwrite = (
            not new_mode
            and str(selected.get("source") or "") == "custom"
            and str(selected.get("id") or "") == str(payload.get("id") or "")
        )
        if not overwrite:
            payload = dict(payload)
            payload.pop("id", None)
        saved = strategy_registry.save_custom_strategy(
            payload,
            strategy_id=(str(selected.get("id") or "") if overwrite else None),
            overwrite=overwrite,
        )
        self._new_strategy_mode = False
        self._load_strategies_registry()
        self._selected_strategy_id = str(saved.get("id") or self._selected_strategy_id)
        self._populate_strategies_tab()
        return f"Saved strategy {saved.get('name')} ({saved.get('id')})"

    @work(thread=True)
    def _run_strategy_backtest_async(self, strategy_id: str, start_date: str, end_date: str, capital: float) -> None:
        try:
            payload = strategy_registry.load_strategy(strategy_id)
            if not payload:
                raise ValueError("Strategy not found")
            self.call_from_thread(self._set_strategy_backtest_status, strategy_id, "RUN")
            self.call_from_thread(self._set_status, f"Backtesting {payload.get('name')}...")
            log_path = PROJECT_ROOT / "data" / "runtime" / "logs" / "strategy_backtest.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s"))

            _bt_loggers = [
                logging.getLogger(n)
                for n in ("backend", "backend.backtesting.simple_backtest", "backend.trading.strategy_registry")
            ]
            _bt_prev_levels = [(lg, lg.level) for lg in _bt_loggers]
            root_logger = logging.getLogger()
            _root_prev = root_logger.level
            _stream_prev_levels = [
                (handler, handler.level)
                for handler in root_logger.handlers
                if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
            ]
            root_logger.addHandler(file_handler)
            root_logger.setLevel(logging.INFO)
            for lg in _bt_loggers:
                lg.setLevel(logging.INFO)
            for handler, _level in _stream_prev_levels:
                handler.setLevel(logging.WARNING)
            try:
                logging.getLogger(__name__).info(
                    "Starting strategy backtest id=%s start=%s end=%s capital=%s log=%s",
                    strategy_id,
                    start_date,
                    end_date,
                    capital,
                    log_path,
                )
                result = strategy_registry.run_strategy_backtest(
                    payload,
                    start_date=start_date,
                    end_date=end_date,
                    capital=capital,
                    progress_callback=lambda progress: self.call_from_thread(
                        self._on_strategy_backtest_progress,
                        strategy_id,
                        dict(progress or {}),
                    ),
                )
                logging.getLogger(__name__).info("Finished strategy backtest id=%s", strategy_id)
            finally:
                for lg, lvl in _bt_prev_levels:
                    lg.setLevel(lvl)
                root_logger.setLevel(_root_prev)
                for handler, level in _stream_prev_levels:
                    handler.setLevel(level)
                root_logger.removeHandler(file_handler)
                file_handler.close()
            self._strategy_backtest_result = result
            summary = dict(result.get("summary") or {})
            nepse   = dict(result.get("nepse") or {})
            self.call_from_thread(self._populate_strategies_tab)
            self.call_from_thread(self._set_strategy_backtest_status, strategy_id, "CHART")
            strat_ret = float(summary.get("total_return_pct") or 0.0)
            nepse_ret = float(nepse.get("return_pct") or 0.0)
            self.call_from_thread(
                self._set_status,
                f"Backtest done | {payload.get('name')} {strat_ret:+.2f}% vs NEPSE {nepse_ret:+.2f}%  |  Generating chart...",
            )
            # ── Generate quick chart (non-blocking, runs in same thread) ──────
            try:
                from validation.quick_chart import generate_quick_chart
                from backend.quant_pro.database import get_db_path as _get_db_path
                chart_path = generate_quick_chart(
                    result,
                    strategy_name=str(payload.get("name") or strategy_id),
                    start_date=start_date,
                    end_date=end_date,
                    db_path=str(_get_db_path()),
                    auto_open=True,
                )
                chart_note = f"  Chart → {chart_path}" if chart_path else ""
            except Exception:
                chart_note = ""
            self.call_from_thread(
                self._set_status,
                f"Backtest done | {payload.get('name')} {strat_ret:+.2f}% vs NEPSE {nepse_ret:+.2f}%{chart_note}",
            )
            self.call_from_thread(self._set_strategy_backtest_status, strategy_id, "OK")
        except Exception as exc:
            self.call_from_thread(self._set_strategy_backtest_status, strategy_id, "FAIL", str(exc))
            self.call_from_thread(self._set_status, f"Strategy backtest failed: {exc}")

    def _start_strategy_backtest(self) -> str:
        selected = self._selected_strategy_payload()
        if not selected:
            raise ValueError("Select a strategy first")
        start = str(self.query_one("#strategy-inp-backtest-start", Input).value or "").strip()
        end = str(self.query_one("#strategy-inp-backtest-end", Input).value or "").strip()
        capital_raw = str(self.query_one("#strategy-inp-backtest-capital", Input).value or "").strip().replace(",", "")
        if not start or not end:
            raise ValueError("Enter start and end dates")
        capital = float(capital_raw or INITIAL_CAPITAL)
        strategy_id = str(selected.get("id") or "")
        self._set_strategy_backtest_status(strategy_id, "RUN")
        self._run_strategy_backtest_async(strategy_id, start, end, capital)
        return f"Backtest launched for {selected.get('name')}"

    def _account_create_from_form(self) -> str:
        raw_name = self.query_one("#profile-inp-account-name", Input).value
        raw_path = self.query_one("#profile-inp-portfolio", Input).value
        raw_nav = self.query_one("#profile-inp-target-nav", Input).value
        return self._create_paper_account(raw_name, raw_path, raw_nav)

    def _account_activate_selected(self) -> str:
        return self._activate_paper_account(self._selected_account_id)

    def _account_set_nav_from_form(self) -> str:
        raw_nav = self.query_one("#profile-inp-target-nav", Input).value
        msg = self._seed_manual_nav(raw_nav)
        self._populate_portfolio_and_risk()
        return msg

    def _account_sync_watchlist(self) -> str:
        held = self._sync_holdings_watchlist()
        self._populate_paper_profile_panel(self._stats)
        return f"Watchlist synced from {held} holdings"

    def _toggle_account_help(self) -> str:
        self._account_help_visible = not self._account_help_visible
        self._populate_paper_profile_panel(self._stats)
        return "Account help shown" if self._account_help_visible else "Account help hidden"

    def _backup_profile_targets(self, targets: list[Path]) -> Optional[Path]:
        existing = [Path(target) for target in targets if Path(target).exists()]
        if not existing:
            return None
        backup_dir = ensure_dir(PAPER_IMPORT_BACKUP_DIR / datetime.now().strftime("%Y%m%d_%H%M%S"))
        for target in existing:
            shutil.copy2(target, backup_dir / target.name)
        return backup_dir

    def _persist_profile_paths(self, **updates) -> None:
        profile = _load_profile_config()
        profile.update({k: v for k, v in updates.items() if v is not None})
        _save_profile_config(profile)

    def _account_snapshot(self, account: dict) -> dict:
        account_id = str(account.get("id") or "")
        account_dir = _account_dir(account_id)
        snap = self._profile_runtime_snapshot(account_dir=account_dir)
        return {
            "id": account_id,
            "name": str(account.get("name") or account_id),
            "strategy": strategy_registry.strategy_name(str(account.get("strategy_id") or strategy_registry.default_strategy_for_account(account_id))),
            "holdings": snap["holdings"],
            "trades": snap["trades"],
            "nav": snap["nav"],
            "cash": snap["cash"],
            "updated_at": str(account.get("updated_at") or ""),
        }

    def _persist_active_account_snapshot(self) -> None:
        account_id = str(getattr(self, "_current_account_id", "") or "").strip()
        if not account_id:
            return
        target_dir = _account_dir(account_id)
        _blank_account_files(target_dir)
        for name, active_path in ACTIVE_ACCOUNT_FILES.items():
            target = target_dir / name
            # Account directories are now the canonical paper books. Keep legacy
            # active-file snapshots from overwriting live autopilot/manual fills.
            if name != "watchlist.json" and target.exists():
                continue
            _copy_file_if_exists(Path(active_path), target)
        registry = _load_accounts_registry()
        accounts = list(registry.get("accounts") or [])
        now_stamp = datetime.now().isoformat(timespec="seconds")
        for account in accounts:
            if str(account.get("id") or "") == account_id:
                account["updated_at"] = now_stamp
        registry["accounts"] = accounts
        _save_accounts_registry(registry)
        self._paper_accounts = accounts

    def _save_current_account_snapshot(self) -> str:
        self._persist_active_account_snapshot()
        self._populate_paper_profile_panel(self._stats)
        active_name = next((str(a.get("name") or self._current_account_id) for a in self._paper_accounts if str(a.get("id") or "") == self._current_account_id), self._current_account_id)
        return f"Saved snapshot for {active_name}"

    def _copy_import_dataframe(
        self,
        source_path: Path,
        target_path: Path,
        *,
        normalizer,
        mirror_path: Optional[Path] = None,
    ) -> int:
        df = pd.read_csv(source_path)
        normalized = normalizer(df)
        ensure_dir(target_path.parent)
        normalized.to_csv(target_path, index=False)
        if mirror_path:
            ensure_dir(mirror_path.parent)
            normalized.to_csv(mirror_path, index=False)
        return len(normalized.index)

    def _effective_paper_watchlist(self) -> tuple[list[dict], int]:
        ltps = self.md.ltps() if hasattr(self, "md") else {}
        port = load_port()
        held_entries = _build_holdings_watchlist_entries(port, ltps)
        return _merge_watchlist_entries(held_entries, self._paper_watchlist), len(held_entries)

    def _sync_holdings_watchlist(self) -> int:
        port = load_port()
        held_entries = _build_holdings_watchlist_entries(port, self.md.ltps() if hasattr(self, "md") else {})
        merged = _merge_watchlist_entries(held_entries, self._paper_watchlist)
        self._paper_watchlist = merged
        _save_watchlist(self._paper_watchlist)
        self._watchlist = list(merged)
        self._populate_watchlist()
        self._persist_active_account_snapshot()
        self._populate_account_list()
        return len(held_entries)

    def _seed_manual_nav(self, raw_value: str) -> str:
        token = str(raw_value or "").strip().replace(",", "")
        if not token:
            raise ValueError("Enter a target NAV")
        target_nav = float(token)
        if target_nav <= 0:
            raise ValueError("Target NAV must be positive")
        stats = _compute_portfolio_stats(self.md)
        positions_value = float(stats.get("total_value") or 0.0)
        target_cash = round(target_nav - positions_value, 2)
        if target_cash < 0:
            raise ValueError(f"Target NAV is below current positions value {_npr_k(positions_value)}")

        state = load_runtime_state(str(PAPER_STATE_FILE))
        state["cash"] = target_cash
        state["daily_start_nav"] = target_nav
        state["initial_capital"] = target_nav
        save_runtime_state(str(PAPER_STATE_FILE), state)

        tui_state = load_runtime_state(str(TUI_PAPER_STATE_FILE))
        tui_state["cash"] = target_cash
        tui_state["daily_start_nav"] = target_nav
        tui_state["initial_capital"] = target_nav
        save_runtime_state(str(TUI_PAPER_STATE_FILE), tui_state)

        nav_log = _load_nav_log()
        today = datetime.now().strftime("%Y-%m-%d")
        row = {
            "Date": today,
            "Cash": target_cash,
            "Positions_Value": round(positions_value, 2),
            "NAV": round(target_nav, 2),
            "Num_Positions": int(stats.get("n_positions") or 0),
        }
        if not nav_log.empty and str(nav_log.iloc[-1].get("Date", ""))[:10] == today:
            nav_log.iloc[-1] = row
        else:
            nav_log = pd.concat([nav_log, pd.DataFrame([row])], ignore_index=True)
        nav_log.reindex(columns=NAV_LOG_COLS).to_csv(PAPER_NAV_LOG_FILE, index=False)
        nav_log.reindex(columns=NAV_LOG_COLS).to_csv(TUI_PAPER_NAV_LOG_FILE, index=False)

        self._persist_profile_paths(target_nav=target_nav)
        self._persist_active_account_snapshot()
        self._populate_account_list()
        return f"NAV set to {_npr_k(target_nav)} with cash {_npr_k(target_cash)}"

    def _import_profile_bundle(self, raw_path: str) -> str:
        path = _coerce_dragdrop_path(raw_path)
        if not path or not path.exists():
            raise ValueError("Import folder/file path not found")
        base_dir = path if path.is_dir() else path.parent
        portfolio = base_dir / "paper_portfolio.csv"
        trades = base_dir / "paper_trade_log.csv"
        nav = base_dir / "paper_nav_log.csv"
        state = base_dir / "paper_state.json"
        if not portfolio.exists() and path.is_file() and path.suffix.lower() == ".csv":
            if "portfolio" in path.name:
                portfolio = path
            elif "trade" in path.name:
                trades = path
            elif "nav" in path.name:
                nav = path
        if not any(p.exists() for p in (portfolio, trades, nav, state)):
            raise ValueError("No paper trading files found in that folder")
        backup_dir = self._backup_profile_targets(
            [
                PAPER_PORTFOLIO_FILE,
                PAPER_TRADE_LOG_FILE,
                PAPER_NAV_LOG_FILE,
                PAPER_STATE_FILE,
                TUI_PAPER_PORTFOLIO_FILE,
                TUI_PAPER_TRADE_LOG_FILE,
                TUI_PAPER_NAV_LOG_FILE,
                TUI_PAPER_STATE_FILE,
            ]
        )
        imported: list[str] = []
        if portfolio.exists():
            count = self._copy_import_dataframe(
                portfolio,
                PAPER_PORTFOLIO_FILE,
                normalizer=_normalize_import_portfolio,
                mirror_path=TUI_PAPER_PORTFOLIO_FILE,
            )
            imported.append(f"portfolio {count}")
        if trades.exists():
            count = self._copy_import_dataframe(
                trades,
                PAPER_TRADE_LOG_FILE,
                normalizer=_normalize_import_trade_log,
                mirror_path=TUI_PAPER_TRADE_LOG_FILE,
            )
            imported.append(f"trades {count}")
        if nav.exists():
            count = self._copy_import_dataframe(
                nav,
                PAPER_NAV_LOG_FILE,
                normalizer=_normalize_import_nav_log,
                mirror_path=TUI_PAPER_NAV_LOG_FILE,
            )
            imported.append(f"nav {count}")
        if state.exists():
            payload = load_runtime_state(str(state))
            if not isinstance(payload, dict):
                raise ValueError("paper_state.json is not valid JSON")
            save_runtime_state(str(PAPER_STATE_FILE), payload)
            save_runtime_state(str(TUI_PAPER_STATE_FILE), payload)
            imported.append("state")
        held_count = self._sync_holdings_watchlist()
        self._persist_profile_paths(
            folder_path=str(base_dir),
            portfolio_path=str(portfolio) if portfolio.exists() else None,
            trades_path=str(trades) if trades.exists() else None,
            nav_path=str(nav) if nav.exists() else None,
        )
        self._populate_portfolio_and_risk()
        self._populate_trades_full()
        self._render_hedge_panel()
        self._populate_watchlist()
        self._populate_paper_profile_panel(self._stats)
        backup_note = f" | backup {backup_dir.name}" if backup_dir else ""
        return f"Imported {', '.join(imported)} | holdings synced {held_count}{backup_note}"

    def _import_profile_csv(self, kind: str, raw_path: str) -> str:
        path = _coerce_dragdrop_path(raw_path)
        if not path or not path.exists():
            raise ValueError(f"{kind.title()} CSV path not found")
        if path.is_dir():
            return self._import_profile_bundle(str(path))
        backup_dir = None
        if kind == "portfolio":
            backup_dir = self._backup_profile_targets([PAPER_PORTFOLIO_FILE, TUI_PAPER_PORTFOLIO_FILE])
            count = self._copy_import_dataframe(
                path,
                PAPER_PORTFOLIO_FILE,
                normalizer=_normalize_import_portfolio,
                mirror_path=TUI_PAPER_PORTFOLIO_FILE,
            )
            held_count = self._sync_holdings_watchlist()
            self._persist_profile_paths(portfolio_path=str(path))
            message = f"Imported portfolio {count} rows | holdings synced {held_count}"
        elif kind == "trades":
            backup_dir = self._backup_profile_targets([PAPER_TRADE_LOG_FILE, TUI_PAPER_TRADE_LOG_FILE])
            count = self._copy_import_dataframe(
                path,
                PAPER_TRADE_LOG_FILE,
                normalizer=_normalize_import_trade_log,
                mirror_path=TUI_PAPER_TRADE_LOG_FILE,
            )
            rebuilt_cash = calculate_cash_from_trade_log(INITIAL_CAPITAL, str(PAPER_TRADE_LOG_FILE))
            if rebuilt_cash is not None:
                state = load_runtime_state(str(PAPER_STATE_FILE))
                state["cash"] = float(rebuilt_cash)
                save_runtime_state(str(PAPER_STATE_FILE), state)
                tui_state = load_runtime_state(str(TUI_PAPER_STATE_FILE))
                tui_state["cash"] = float(rebuilt_cash)
                save_runtime_state(str(TUI_PAPER_STATE_FILE), tui_state)
            self._persist_profile_paths(trades_path=str(path))
            message = f"Imported trade log {count} rows"
        elif kind == "nav":
            backup_dir = self._backup_profile_targets([PAPER_NAV_LOG_FILE, TUI_PAPER_NAV_LOG_FILE])
            count = self._copy_import_dataframe(
                path,
                PAPER_NAV_LOG_FILE,
                normalizer=_normalize_import_nav_log,
                mirror_path=TUI_PAPER_NAV_LOG_FILE,
            )
            nav_log = _load_nav_log()
            if not nav_log.empty:
                try:
                    latest = nav_log.iloc[-1]
                    state = load_runtime_state(str(PAPER_STATE_FILE))
                    state["cash"] = float(latest.get("Cash") or state.get("cash") or 0.0)
                    state["daily_start_nav"] = float(latest.get("NAV") or state.get("daily_start_nav") or 0.0)
                    save_runtime_state(str(PAPER_STATE_FILE), state)
                    tui_state = load_runtime_state(str(TUI_PAPER_STATE_FILE))
                    tui_state["cash"] = state["cash"]
                    tui_state["daily_start_nav"] = state["daily_start_nav"]
                    save_runtime_state(str(TUI_PAPER_STATE_FILE), tui_state)
                except Exception:
                    pass
            self._persist_profile_paths(nav_path=str(path))
            message = f"Imported NAV log {count} rows"
        else:
            raise ValueError(f"Unsupported import kind: {kind}")
        self._populate_portfolio_and_risk()
        self._populate_trades_full()
        self._render_hedge_panel()
        self._populate_watchlist()
        self._populate_paper_profile_panel(self._stats)
        backup_note = f" | backup {backup_dir.name}" if backup_dir else ""
        return f"{message}{backup_note}"

    def _add_local_watchlist_entry(self, entry: dict) -> None:
        normalized = _normalize_watchlist_entry(entry)
        if not normalized:
            self._set_status("Unable to add watchlist item")
            return
        existing = {_watchlist_entry_key(item) for item in self._paper_watchlist}
        key = _watchlist_entry_key(normalized)
        label = str(normalized.get("label") or normalized.get("symbol") or key)
        if key in existing:
            self._set_status(f"{label} already in watchlist")
            return
        self._paper_watchlist.append(normalized)
        _save_watchlist(self._paper_watchlist)
        if self.trade_mode != "live" or str(normalized.get("kind") or "stock") != "stock":
            self._watchlist = list(self._paper_watchlist)
        self._populate_watchlist()
        self._set_status(f"Added {label} to watchlist")

    def _remove_local_watchlist_entry(self, entry: dict) -> None:
        key = _watchlist_entry_key(entry)
        label = str(entry.get("label") or entry.get("symbol") or key)
        updated = [item for item in self._paper_watchlist if _watchlist_entry_key(item) != key]
        if len(updated) == len(self._paper_watchlist):
            self._set_status(f"{label} not found in local watchlist")
            return
        self._paper_watchlist = updated
        _save_watchlist(self._paper_watchlist)
        self._populate_watchlist()
        self._set_status(f"Removed {label} from watchlist")

    def _watchlist_entry_from_rates_selection(self) -> Optional[dict]:
        filtered_rows, filtered_indicators, filtered_forex = self._get_filtered_rates_payload()
        try:
            dt = self.query_one("#dt-kalimati", DataTable)
            row_idx = dt.cursor_row
            if row_idx is not None and 0 <= row_idx < len(filtered_rows):
                row = filtered_rows[row_idx]
                label = str(row.get("name_english") or "").strip()
                return {
                    "kind": "commodity",
                    "key": f"commodity:{label}",
                    "label": label,
                    "unit": str(row.get("unit") or ""),
                }
        except Exception:
            pass
        try:
            dt = self.query_one("#dt-macro", DataTable)
            row_idx = dt.cursor_row
            if row_idx is not None and 0 <= row_idx < len(filtered_indicators):
                row = filtered_indicators[row_idx]
                label = str(row.get("item") or "").strip()
                return {
                    "kind": "macro",
                    "key": f"macro:{label}",
                    "label": label,
                    "group": str(row.get("group") or ""),
                }
        except Exception:
            pass
        try:
            dt = self.query_one("#dt-forex", DataTable)
            row_idx = dt.cursor_row
            if row_idx is not None and 0 <= row_idx < len(filtered_forex):
                row = filtered_forex[row_idx]
                code = str(row.get("currency_code") or "").strip().upper()
                return {
                    "kind": "forex",
                    "key": f"forex:{code}",
                    "label": code,
                    "currency_name": str(row.get("currency_name") or ""),
                }
        except Exception:
            pass
        return None
