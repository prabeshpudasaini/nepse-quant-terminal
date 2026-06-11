from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class LookupScreen(ModalScreen[dict | None]):
    """Compact symbol lookup prompt."""

    DEFAULT_CSS = """
    LookupScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.82);
    }
    #lookup-box {
        width: 38;
        height: 3;
        border: solid #3f474f;
        background: #090d12;
        padding: 0 1;
        layout: horizontal;
        align: left middle;
    }
    #lookup-label {
        color: #f2b94b;
        text-style: bold;
        height: 1;
        width: auto;
        padding: 0 1 0 0;
    }
    #lookup-input {
        width: 1fr;
        height: 1;
        background: #0c1116;
        border: none;
        color: #e8edf3;
        padding: 0 1;
    }
    #lookup-input:focus {
        background: #0c1116;
        color: #fff6de;
        border: none;
    }
    #lookup-hint {
        height: 1;
        width: auto;
        color: #4a5562;
        padding: 0 0 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="lookup-box"):
            yield Static("LOOKUP", id="lookup-label")
            yield Input(id="lookup-input", placeholder="NABIL")
            yield Static("↵ GO  ESC ✕", id="lookup-hint")

    def on_mount(self) -> None:
        self.query_one("#lookup-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        val = self.query_one("#lookup-input", Input).value.strip().upper()
        if val:
            self.dismiss({"symbol": val})
        else:
            self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)
