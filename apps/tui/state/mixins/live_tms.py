"""Live-brokerage stubs + the live-watchlist worker trio + their reader.

Live trading is a stub in the public build, but the watchlist-sync workers are
real. The 3 @work watchlist workers and their single call_from_thread reader
(_set_watchlist_from_tms_snapshot) live together here as the co-location anchor.

build_tui_control_plane and save_tms_snapshot are imported lazily inside the
method bodies that need them (never at module top) to avoid a partial-init
circular-import trap through command_service. These paths are inert in the unit
suite.
"""

import time

from textual import work

from apps.tui.io.csv_import import _merge_watchlist_entries
from apps.tui.io.watchlist_io import _dedupe_watchlist_entries, _stock_watchlist_entry


class LiveTMSMixin:
    @work(thread=True)
    def _submit_tms_order(self, result: dict, action: str, agent_reason: str) -> None:
        """Live TMS order submission is disabled in the public paper build."""
        self.app.call_from_thread(self._set_status, "Only paper trading is supported in this build.")

    @work(thread=True)
    def _submit_live_order(self, action: str, sym: str, qty: int, price: float) -> None:
        """Live TMS order submission is disabled in the public paper build."""
        self.app.call_from_thread(self._set_status, "Only paper trading is supported in this build.")

    def _set_watchlist_from_tms_snapshot(self, snapshot: dict) -> bool:
        if not isinstance(snapshot, dict):
            return False
        raw_items = snapshot.get("items")
        if raw_items is None:
            return False
        symbols: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            sym = str((item or {}).get("symbol") or "").strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            symbols.append(sym)
        self._live_watchlist = _dedupe_watchlist_entries([_stock_watchlist_entry(sym) for sym in symbols])
        local_extras = [item for item in self._paper_watchlist if str(item.get("kind") or "stock") != "stock"]
        self._watchlist = _merge_watchlist_entries(self._live_watchlist, local_extras)
        bundle = dict(getattr(self, "_tms_bundle", None) or {})
        bundle["watchlist"] = snapshot
        self._tms_bundle = bundle
        try:
            from backend.quant_pro.tms_audit import save_tms_snapshot

            save_tms_snapshot("tms_watchlist", dict(snapshot), status="ok")
        except Exception:
            pass
        return True

    @work(thread=True)
    def _watchlist_add_live(self, sym: str) -> None:
        from backend.quant_pro.control_plane.command_service import build_tui_control_plane

        self.app.call_from_thread(self._set_status, f"TMS monitor  |  Adding {sym} to broker watchlist...")
        try:
            result = build_tui_control_plane(self).sync_watchlist(action="add", symbol=sym)
            snapshot = dict(result.payload or {})
            self.app.call_from_thread(self._set_watchlist_from_tms_snapshot, snapshot)
            self.app.call_from_thread(self._populate_watchlist)
            self.app.call_from_thread(self._set_status, f"Added {sym} to TMS watchlist")
        except Exception as e:
            self.app.call_from_thread(self._set_status, self._summarize_tms_watchlist_error(e, "add"))

    @work(thread=True)
    def _watchlist_remove_live(self, sym: str) -> None:
        from backend.quant_pro.control_plane.command_service import build_tui_control_plane

        self.app.call_from_thread(self._set_status, f"TMS monitor  |  Removing {sym} from broker watchlist...")
        try:
            result = build_tui_control_plane(self).sync_watchlist(action="remove", symbol=sym)
            snapshot = dict(result.payload or {})
            self.app.call_from_thread(self._set_watchlist_from_tms_snapshot, snapshot)
            self.app.call_from_thread(self._populate_watchlist)
            self.app.call_from_thread(self._set_status, f"Removed {sym} from TMS watchlist")
        except Exception as e:
            self.app.call_from_thread(self._set_status, self._summarize_tms_watchlist_error(e, "remove"))

    @work(thread=True)
    def _refresh_watchlist_live(self, force: bool = False) -> None:
        if self.trade_mode != "live" or not self.tms_service:
            return
        if self._tms_watchlist_refresh_inflight:
            return
        now = time.monotonic()
        if not force and (now - float(getattr(self, "_last_tms_watchlist_fetch_at", 0.0) or 0.0)) < 90.0:
            return
        self._tms_watchlist_refresh_inflight = True
        try:
            from backend.quant_pro.control_plane.command_service import build_tui_control_plane

            self.app.call_from_thread(self._set_status, "TMS monitor  |  Syncing broker watchlist...")
            result = build_tui_control_plane(self).sync_watchlist(action="fetch")
            snapshot = dict(result.payload or {})
            self._last_tms_watchlist_fetch_at = time.monotonic()
            self.app.call_from_thread(self._set_watchlist_from_tms_snapshot, snapshot)
            self.app.call_from_thread(self._populate_watchlist)
            symbols = snapshot.get("symbols") or []
            self.app.call_from_thread(
                self._set_status,
                f"TMS monitor  |  Broker watchlist synced ({len(symbols)} symbols)",
            )
        except Exception as e:
            self._last_tms_watchlist_fetch_at = time.monotonic()
            self.app.call_from_thread(self._set_status, self._summarize_tms_watchlist_error(e, "sync"))
        finally:
            self._tms_watchlist_refresh_inflight = False
