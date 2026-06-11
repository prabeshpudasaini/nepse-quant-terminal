from __future__ import annotations

from rich.text import Text

from apps.tui.render.text_blocks import _headline_fallback_from_url
from apps.tui.theme import (
    _vol,
    GAIN_HI, GAIN, LOSS_HI, LOSS, WHITE, LABEL,
)


def _contains_non_ascii(text: str) -> bool:
    return bool(text and any(ord(c) > 127 for c in text[:20]))


def _news_display_headline(story: dict) -> str:
    """Return the best headline — prefer English, fall back to Nepali."""
    translated = str(story.get("_translated") or "").strip()
    if translated:
        return translated
    summary = str(story.get("summary") or "").strip()
    if summary and not _contains_non_ascii(summary):
        return summary
    translated_summary = str(story.get("_translated_summary") or "").strip()
    if translated_summary:
        return translated_summary
    url = str(story.get("url") or "").strip()
    if url:
        slug = _headline_fallback_from_url(url)
        if slug:
            return slug
    headline = str(story.get("canonical_headline") or "").strip()
    return headline or "Untitled story"


def _truncate_text(text: str, width: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)].rstrip() + "…"


# ── Color helpers for DataTable cells ─────────────────────────────────────────

def _chg_text(v: float) -> Text:
    s = "+" if v >= 0 else ""
    if v > 2:    c = GAIN_HI
    elif v > 0:  c = GAIN
    elif v < -2: c = LOSS_HI
    elif v < 0:  c = LOSS
    else:        c = WHITE
    return Text(f"{s}{v:.2f}%", style=c)

def _sym_text(s: str) -> Text:
    return Text(s, style=f"bold {WHITE}")

def _dim_text(s: str) -> Text:
    return Text(s, style=LABEL)

def _vol_text(v: float) -> Text:
    return Text(_vol(v), style=LABEL)

def _price_text(v: float) -> Text:
    return Text(f"{v:.1f}", style=WHITE)

def _pnl_color(v: float) -> str:
    return GAIN_HI if v > 0 else (LOSS_HI if v < 0 else WHITE)

def _npr_k(v: float) -> str:
    """Format NPR value compactly."""
    if abs(v) >= 1_000_000: return f"NPR {v/1_000_000:.2f}M"
    if abs(v) >= 1_000: return f"NPR {v/1_000:.1f}K"
    return f"NPR {v:.0f}"


def _format_compact_npr(value: float) -> str:
    return f"NPR {value:,.0f}"
