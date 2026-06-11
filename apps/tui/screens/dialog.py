from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from apps.tui.io.orders_io import _format_sell_holdings_summary
from apps.tui.theme import LOSS_HI


class ModalDialog(ModalScreen[dict | None]):
    """Buy/Sell/Lookup modal dialog."""

    DEFAULT_CSS = """
    ModalDialog { align: center middle; }
    """

    def __init__(
        self,
        mode: str = "buy",
        *,
        initial_symbol: str = "",
        initial_shares: str = "",
        initial_price: str = "",
        default_slippage: str = "2.0",
        holdings_positions: list[dict] | None = None,
    ):
        super().__init__()
        self.mode = mode
        self.initial_symbol = initial_symbol
        self.initial_shares = initial_shares
        self.initial_price = initial_price
        self.default_slippage = default_slippage
        self.holdings_summary = _format_sell_holdings_summary(holdings_positions)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box", classes=f"dialog-shell dialog-{self.mode}"):
            title_map = {"buy": "BUY", "sell": "SELL", "lookup": "LOOKUP"}
            yield Static(title_map.get(self.mode, ""), classes="dialog-title")
            if self.mode == "buy":
                yield Static("Create a paper ticket for the daily order book.", id="dialog-kicker")
            elif self.mode == "sell":
                yield Static("Holdings available to sell", id="dialog-kicker")
                yield Static(self.holdings_summary, id="dialog-holdings")
            yield Input(id="inp-symbol", placeholder="Symbol e.g. NABIL")
            if self.mode != "lookup":
                yield Input(id="inp-shares",
                            placeholder="Shares" if self.mode == "buy" else "Shares (or all)")
                yield Input(id="inp-price", placeholder="Price (blank=last close)")
                yield Input(id="inp-slippage", placeholder="Slippage %", value=self.default_slippage)
            yield Static("", id="dialog-result")
            with Horizontal(id="dialog-buttons"):
                yield Button("Submit", id="btn-submit", variant="success")
                yield Button("Cancel", id="btn-cancel", variant="error")

    def on_mount(self) -> None:
        symbol = self.query_one("#inp-symbol", Input)
        symbol.value = self.initial_symbol
        if self.mode != "lookup":
            self.query_one("#inp-shares", Input).value = self.initial_shares
            self.query_one("#inp-price", Input).value = self.initial_price
        symbol.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-submit":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self):
        sym = self.query_one("#inp-symbol", Input).value.strip().upper()
        if not sym:
            self.query_one("#dialog-result", Static).update(
                Text("Symbol required", style=LOSS_HI))
            return
        if self.mode == "lookup":
            self.dismiss({"symbol": sym})
        else:
            shares = self.query_one("#inp-shares", Input).value.strip()
            price = self.query_one("#inp-price", Input).value.strip()
            slippage = self.query_one("#inp-slippage", Input).value.strip()
            if not shares:
                self.query_one("#dialog-result", Static).update(
                    Text("Shares required", style=LOSS_HI))
                return
            self.dismiss({"symbol": sym, "shares": shares, "price": price, "slippage": slippage})

    def key_escape(self) -> None:
        self.dismiss(None)
