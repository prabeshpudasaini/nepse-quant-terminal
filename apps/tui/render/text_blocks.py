from __future__ import annotations

import re
import textwrap
from urllib.parse import urlparse, unquote

from rich.text import Text

from apps.tui.theme import (
    AMBER, WHITE, LABEL, GAIN_HI, LOSS_HI, CYAN, YELLOW, PURPLE,
)


def _render_stock_report(report: dict) -> Text:
    """Render deterministic financial report for the lookup pane."""
    def _append_wrapped_block(
        target: Text,
        body: str,
        *,
        style: str = WHITE,
        indent: str = "  ",
        continuation: str = "  ",
        width: int = 44,
    ) -> None:
        content = str(body or "").strip()
        if not content:
            return
        wrapped = textwrap.fill(content, width=width, initial_indent=indent, subsequent_indent=continuation)
        target.append(f"{wrapped}\n", style=style)

    text = Text()
    signal = report.get("signal", "NO DATA")
    score = int(report.get("score", 0) or 0)
    score_text = "N/A" if signal == "NO DATA" and score <= 0 else f"{score}/100"
    signal_style = {
        "ACCUMULATE": f"bold {GAIN_HI}",
        "WATCH": f"bold {YELLOW}",
        "CAUTION": f"bold {LOSS_HI}",
        "NO DATA": LABEL,
    }.get(signal, WHITE)

    text.append("  FINANCIAL REPORT\n", style=f"bold {AMBER}")
    text.append("  Signal", style=LABEL)
    text.append(f"  {signal}", style=signal_style)
    text.append("   ", style=WHITE)
    text.append("Score", style=LABEL)
    text.append(f"  {score_text}\n", style=f"bold {WHITE}")
    _append_wrapped_block(
        text,
        report.get("summary", "No summary available."),
        continuation="    ",
        width=42,
    )

    company_profile = report.get("company_profile") or {}
    board = company_profile.get("board") or []
    officers = company_profile.get("officers") or []
    company_name = str(company_profile.get("company_name") or "").strip()
    if company_name or board or officers:
        text.append("\n  Company Profile\n", style=f"bold {AMBER}")
        if company_name:
            text.append("  Name   ", style=LABEL)
            text.append(f"{company_name}\n", style=WHITE)
        if board:
            text.append("  Board of Directors\n", style=LABEL)
            for item in board:
                name = str((item or {}).get("name") or "").strip()
                role = str((item or {}).get("role") or "").strip()
                if not name:
                    continue
                text.append("    ", style=WHITE)
                text.append(f"{name:<24}", style=WHITE)
                if role:
                    text.append(f" {role}", style=LABEL)
                text.append("\n", style=WHITE)
        if officers:
            text.append("  Officers\n", style=LABEL)
            for item in officers:
                name = str((item or {}).get("name") or "").strip()
                role = str((item or {}).get("role") or "").strip()
                if not name:
                    continue
                text.append("    ", style=WHITE)
                text.append(f"{name:<24}", style=WHITE)
                if role:
                    text.append(f" {role}", style=LABEL)
                text.append("\n", style=WHITE)

    snapshot = report.get("snapshot") or []
    visible_snapshot = []
    for label, value in snapshot:
        rendered = str(value or "").strip()
        if not rendered or rendered == "—":
            continue
        visible_snapshot.append((label, rendered))
    if visible_snapshot:
        text.append("\n  Snapshot\n", style=f"bold {CYAN}")
        for label, value in visible_snapshot[:8]:
            text.append(f"  {label:<12}", style=LABEL)
            text.append(f" {value}\n", style=WHITE)

    for title, items, style in [
        ("Bull Case", report.get("positives") or [], GAIN_HI),
        ("Risk Case", report.get("risks") or [], LOSS_HI),
        ("Monitor", report.get("monitors") or [], YELLOW),
    ]:
        if not items:
            continue
        text.append(f"\n  {title}\n", style=f"bold {style}")
        for item in items:
            wrapped = textwrap.fill(
                str(item),
                width=42,
                initial_indent="  • ",
                subsequent_indent="    ",
            )
            text.append(f"{wrapped}\n", style=WHITE)

    notes = (report.get("latest_notes") or "").strip()
    if notes:
        text.append("\n  Latest Notes\n", style=f"bold {PURPLE}")
        display_notes = notes[:260] + ("..." if len(notes) > 260 else "")
        _append_wrapped_block(text, display_notes)

    return text


def _render_lookup_intelligence(report: dict, symbol: str) -> Text:
    """Render symbol-scoped intelligence / supply-chain brief."""
    intel_payload = report.get("intelligence") or {}
    text = Text()

    def _append_wrapped(target: Text, value: str, *, indent: str = "  ", continuation: str = "  ", width: int = 40, style: str = WHITE) -> None:
        body = str(value or "").strip()
        if not body:
            return
        wrapped = textwrap.fill(body, width=width, initial_indent=indent, subsequent_indent=continuation)
        target.append(f"{wrapped}\n", style=style)

    headline = str(intel_payload.get("headline") or "").strip()
    text.append("  CORPORATE INTELLIGENCE\n", style=f"bold {AMBER}")
    if headline:
        _append_wrapped(text, headline, width=38)

    sections = intel_payload.get("sections") or []
    for section in sections:
        title = str((section or {}).get("title") or "").strip()
        rows = (section or {}).get("rows") or []
        bullets = (section or {}).get("bullets") or []
        if title:
            text.append(f"\n  {title}\n", style=f"bold {AMBER}")
        for label, value in rows:
            clean_label = str(label).strip()
            clean_value = str(value).strip()
            if not clean_value:
                continue
            text.append(f"  {clean_label}\n", style=LABEL)
            _append_wrapped(text, clean_value, indent="    ", continuation="    ", width=36)
        for item in bullets:
            _append_wrapped(text, str(item), indent="  • ", continuation="    ", width=38)

    if not headline and not sections:
        text.append("\n  Pipeline Ready\n", style=f"bold {AMBER}")
        text.append("  Supply Chain", style=LABEL)
        text.append("  Planned\n", style=WHITE)
        text.append("  Threat / Political", style=LABEL)
        text.append("  Planned\n", style=WHITE)
        text.append("  News Catalyst", style=LABEL)
        text.append("  Use the news/OSINT feed as source\n", style=WHITE)
        text.append("  Next Step", style=LABEL)
        text.append(f"  Add symbol-scoped risk, supplier, and route signals for {symbol}", style=WHITE)

    return text


def _headline_fallback_from_url(url: str) -> str:
    """Return a readable headline candidate from a URL, or empty string."""
    clean_url = str(url or "").strip()
    if not clean_url:
        return ""
    try:
        parsed = urlparse(clean_url)
    except Exception:
        return ""

    candidate = unquote((parsed.path or "").rstrip("/").split("/")[-1]).strip()
    if not candidate:
        return ""

    candidate = re.sub(r"\.(html?|aspx|php|jsp)$", "", candidate, flags=re.I)
    candidate = candidate.replace("-", " ").replace("_", " ")
    candidate = re.sub(r"\s+", " ", candidate).strip()
    candidate_lower = candidate.lower()

    generic_tokens = {
        "newsdetail", "newsdetails", "detail", "details", "article",
        "articles", "news", "story", "stories", "index",
    }
    if candidate_lower in generic_tokens or candidate_lower.startswith("newsdetail"):
        return ""
    if parsed.query and re.fullmatch(r"[a-z]+detail", candidate_lower):
        return ""
    if candidate.isdigit() or len(candidate) <= 5:
        return ""
    if not re.search(r"[A-Za-z]", candidate):
        return ""
    return candidate.title()
