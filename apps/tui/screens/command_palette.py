from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from backend.quant_pro.dashboard_data import _db
from apps.tui.theme import AMBER, CYAN, DIM, GAIN_HI, LABEL, WHITE


class CommandPalette(ModalScreen[dict | None]):
    """Bloomberg-style GO bar. Type symbol name → lookup, tab name → switch, action → execute."""

    DEFAULT_CSS = """
    CommandPalette {
        align: center top;
        background: rgba(0, 0, 0, 0.80);
    }
    #cmd-box {
        width: 64;
        height: auto;
        max-height: 22;
        background: #090d12;
        border: solid #3f474f;
        margin-top: 2;
        padding: 0;
    }
    #cmd-header {
        height: 1;
        width: 100%;
        background: #090d12;
        layout: horizontal;
        padding: 0 1;
    }
    #cmd-header-label {
        width: auto;
        height: 1;
        color: #f2b94b;
        text-style: bold;
        padding: 0 1 0 0;
    }
    #cmd-input {
        width: 1fr;
        background: #0c1116;
        border: none;
        color: #e8edf3;
        padding: 0 1;
        height: 1;
    }
    #cmd-input:focus {
        background: #0c1116;
        color: #fff6de;
        border: none;
    }
    #cmd-results {
        height: auto;
        max-height: 18;
        width: 100%;
        background: #090d12;
        padding: 0;
        border-top: solid #1a1f25;
    }
    .cmd-row {
        height: 1;
        width: 100%;
        padding: 0 1;
        color: #6b7785;
    }
    .cmd-row-selected {
        height: 1;
        width: 100%;
        padding: 0 1;
        background: #111820;
        color: #f2b94b;
    }
    #cmd-hint {
        height: 1;
        width: 100%;
        background: #090d12;
        color: #3a4450;
        padding: 0 1;
        border-top: solid #1a1f25;
    }
    """

    _selected: int = 0
    _items: list[dict] = []
    _filtered: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="cmd-box"):
            with Horizontal(id="cmd-header"):
                yield Static("GO", id="cmd-header-label")
                yield Input(id="cmd-input", placeholder="symbol, tab, or action")
            yield Vertical(id="cmd-results")
            yield Static(" ↑↓ Navigate  ENTER Execute  ESC Close", id="cmd-hint")

    def on_mount(self) -> None:
        self._build_items()
        self._selected = 0
        self._filter("")
        self.query_one("#cmd-input", Input).focus()

    def _build_items(self) -> None:
        self._items = []
        # Tab navigation
        tabs = [
            ("1 MARKET", "tab", "market", "Market overview — gainers, losers, volume"),
            ("2 PORTFOLIO", "tab", "portfolio", "Portfolio holdings, NAV, risk"),
            ("3 SIGNALS", "tab", "signals", "Trading signals, screener & calendar"),
            ("4 LOOKUP", "tab", "lookup", "Stock lookup & charts"),
            ("5 AGENTS", "tab", "agents", "AI agent analysis & chat"),
            ("6 ORDERS", "tab", "orders", "Order management"),
            ("7 WATCHLIST", "tab", "watchlist", "Your watched stocks"),
            ("8 RATES & COMMODITIES", "tab", "kalimati", "FX, metals and local commodity prices"),
            ("9 ACCOUNT", "tab", "account", "Paper account profiles and runtime"),
            ("0 STRATEGIES", "tab", "strategies", "Saved strategies, bindings and backtests"),
        ]
        for name, kind, target, desc in tabs:
            self._items.append({"name": name, "kind": kind, "target": target, "desc": desc})
        # Actions
        actions = [
            ("BUY", "action", "buy", "Open buy dialog"),
            ("SELL", "action", "sell", "Open sell dialog"),
            ("REFRESH", "action", "refresh", "Refresh all market data"),
            ("AGENT", "action", "agent", "Run AI agent analysis"),
        ]
        for name, kind, target, desc in actions:
            self._items.append({"name": name, "kind": kind, "target": target, "desc": desc})
        # Stock symbols from market data (will be populated dynamically)
        try:
            conn = _db()
            import sqlite3
            syms = [r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM stock_prices WHERE symbol != 'NEPSE' "
                "ORDER BY symbol").fetchall()]
            conn.close()
            for sym in syms:
                self._items.append({"name": sym, "kind": "stock", "target": sym,
                                    "desc": "Look up stock"})
        except Exception:
            pass

    def _filter(self, query: str) -> None:
        q = query.strip().upper()
        if not q:
            # Show tabs and actions first
            self._filtered = [i for i in self._items if i["kind"] != "stock"][:15]
        else:
            # Fuzzy match: items starting with query first, then contains
            starts = [i for i in self._items if i["name"].upper().startswith(q)]
            contains = [i for i in self._items if q in i["name"].upper() and i not in starts]
            desc_match = [i for i in self._items if q in i["desc"].upper()
                          and i not in starts and i not in contains]
            self._filtered = (starts + contains + desc_match)[:15]
        self._selected = min(self._selected, max(0, len(self._filtered) - 1))
        self._render_results()

    def _render_results(self) -> None:
        container = self.query_one("#cmd-results", Vertical)
        container.remove_children()
        for i, item in enumerate(self._filtered):
            kind_icon = {"tab": "◧", "action": "▶", "stock": "◆"}.get(item["kind"], "·")
            kind_color = {"tab": CYAN, "action": GAIN_HI, "stock": AMBER}.get(item["kind"], LABEL)
            t = Text()
            if i == self._selected:
                t.append(f"  ▸ {kind_icon} ", style=f"bold {kind_color}")
                t.append(f"{item['name']:<16}", style=f"bold {WHITE}")
                t.append(item["desc"], style=LABEL)
                cls = "cmd-row-selected"
            else:
                t.append(f"    {kind_icon} ", style=kind_color)
                t.append(f"{item['name']:<16}", style=DIM)
                t.append(item["desc"], style=DIM)
                cls = "cmd-row"
            container.mount(Static(t, classes=cls))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "cmd-input":
            self._selected = 0
            self._filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "cmd-input":
            self._execute()

    def key_up(self) -> None:
        self._selected = max(0, self._selected - 1)
        self._render_results()

    def key_down(self) -> None:
        self._selected = min(len(self._filtered) - 1, self._selected + 1)
        self._render_results()

    def key_escape(self) -> None:
        self.dismiss(None)

    def _execute(self) -> None:
        if not self._filtered:
            # Treat input as stock symbol
            val = self.query_one("#cmd-input", Input).value.strip().upper()
            if val:
                self.dismiss({"kind": "stock", "target": val})
            return
        item = self._filtered[self._selected]
        self.dismiss(item)
