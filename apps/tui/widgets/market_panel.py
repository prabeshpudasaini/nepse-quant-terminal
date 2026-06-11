from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable, Static

from apps.tui.theme import AMBER


class MarketPanel(Widget):
    """Titled panel with a DataTable inside."""

    def __init__(self, panel_title: str = "", title_color: str = AMBER, **kw):
        super().__init__(**kw)
        self._panel_title = panel_title
        self._title_color = title_color

    def compose(self) -> ComposeResult:
        yield Static(self._panel_title, classes="panel-title")
        yield DataTable(zebra_stripes=True, cursor_type="row")

    def set_data(self, columns: list[tuple[str, str]], rows: list[list]):
        dt = self.query_one(DataTable)
        dt.clear(columns=True)
        for label, key in columns:
            dt.add_column(label, key=key)
        for row in rows:
            dt.add_row(*row)

    def update_title(self, title: str):
        self.query_one(".panel-title", Static).update(title)
