"""Signal picker definitions + button-id helpers + noisy-logger silencing."""
from __future__ import annotations

import logging


def _silence_tui_noisy_loggers() -> None:
    """Keep background logger output from painting over the TUI."""
    noisy_loggers = [
        "httpx",
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "urllib3",
        "urllib3.connectionpool",
        "backend",
        "backend.backtesting.simple_backtest",
        "backend.trading.live_trader",
        "streamlit",
        "streamlit.runtime",
        "streamlit.runtime.caching",
        "streamlit.runtime.caching.cache_data_api",
    ]
    for name in noisy_loggers:
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        logger.propagate = False

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    for handler in list(root_logger.handlers):
        try:
            handler.setLevel(logging.WARNING)
        except Exception:
            pass


# ── Signal picker definitions (label, signal_type) ────────────────────────────
_SIGNAL_DEFS: list[tuple[str, str]] = [
    # Row 1 — factor / fundamental
    ("QUALITY",    "quality"),
    ("LOW VOL",    "low_vol"),
    ("XSEC MOM",  "xsec_momentum"),
    ("QF",         "quarterly_fundamental"),
    ("MEAN REV",  "mean_reversion"),
    # Row 2 — price / technical
    ("VOLUME",     "volume"),
    ("MOMENTUM",  "momentum"),
    ("ACCUM",     "accumulation"),
    ("52W HIGH",  "52wk_high"),
    ("VAL BNCE",  "value_bounce"),
    # Row 3 — alternative / microstructure / event
    ("CORP ACT",  "corp_action"),
    ("SMART $",   "smart_money"),
    ("SATELLITE", "satellite_hydro"),
]
_SIG_ID_PREFIX = "sig-btn-"


def _sig_btn_id(sig_type: str) -> str:
    return _SIG_ID_PREFIX + sig_type.replace("_", "-")


def _sig_type_from_id(btn_id: str) -> str:
    return btn_id[len(_SIG_ID_PREFIX):].replace("-", "_")
