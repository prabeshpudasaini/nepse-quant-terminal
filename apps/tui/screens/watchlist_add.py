from __future__ import annotations

from rich.markup import escape as _escape_markup
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from backend.quant_pro.dashboard_data import _db
from backend.market.kalimati_market import get_kalimati_display_rows
from apps.tui.io.fetchers import _fetch_gold_silver_prices, _fetch_nrb_forex_rates
from apps.tui.io.watchlist_io import _stock_watchlist_entry
from apps.tui.theme import AMBER, CYAN, DIM, GAIN_HI, LABEL, WHITE


class WatchlistAddScreen(ModalScreen[dict | str | None]):
    """Bloomberg-style watchlist picker with live suggestions."""

    DEFAULT_CSS = """
    WatchlistAddScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.88);
    }
    #wl-add-box {
        width: 76;
        height: auto;
        max-height: 24;
        border: solid #5d6670;
        background: #090b0e;
        padding: 0;
    }
    #wl-add-title {
        height: 1;
        width: 100%;
        background: #17191d;
        color: #ffaf00;
        text-style: bold;
        padding: 0 2;
    }
    #wl-add-row {
        width: 100%;
        height: 3;
        background: #101419;
        border-top: solid #232a31;
        border-bottom: solid #232a31;
        padding: 0 2;
        layout: horizontal;
        content-align: left middle;
    }
    #wl-add-label {
        height: 1;
        width: 8;
        padding-right: 2;
        color: #8fa0b1;
        text-style: bold;
        content-align: left middle;
    }
    #wl-add-input {
        width: 1fr;
        height: 1;
        background: #101419;
        border: none;
        color: #f6fbff;
        padding: 0 1;
        content-align: left middle;
    }
    #wl-add-input:focus {
        background: #151a20;
        color: #ffffff;
        border: none;
    }
    #wl-add-query {
        height: 1;
        width: 100%;
        background: #0d1014;
        color: #8fa0b1;
        padding: 0 2;
        border-bottom: solid #1b2128;
    }
    #wl-add-results {
        height: auto;
        max-height: 17;
        width: 100%;
        background: #0b0d10;
        padding: 1 0;
    }
    .wl-add-row-item {
        height: 1;
        width: 100%;
        padding: 0 2;
        color: #7e8791;
    }
    .wl-add-row-selected {
        height: 1;
        width: 100%;
        padding: 0 2;
        background: #191d22;
        color: #ffcf70;
        text-style: bold;
    }
    #wl-add-hint {
        height: 1;
        width: 100%;
        background: #101318;
        color: #56616d;
        padding: 0 2;
        border-top: solid #1b2128;
    }
    """

    _selected: int = 0
    _items: list[dict] = []
    _filtered: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="wl-add-box"):
            yield Static("ADD TO WATCHLIST", id="wl-add-title")
            with Horizontal(id="wl-add-row"):
                yield Static("SEARCH", id="wl-add-label")
                yield Input(id="wl-add-input", placeholder="NABIL, USD, Gold / Tola, Petrol...")
            yield Static("", id="wl-add-query")
            yield Vertical(id="wl-add-results")
            yield Static("↑↓ Navigate   ENTER Add   ESC Cancel   Stocks, forex, macro, commodities", id="wl-add-hint")

    def on_mount(self) -> None:
        self._build_items()
        self._selected = 0
        self._filter("")
        self._update_query_preview("")
        self.query_one("#wl-add-input", Input).focus()

    def _build_items(self) -> None:
        self._items = []
        try:
            conn = _db()
            symbols = [r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM stock_prices WHERE symbol != 'NEPSE' ORDER BY symbol"
            ).fetchall()]
            conn.close()
            for sym in symbols:
                self._items.append({
                    "name": sym,
                    "kind": "stock",
                    "target": _stock_watchlist_entry(sym),
                    "desc": "NEPSE stock",
                })
        except Exception:
            pass

        app = self.app
        kalimati_rows = list(getattr(app, "_kalimati_rows", []) or [])
        macro_rates = dict(getattr(app, "_macro_rates", {}) or {})
        if not kalimati_rows:
            try:
                kalimati_rows = list(get_kalimati_display_rows() or [])
            except Exception:
                kalimati_rows = []
        if not macro_rates:
            try:
                metals = _fetch_gold_silver_prices()
                indicators: list[dict] = []
                if metals:
                    gold = float(metals.get("gold_per_tola") or 0)
                    silver = float(metals.get("silver_per_tola") or 0)
                    if gold > 0:
                        indicators.append({"item": "Gold / Tola", "group": "Metals", "unit": "NPR/tola"})
                    if silver > 0:
                        indicators.append({"item": "Silver / Tola", "group": "Metals", "unit": "NPR/tola"})
                forex_rows = _fetch_nrb_forex_rates(("USD", "EUR", "GBP", "INR", "CNY", "JPY"))
                macro_rates = {"indicators": indicators, "forex_rows": forex_rows}
            except Exception:
                macro_rates = {}

        for row in kalimati_rows:
            label = str(row.get("name_english") or "").strip()
            if not label:
                continue
            self._items.append({
                "name": label,
                "kind": "commodity",
                "target": {
                    "kind": "commodity",
                    "key": f"commodity:{label}",
                    "label": label,
                    "unit": str(row.get("unit") or ""),
                },
                "desc": f"Kalimati commodity  {row.get('unit') or ''}".strip(),
            })

        for row in list(macro_rates.get("indicators", []) or []):
            label = str(row.get("item") or "").strip()
            if not label:
                continue
            self._items.append({
                "name": label,
                "kind": "macro",
                "target": {
                    "kind": "macro",
                    "key": f"macro:{label}",
                    "label": label,
                    "group": str(row.get("group") or ""),
                },
                "desc": f"{row.get('group') or 'Macro'}  {row.get('unit') or ''}".strip(),
            })

        for row in list(macro_rates.get("forex_rows", []) or []):
            code = str(row.get("currency_code") or "").strip().upper()
            name = str(row.get("currency_name") or "").strip()
            if not code:
                continue
            self._items.append({
                "name": code,
                "kind": "forex",
                "target": {
                    "kind": "forex",
                    "key": f"forex:{code}",
                    "label": code,
                    "currency_name": name,
                },
                "desc": name or "NRB forex rate",
            })

    def _filter(self, query: str) -> None:
        q = query.strip().upper()
        if not q:
            priority = ("stock", "forex", "macro", "commodity")
            ordered: list[dict] = []
            for kind in priority:
                ordered.extend([item for item in self._items if item["kind"] == kind][:4])
            self._filtered = ordered[:12]
        else:
            exact = [item for item in self._items if item["name"].upper() == q]
            starts = [item for item in self._items if item["name"].upper().startswith(q) and item not in exact]
            contains = [item for item in self._items if q in item["name"].upper() and item not in exact and item not in starts]
            desc = [item for item in self._items if q in item["desc"].upper() and item not in exact and item not in starts and item not in contains]
            self._filtered = (exact + starts + contains + desc)[:12]
        self._selected = min(self._selected, max(0, len(self._filtered) - 1))
        self._render_results()

    def _render_results(self) -> None:
        container = self.query_one("#wl-add-results", Vertical)
        container.remove_children()
        if not self._filtered:
            container.mount(Static(Text("  No matches", style=DIM), classes="wl-add-row-item"))
            return
        kind_icon = {"stock": "◆", "forex": "FX", "macro": "●", "commodity": "◧"}
        kind_style = {"stock": AMBER, "forex": CYAN, "macro": GAIN_HI, "commodity": WHITE}
        for idx, item in enumerate(self._filtered):
            t = Text()
            icon = kind_icon.get(item["kind"], "·")
            color = kind_style.get(item["kind"], LABEL)
            if idx == self._selected:
                t.append(f"  ▸ {icon} ", style=f"bold {color}")
                t.append(f"{item['name']:<20}", style=f"bold {WHITE}")
                t.append(item["desc"][:42], style=LABEL)
                cls = "wl-add-row-selected"
            else:
                t.append(f"    {icon} ", style=color)
                t.append(f"{item['name']:<20}", style=DIM)
                t.append(item["desc"][:42], style=DIM)
                cls = "wl-add-row-item"
            container.mount(Static(t, classes=cls))

    def _update_query_preview(self, query: str) -> None:
        widget = self.query_one("#wl-add-query", Static)
        clean = query.strip()
        if clean:
            widget.update(Text.from_markup(
                f"[#6e7c89]QUERY[/] [bold {WHITE}]{_escape_markup(clean)}[/]   "
                f"[#6e7c89]MATCHES[/] [bold {AMBER}]{len(self._filtered)}[/]"
            ))
        else:
            widget.update(Text.from_markup(
                f"[#6e7c89]QUERY[/] [#55616d]Start typing to search stocks, FX, macro and commodities[/]"
            ))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "wl-add-input":
            self._selected = 0
            self._filter(event.value)
            self._update_query_preview(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "wl-add-input":
            self._submit()

    def key_up(self) -> None:
        self._selected = max(0, self._selected - 1)
        self._render_results()

    def key_down(self) -> None:
        self._selected = min(len(self._filtered) - 1, self._selected + 1)
        self._render_results()

    def _submit(self) -> None:
        raw = self.query_one("#wl-add-input", Input).value.strip()
        if self._filtered:
            self.dismiss(self._filtered[self._selected]["target"])
            return
        if raw:
            self.dismiss(raw.upper())
        else:
            self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)
