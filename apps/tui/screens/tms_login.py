from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


def load_tms_settings() -> dict:
    return {}


class TMSLoginScreen(ModalScreen):
    """Credential entry screen for live TMS auto-login."""

    DEFAULT_CSS = """
    TMSLoginScreen {
        align: center middle;
        background: #060606;
    }
    #tms-box {
        width: 56;
        height: auto;
        max-height: 18;
        background: #0a0a0a;
        border: tall #333333;
        padding: 1 2;
    }
    .tms-title {
        width: 100%; text-align: center;
        color: #ffaf00; text-style: bold;
        height: 1; margin-bottom: 1;
    }
    .tms-label {
        width: 100%; height: 1;
        color: #666666; padding: 0 1;
    }
    .tms-input {
        width: 100%;
        background: #111111;
        border: none;
        border-bottom: solid #222222;
        color: #e8e8e8;
        margin-bottom: 1;
    }
    .tms-input:focus {
        border-bottom: solid #ffaf00;
    }
    .tms-status {
        width: 100%; text-align: center;
        height: 1; color: #666666;
        margin-top: 1;
    }
    .tms-hint {
        width: 100%; text-align: center;
        height: 1; margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="tms-box"):
            yield Static(
                Text.from_markup("[bold #ffaf00]TMS19 LOGIN[/]"),
                classes="tms-title",
            )
            yield Static("Client ID / Username", classes="tms-label")
            yield Input(id="tms-user", placeholder="e.g. 12345678", classes="tms-input")
            yield Static("Password", classes="tms-label")
            yield Input(id="tms-pass", placeholder="", password=True, classes="tms-input")
            yield Static("", id="tms-status", classes="tms-status")
            yield Static(
                Text.from_markup(
                    "[bold #ffaf00]ENTER[/][#555555] login   [/]"
                    "[bold #ffaf00]ESC[/][#555555] back[/]"
                ),
                classes="tms-hint",
            )

    def on_mount(self) -> None:

        settings = load_tms_settings()
        user_env = settings.username or ""
        pass_env = settings.password or ""
        if user_env:
            self.query_one("#tms-user", Input).value = user_env
        if pass_env:
            self.query_one("#tms-pass", Input).value = pass_env
        if user_env and pass_env:
            # Both pre-filled, focus submit hint
            self.query_one("#tms-pass", Input).focus()
        else:
            self.query_one("#tms-user", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "tms-user":
            self.query_one("#tms-pass", Input).focus()
        elif event.input.id == "tms-pass":
            self._do_login()

    def key_escape(self) -> None:
        self.dismiss(None)

    def _do_login(self) -> None:
        user = self.query_one("#tms-user", Input).value.strip()
        pwd = self.query_one("#tms-pass", Input).value.strip()
        if not user or not pwd:
            self.query_one("#tms-status", Static).update(
                Text("Enter both client ID and password", style="#ff4444")
            )
            return
        self.query_one("#tms-status", Static).update(
            Text("Connecting to TMS19...", style="#ffaf00")
        )
        self.dismiss({"username": user, "password": pwd})
