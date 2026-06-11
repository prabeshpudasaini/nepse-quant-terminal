from __future__ import annotations

import pandas as pd
from rich.text import Text

from apps.tui.theme import (
    DIM, LABEL, GAIN, LOSS_HI,
)

# Injected by dashboard_tui after import; the resampler still lives in
# dashboard_tui until the intraday helpers are extracted.
_resample_ohlcv = None


def _render_candlestick_chart(df: pd.DataFrame, width: int = 120, height: int = 24,
                               timeframe: str = "D") -> Text:
    """Render candlestick chart using py-candlestick-chart library.

    Professional quality candles with proper wicks, bodies, and scaling.
    Supports D/W/M/Y/I timeframes via resampling.
    Adds date labels on the X-axis below the chart.
    """
    from candlestick_chart import Candle, Chart, constants

    if df.empty or len(df) < 2:
        return Text("  No data for chart", style=LABEL)

    rows = _resample_ohlcv(df, timeframe).sort_values("date").reset_index(drop=True)
    if rows.empty or len(rows) < 2:
        return Text("  Insufficient data for chart", style=LABEL)

    # Overall change for title
    first_close = float(rows.iloc[0]["close"])
    last_close = float(rows.iloc[-1]["close"])
    total_chg = (last_close - first_close) / first_close * 100 if first_close else 0

    chart_width = max(width, 40)

    # Build candles
    candles = []
    for _, r in rows.iterrows():
        candles.append(Candle(
            open=float(r["open"]), high=float(r["high"]),
            low=float(r["low"]), close=float(r["close"]),
            volume=float(r.get("volume", 0)),
        ))

    # Tighten library layout — reduce wasted vertical space
    constants.MARGIN_TOP = 1    # default 3 → 1 (less empty rows above candles)
    constants.Y_AXIS_SPACING = 3  # default 4 → 3 (denser price labels)

    # Chart title with timeframe + change
    tf_labels = {"D": "Daily", "W": "Weekly", "M": "Monthly", "Y": "Yearly", "I": "Intraday"}
    tf_name = tf_labels.get(timeframe, "Daily")
    chg_sign = "+" if total_chg >= 0 else ""
    title = f"{tf_name}  {chg_sign}{total_chg:.2f}%"

    chart = Chart(candles, title=title, width=chart_width, height=max(height, 8))

    # Colors: teal green bull, red bear
    chart.set_bull_color(38, 166, 154)
    chart.set_bear_color(239, 83, 80)
    chart.set_vol_bull_color(38, 166, 154)
    chart.set_vol_bear_color(239, 83, 80)

    # Disable volume pane — saves vertical space, volume shown in header already
    chart.set_volume_pane_enabled(False)

    # Clean up info labels
    chart.set_label("average", "")
    chart.set_label("volume", "")
    chart.set_label("currency", "NPR")

    # Render chart to ANSI string
    ansi_str = chart._render()

    # Strip the library's bottom cruft (scrollbar, date axis, info line)
    # Keep only lines up to and including the last candle/Y-axis line
    ansi_lines = ansi_str.split('\n')

    # Find last line that contains Y-axis price labels or candle characters
    # The library's own date axis and info line come after the candle area
    # Info line pattern: contains "Price:" or "Highest:" or "Lowest:" or "Var.:"
    # Library date axis: contains only spaces, digits, dashes, and month names
    cut_idx = len(ansi_lines)
    for i in range(len(ansi_lines) - 1, -1, -1):
        stripped = ansi_lines[i].strip()
        if not stripped:
            cut_idx = i
            continue
        # Detect library info line (contains Price:/Highest:/Lowest:/Var.)
        plain = stripped.replace('\x1b', '')  # rough strip of ANSI for detection
        if any(kw in plain for kw in ['Price:', 'Highest:', 'Lowest:', 'Var.:']):
            cut_idx = i
            continue
        # Detect library date axis (mostly dashes, digits, month abbreviations)
        # and scrollbar lines (box drawing chars like ─ ┬ └ ┘ █ ░)
        if all(c in ' ─┬└┘┼│░▓█▒▄▀0123456789-' for c in stripped):
            cut_idx = i
            continue
        break  # hit a real candle/axis line, stop

    candle_lines = '\n'.join(ansi_lines[:cut_idx])

    # Build our own date axis labels
    y_axis_area = constants.WIDTH
    margin_r = constants.MARGIN_RIGHT if not constants.Y_AXIS_ON_THE_RIGHT else 0
    candle_area = chart_width - y_axis_area - margin_r
    n_visible = min(len(rows), candle_area)
    visible_rows = rows.tail(n_visible).reset_index(drop=True)
    if timeframe == "I":
        labels = [pd.Timestamp(d).strftime("%H:%M") for d in pd.to_datetime(visible_rows["date"])]
        label_len = 5
    else:
        labels = [str(d)[:10] for d in visible_rows["date"]]
        label_len = 10

    date_axis = Text()
    if n_visible >= 2:
        min_gap = label_len + 2
        max_labels = max(1, candle_area // min_gap)
        n_labels = min(max_labels, min(8, n_visible))

        if n_labels >= 2:
            step = max(1, (n_visible - 1) // (n_labels - 1))
            tick_line = [" "] * (y_axis_area + candle_area)
            label_positions = []
            for li in range(n_labels):
                idx = min(li * step, n_visible - 1)
                pos = y_axis_area + idx
                if pos < len(tick_line):
                    tick_line[pos] = "┬"
                    label_positions.append((idx, pos))
            date_axis.append("".join(tick_line[:y_axis_area]), style=DIM)
            date_axis.append("".join(tick_line[y_axis_area:y_axis_area + candle_area]).replace(" ", "─"), style=DIM)
            date_axis.append("\n")

            last_end = 0
            for idx, pos in label_positions:
                if pos < last_end:
                    continue
                gap = pos - last_end
                if gap > 0:
                    date_axis.append(" " * gap)
                date_axis.append(labels[idx], style=LABEL)
                last_end = pos + label_len

    # Assemble: candle chart + our date axis (no library info line)
    result = Text.from_ansi(candle_lines)
    if date_axis.plain.strip():
        result.append("\n")
        result.append_text(date_axis)

    return result


def _render_sparkline(values: list[float], width: int = 30) -> Text:
    """Tiny inline sparkline using Unicode blocks. Green=up, Red=down vs previous bar."""
    if not values or len(values) < 2:
        return Text("—", style=LABEL)
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    blocks = " ▁▂▃▄▅▆▇█"
    result = Text()
    step = max(1, len(values) // width)
    sampled = values[::step][-width:]
    for i, v in enumerate(sampled):
        idx = int((v - mn) / rng * 8)
        if i == 0:
            color = LABEL
        elif v > sampled[i - 1]:
            color = GAIN
        elif v < sampled[i - 1]:
            color = LOSS_HI
        else:
            color = LABEL
        result.append(blocks[idx], style=color)
    return result
