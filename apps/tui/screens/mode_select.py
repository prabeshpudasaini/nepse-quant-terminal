from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class ModeSelectScreen(ModalScreen):
    """Startup screen for the paper trading workspace."""

    DEFAULT_CSS = """
    ModeSelectScreen {
        align: center middle;
        background: #060606;
    }
    #mode-box {
        width: 64;
        height: auto;
        max-height: 20;
        background: #0a0a0a;
        border: none;
        padding: 0;
    }
    #mode-header {
        height: 4; width: 100%; padding: 1 2;
        background: #0a0a0a;
    }
    .mode-brand {
        width: 100%; text-align: center;
        color: #888888; height: 1;
    }
    .mode-version {
        width: 100%; text-align: center;
        color: #5d6670; height: 1;
    }
    #mode-options {
        height: auto; width: 100%;
        padding: 1 3;
        background: #0a0a0a;
    }
    .mode-row {
        width: 100%; height: 1;
        padding: 0 2;
        margin: 0 0;
    }
    #mode-footer {
        height: 3; width: 100%;
        padding: 1 2;
        background: #0a0a0a;
    }
    .mode-keys {
        width: 100%; text-align: center;
        height: auto;
    }
    """

    _selected: int = 0
    _options = [
        ("paper", "PAPER", "Paper trading workspace and local portfolio"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="mode-box"):
            with Vertical(id="mode-header"):
                yield Static(
                    Text.from_markup(
                        "[bold #ffaf00]N E P S E[/]  [bold #e8e8e8]Q U A N T[/]"
                    ),
                    classes="mode-brand",
                )
                yield Static(
                    Text.from_markup("[#5d6670]Terminal v3  ◆  Paper Trading[/]"),
                    classes="mode-version",
                )
            with Vertical(id="mode-options"):
                yield Static("", id="mode-opt-0", classes="mode-row")
            with Vertical(id="mode-footer"):
                yield Static(
                    Text.from_markup(
                        "[bold #ffaf00]↑↓[/][#555555] select   [/]"
                        "[bold #ffaf00]ENTER[/][#555555] confirm   [/]"
                        "[bold #ffaf00]Q[/][#555555] quit[/]"
                    ),
                    classes="mode-keys",
                )

    def on_mount(self) -> None:
        self._render_options()

    def _render_options(self) -> None:
        for i, (key, label, desc) in enumerate(self._options):
            widget = self.query_one(f"#mode-opt-{i}", Static)
            if i == self._selected:
                t = Text()
                t.append("  ▶ ", style="bold #ffaf00")
                t.append(f"{label:<10}", style="bold #e8e8e8")
                t.append(desc, style="#888888")
                widget.update(t)
            else:
                t = Text()
                t.append("    ", style="")
                t.append(f"{label:<10}", style="#555555")
                t.append(desc, style="#333333")
                widget.update(t)

    def key_up(self) -> None:
        self._selected = max(0, self._selected - 1)
        self._render_options()

    def key_down(self) -> None:
        self._selected = min(len(self._options) - 1, self._selected + 1)
        self._render_options()

    def key_enter(self) -> None:
        self.dismiss(self._options[self._selected][0])

    def key_q(self) -> None:
        self.app.exit()

    def key_escape(self) -> None:
        self.app.exit()
