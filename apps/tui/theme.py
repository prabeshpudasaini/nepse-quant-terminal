from __future__ import annotations

from rich.text import Text

# ── palette ───────────────────────────────────────────────────────────────────
AMBER   = "#ffaf00"
WHITE   = "#e8e8e8"
DIM     = "#555555"
LABEL   = "#888888"
GAIN_HI = "#00ff7f"
GAIN    = "#00cc60"
LOSS_HI = "#ff4545"
LOSS    = "#cc3333"
CYAN    = "#00cfff"
YELLOW  = "#ffd700"
PURPLE  = "#c87fff"
BLUE    = "#4d9fff"


def _pct(v: float, bold: bool = False) -> Text:
    s = "+" if v >= 0 else ""
    c = (GAIN_HI if v > 2 else GAIN) if v > 0 else (LOSS_HI if v < -2 else LOSS) if v < 0 else WHITE
    return Text(f"{s}{v:.2f}%", style=f"bold {c}" if bold else c)


def _vol(v: float) -> str:
    if v >= 1_000_000: return f"{v/1_000_000:.2f}M"
    if v >= 1_000:     return f"{v/1_000:.0f}K"
    return str(int(v))


def _npr(v: float) -> Text:
    s = "+" if v >= 0 else ""
    c = GAIN_HI if v > 0 else (LOSS_HI if v < 0 else WHITE)
    a = abs(v)
    t = f"{s}NPR {v/1_000_000:.2f}M" if a >= 1_000_000 else \
        f"{s}NPR {v/1_000:.1f}K"     if a >= 1_000 else f"{s}NPR {v:.0f}"
    return Text(t, style=f"bold {c}")
