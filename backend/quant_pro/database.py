import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from .exceptions import DatabaseError
from .paths import get_data_dir

# Configure logging
logger = logging.getLogger(__name__)

# Flag to track if WAL mode has been initialized
_wal_initialized = False

# Tolerances for the save-path re-base detector.
# LOG_TOL is absolute: unchanged JSON floats reproduce exactly, so anything
# beyond it is a genuine retro change worth logging.
# REBASE_RTOL is relative: the smallest real NEPSE events (small rights/bonus)
# shift prices by >=1%, while float jitter is well under 0.1%, so a relative
# move beyond REBASE_RTOL classifies a vendor re-base.
LOG_TOL = 1e-9
REBASE_RTOL = 1e-3


def get_db_path() -> Path:
    """
    Return the canonical database file path.

    Reads ``NEPSE_DB_FILE`` from the environment (if set) and resolves it to an
    absolute path. Falls back to ``data/nepse_market_data.db`` in the project root.
    """
    raw = os.environ.get("NEPSE_DB_FILE", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return get_data_dir(__file__) / "nepse_market_data.db"


# Legacy module-level constant kept for backwards compat during migration.
DB_FILE = str(get_db_path())


def _is_nepse_trading_day(date_like: object) -> bool:
    """
    Prefer deriving trading days from benchmark history when available:
    - If NEPSE index has a row for the date, treat it as a trading day.
    - Otherwise fall back to the NEPSE weekmask (Sun–Thu) used throughout the codebase.
    """
    try:
        day = pd.Timestamp(date_like).normalize()
    except (ValueError, TypeError):
        return False
    day_str = day.strftime("%Y-%m-%d")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM stock_prices WHERE symbol = ? AND date = ? LIMIT 1", ("NEPSE", day_str))
        row = cur.fetchone()
        conn.close()
        if row is not None:
            return True
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        pass
    # Fallback: Sunday(6) ... Thursday(3) on pandas dayofweek scale (Mon=0).
    try:
        return int(day.dayofweek) in {6, 0, 1, 2, 3}
    except (ValueError, AttributeError):
        return False


def get_db_connection(timeout: float = 60.0, retries: int = 3) -> sqlite3.Connection:
    """
    Get database connection with proper pragmas for concurrency.

    Retries with exponential backoff on ``OperationalError`` (database locked).
    Raises ``DatabaseError`` after exhausting retries.
    """
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            conn = sqlite3.connect(str(get_db_path()), timeout=timeout)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 60000")
            return conn
        except sqlite3.OperationalError as exc:
            last_err = exc
            if attempt < retries:
                wait = min(2 ** attempt, 30)
                logger.warning(
                    "DB connection attempt %d/%d failed (%s), retrying in %ds",
                    attempt, retries, exc, wait,
                )
                time.sleep(wait)
    raise DatabaseError(f"Failed to connect after {retries} attempts: {last_err}") from last_err


def _ensure_column(cursor: sqlite3.Cursor, table: str, column: str, decl: str) -> bool:
    """Add ``column`` to ``table`` if missing (SQLite has no IF NOT EXISTS on
    ADD COLUMN). Returns True iff the column was added by this call."""
    have = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
    if column not in have:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return True
    return False


def _ensure_price_adjustment_schema(cursor: sqlite3.Cursor) -> None:
    """Idempotent guard for raw_close + the adjustment audit log + resync queue."""
    added = _ensure_column(cursor, "stock_prices", "raw_close", "REAL")
    if added:
        # One-time eager backfill: best-effort "as first seen" = current stored close.
        cursor.execute("UPDATE stock_prices SET raw_close = close WHERE raw_close IS NULL")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_adjustment_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            date DATE NOT NULL,
            old_close REAL,
            new_close REAL,
            ratio REAL,
            reason TEXT NOT NULL DEFAULT 'resave',
            detected_at_utc TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_price_adjustment_log_symbol
        ON price_adjustment_log (symbol, date)
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_full_resync (
            symbol TEXT PRIMARY KEY,
            first_detected_at_utc TEXT NOT NULL,
            last_attempt_utc TEXT,
            attempts INTEGER NOT NULL DEFAULT 0
        )
    ''')


def _ensure_corporate_actions_schema(cursor: sqlite3.Cursor) -> None:
    """Idempotent guard unifying the corporate_actions schema onto a superset.

    The base table (schema A) is created by ``init_db``; this adds the extra
    columns the scraper writes. For DBs whose table was created standalone by the
    scraper (schema B), these calls are no-ops and the schema-A-only columns are
    backfilled instead — both ancestries converge on a working superset.
    """
    _ensure_column(cursor, "corporate_actions", "description", "TEXT")
    _ensure_column(cursor, "corporate_actions", "source_url", "TEXT")
    _ensure_column(cursor, "corporate_actions", "scraped_at_utc", "TEXT")
    _ensure_column(cursor, "corporate_actions", "cash_dividend_pct", "REAL DEFAULT 0")
    _ensure_column(cursor, "corporate_actions", "bonus_share_pct", "REAL DEFAULT 0")
    _ensure_column(cursor, "corporate_actions", "right_share_ratio", "TEXT")
    _ensure_column(cursor, "corporate_actions", "agenda", "TEXT")
    _ensure_column(cursor, "corporate_actions", "fiscal_year", "TEXT")


@dataclass
class SaveResult:
    rows_saved: int = 0
    retro_changes: int = 0        # rows whose stored close differed beyond LOG_TOL
    rebase_detected: bool = False  # any row beyond REBASE_RTOL -> vendor re-based


def _dedupe_stock_prices(cursor: sqlite3.Cursor) -> None:
    """Keep the newest row for each legacy duplicate (symbol, date) pair."""
    cursor.execute(
        '''
        DELETE FROM stock_prices
        WHERE rowid NOT IN (
            SELECT MAX(rowid)
            FROM stock_prices
            GROUP BY symbol, date
        )
        '''
    )


def init_db():
    """Creates the table if it doesn't exist and configures WAL mode."""
    global _wal_initialized
    conn = get_db_connection()
    cursor = conn.cursor()

    # Enable WAL mode for better concurrency (only needs to be done once per database file)
    if not _wal_initialized:
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")  # Balance durability/speed
            cursor.execute("PRAGMA cache_size=-64000")   # 64MB cache
            cursor.execute("PRAGMA temp_store=MEMORY")
            _wal_initialized = True
            logger.debug("SQLite WAL mode enabled")
        except sqlite3.OperationalError as e:
            logger.warning(f"Failed to enable WAL mode: {e}")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_prices (
            symbol TEXT,
            date DATE,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            PRIMARY KEY (symbol, date)
        )
    ''')
    _ensure_price_adjustment_schema(cursor)
    _dedupe_stock_prices(cursor)
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_prices_symbol_date_unique
        ON stock_prices (symbol, date)
    ''')

    # Create index for faster symbol lookups if not exists
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_stock_prices_symbol
        ON stock_prices (symbol)
    ''')

    # Corporate actions table (used by signal generators and backtest)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS corporate_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            fiscal_year TEXT,
            bookclose_date DATE,
            cash_dividend_pct REAL DEFAULT 0,
            bonus_share_pct REAL DEFAULT 0,
            right_share_ratio TEXT,
            agenda TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, bookclose_date)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_corporate_actions_bookclose
        ON corporate_actions (bookclose_date)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_corporate_actions_symbol
        ON corporate_actions (symbol)
    ''')
    _ensure_corporate_actions_schema(cursor)

    # Raw intraday market snapshots for audit/replay of upstream payloads.
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS market_data_raw (
            raw_id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset TEXT NOT NULL,
            source TEXT NOT NULL,
            symbol TEXT,
            business_date TEXT,
            fetched_at_utc TEXT NOT NULL,
            record_count INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL,
            metadata_json TEXT
        )
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_market_data_raw_dataset_fetched
        ON market_data_raw (dataset, fetched_at_utc DESC)
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_market_data_raw_symbol_fetched
        ON market_data_raw (symbol, fetched_at_utc DESC)
        '''
    )

    # Normalized quote snapshots for fast symbol-level lookup.
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS market_quotes (
            raw_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            security_id TEXT,
            security_name TEXT,
            last_traded_price REAL,
            close_price REAL,
            previous_close REAL,
            percentage_change REAL,
            total_trade_quantity REAL,
            source TEXT NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            PRIMARY KEY (raw_id, symbol),
            FOREIGN KEY (raw_id) REFERENCES market_data_raw(raw_id)
        )
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_market_quotes_symbol_fetched
        ON market_quotes (symbol, fetched_at_utc DESC)
        '''
    )

    # Daily benchmark history snapshots for local performance comparison.
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS benchmark_index_history (
            benchmark TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL NOT NULL,
            volume REAL,
            source TEXT NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            PRIMARY KEY (benchmark, date)
        )
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_benchmark_index_history_date
        ON benchmark_index_history (benchmark, date DESC)
        '''
    )

    # Earnings / fundamentals snapshots used by lookup, signals, and reports.
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS quarterly_earnings (
            symbol TEXT NOT NULL,
            fiscal_year TEXT NOT NULL,
            quarter INTEGER NOT NULL,
            eps REAL,
            net_profit REAL,
            revenue REAL,
            book_value REAL,
            announcement_date TEXT,
            report_date TEXT,
            source TEXT DEFAULT 'sharesansar',
            scraped_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, fiscal_year, quarter)
        )
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_qe_symbol
        ON quarterly_earnings (symbol)
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_qe_announcement
        ON quarterly_earnings (announcement_date)
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS fundamentals (
            symbol TEXT,
            date DATE,
            market_cap REAL,
            pe_ratio REAL,
            pb_ratio REAL,
            eps REAL,
            book_value_per_share REAL,
            roe REAL,
            debt_to_equity REAL,
            dividend_yield REAL,
            payout_ratio REAL,
            current_ratio REAL,
            shares_outstanding REAL,
            sector TEXT,
            PRIMARY KEY (symbol, date)
        )
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol
        ON fundamentals (symbol, date DESC)
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            date DATE,
            headline TEXT,
            url TEXT UNIQUE,
            source TEXT,
            sentiment_score REAL,
            sentiment_label TEXT,
            category TEXT,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_news_symbol
        ON news(symbol)
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_news_date
        ON news(date DESC)
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_news_sentiment
        ON news(sentiment_label)
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS sentiment_scores (
            date TEXT NOT NULL,
            symbol TEXT,
            source TEXT NOT NULL,
            model TEXT NOT NULL,
            score REAL NOT NULL,
            confidence REAL,
            n_documents INTEGER,
            scraped_at_utc TEXT NOT NULL,
            PRIMARY KEY (date, symbol, source, model)
        )
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_sentiment_date
        ON sentiment_scores(date DESC)
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_sentiment_symbol
        ON sentiment_scores(symbol)
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS news_event_scores (
            event_score_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            window_start_utc TEXT NOT NULL,
            window_end_utc TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            impact_direction TEXT NOT NULL,
            impact_score REAL NOT NULL,
            confidence REAL NOT NULL,
            event_type TEXT NOT NULL,
            source_count INTEGER NOT NULL DEFAULT 0,
            source_refs_json TEXT NOT NULL,
            rationale_short TEXT NOT NULL,
            model_name TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        )
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_news_event_scores_run_entity
        ON news_event_scores(run_date DESC, entity_type, entity_key)
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_news_event_scores_created
        ON news_event_scores(created_at_utc DESC)
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS floorsheet_trades (
            transact_no TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            as_of_date DATE NOT NULL,
            buyer_broker INTEGER NOT NULL,
            seller_broker INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            rate REAL NOT NULL,
            amount REAL NOT NULL,
            source_url TEXT,
            scraped_at_utc TEXT NOT NULL
        )
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_floorsheet_symbol_date
        ON floorsheet_trades(symbol, as_of_date)
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_floorsheet_buyer
        ON floorsheet_trades(symbol, as_of_date, buyer_broker)
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_floorsheet_seller
        ON floorsheet_trades(symbol, as_of_date, seller_broker)
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS broker_summary (
            symbol TEXT NOT NULL,
            as_of_date DATE NOT NULL,
            broker_code INTEGER NOT NULL,
            buy_qty INTEGER NOT NULL,
            sell_qty INTEGER NOT NULL,
            net_qty INTEGER NOT NULL,
            buy_amount REAL NOT NULL,
            sell_amount REAL NOT NULL,
            net_amount REAL NOT NULL,
            buy_trades INTEGER NOT NULL,
            sell_trades INTEGER NOT NULL,
            total_trades INTEGER NOT NULL,
            PRIMARY KEY (symbol, as_of_date, broker_code)
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS broker_signal_scores (
            symbol TEXT NOT NULL,
            as_of_date DATE NOT NULL,
            total_trades INTEGER NOT NULL,
            total_qty INTEGER NOT NULL,
            total_amount REAL NOT NULL,
            top1_net_share REAL,
            top5_net_share REAL,
            hhi_net REAL,
            accumulation_score REAL,
            flags TEXT,
            PRIMARY KEY (symbol, as_of_date)
        )
        '''
    )
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS broker_signals_v2 (
            symbol TEXT NOT NULL,
            as_of_date DATE NOT NULL,
            hhi_buy REAL,
            hhi_sell REAL,
            hhi_buy_norm REAL,
            hhi_sell_norm REAL,
            circular_score REAL,
            top_pair_pct REAL,
            self_trade_pct REAL,
            smart_money_score REAL,
            pump_score REAL,
            total_volume INTEGER,
            n_trades INTEGER,
            n_brokers_buy INTEGER,
            n_brokers_sell INTEGER,
            PRIMARY KEY (symbol, as_of_date)
        )
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_bsv2_date
        ON broker_signals_v2 (as_of_date)
        '''
    )
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS broker_microstructure (
            symbol TEXT NOT NULL,
            as_of_date DATE NOT NULL,
            amihud_illiq REAL,
            roll_spread REAL,
            cs_spread REAL,
            kyle_lambda REAL,
            kyle_pvalue REAL,
            kyle_rsq REAL,
            kyle_cwoib REAL,
            kyle_significant INTEGER,
            pin_proxy REAL,
            micro_score REAL,
            PRIMARY KEY (symbol, as_of_date)
        )
        '''
    )
    cursor.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_bmicro_date
        ON broker_microstructure (as_of_date)
        '''
    )
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS floorsheet_scrape_log (
            as_of_date     TEXT PRIMARY KEY,
            total_rows     INTEGER NOT NULL,
            total_pages    INTEGER NOT NULL,
            scraped_at_utc TEXT NOT NULL
        )
        '''
    )

    conn.commit()
    conn.close()

def get_latest_date(symbol):
    """Returns the most recent date we have for a symbol, or None."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) FROM stock_prices WHERE symbol = ?", (symbol,))
    result = cursor.fetchone()[0]
    conn.close()
    return pd.to_datetime(result).date() if result else None


def get_overlap_start_date(symbol: str, n_bars: int = 5) -> Optional[str]:
    """Date of the n_bars-th most recent stored bar (falls back to the oldest
    stored bar when fewer exist), or None when the symbol has no rows.

    Used to widen the incremental fetch window so the re-base detector always has
    overlapping bars to compare; it adds no extra HTTP requests.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM stock_prices WHERE symbol = ?", (symbol,))
    count = int(cursor.fetchone()[0] or 0)
    if count == 0:
        conn.close()
        return None
    offset = min(n_bars - 1, count - 1)
    cursor.execute(
        "SELECT date FROM stock_prices WHERE symbol = ? ORDER BY date DESC LIMIT 1 OFFSET ?",
        (symbol, offset),
    )
    row = cursor.fetchone()
    conn.close()
    return str(row[0]) if row and row[0] is not None else None


def register_pending_resync(symbol: str) -> None:
    """Queue ``symbol`` for a full-history re-fetch (UPSERT, bump attempts)."""
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    _ensure_price_adjustment_schema(cursor)
    cursor.execute(
        '''
        INSERT INTO pending_full_resync (symbol, first_detected_at_utc, last_attempt_utc, attempts)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(symbol) DO UPDATE SET
            last_attempt_utc = excluded.last_attempt_utc,
            attempts = attempts + 1
        ''',
        (symbol, now_iso, now_iso),
    )
    conn.commit()
    conn.close()


def clear_pending_resync(symbol: str) -> None:
    """Remove ``symbol`` from the full-history re-fetch queue."""
    conn = get_db_connection()
    cursor = conn.cursor()
    _ensure_price_adjustment_schema(cursor)
    cursor.execute("DELETE FROM pending_full_resync WHERE symbol = ?", (symbol,))
    conn.commit()
    conn.close()


def list_pending_resyncs() -> List[str]:
    """Return symbols currently queued for a full-history re-fetch."""
    conn = get_db_connection()
    cursor = conn.cursor()
    _ensure_price_adjustment_schema(cursor)
    cursor.execute("SELECT symbol FROM pending_full_resync ORDER BY symbol ASC")
    rows = cursor.fetchall()
    conn.close()
    return [str(r[0]) for r in rows]

def save_to_db(df, symbol, *, on_rebase: str = "log", log_reason: str = "resave") -> "SaveResult":
    """Save OHLCV rows, preserving first-seen ``raw_close`` and auditing changes.

    Every re-save is compared against the stored close: changes beyond ``LOG_TOL``
    are recorded in ``price_adjustment_log``; a relative change beyond
    ``REBASE_RTOL`` flags a vendor re-base.

    ``on_rebase``:
        ``"log"`` (default) — rewrite every row on the new basis (the intent of a
        full-history save) and log the changes.
        ``"abort"`` — when a re-base is detected, write no price rows; only the
        detection evidence is persisted (``reason='rebase_overlap'``). The DB
        stays on a single (old) basis until a full re-fetch replaces it.
    """
    if df.empty:
        return SaveResult()

    conn = get_db_connection()
    df = df.copy()
    df["symbol"] = symbol
    # Ensure date is string YYYY-MM-DD for SQLite
    df["date"] = pd.to_datetime(df["Date"]).dt.strftime('%Y-%m-%d')

    # Data hygiene: reject accidental non-trading-day inserts when Volume==0 and the date
    # is not a known trading session (prevents stale "live candles" polluting EOD history).
    try:
        vol = pd.to_numeric(df.get("Volume", 0.0), errors="coerce").fillna(0.0)
        dates = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
        non_trading = ~dates.apply(_is_nepse_trading_day)
        bad = (vol <= 0.0) & non_trading
        if bad.any():
            df = df.loc[~bad].copy()
            if df.empty:
                conn.close()
                return SaveResult()
    except (KeyError, ValueError, TypeError) as exc:
        logger.debug("Data hygiene check skipped: %s", exc)

    # Rename columns to match DB schema
    df_to_save = df[["symbol", "date", "Open", "High", "Low", "Close", "Volume"]]
    df_to_save.columns = ["symbol", "date", "open", "high", "low", "close", "volume"]

    cursor = conn.cursor()
    _ensure_price_adjustment_schema(cursor)
    # Commit the migration before any data work: the abort path rolls back the
    # transaction, and the DDL must not be undone with it on a first-run DB.
    conn.commit()

    # Pre-read existing rows for the incoming dates so we can preserve raw_close
    # (first-seen close) and compare against the new basis.
    incoming_dates = list(df_to_save["date"])
    existing: Dict[str, tuple] = {}
    if incoming_dates:
        placeholders = ",".join("?" for _ in incoming_dates)
        cursor.execute(
            f'''
            SELECT date, close, raw_close FROM stock_prices
            WHERE symbol = ? AND date IN ({placeholders})
            ''',
            [symbol, *incoming_dates],
        )
        for row in cursor.fetchall():
            existing[str(row[0])] = (row[1], row[2])

    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows: List[tuple] = []
    adjustments: List[tuple] = []
    retro_changes = 0
    rebase_detected = False

    for r in df_to_save.itertuples(index=False):
        new_close = r.close
        prior = existing.get(r.date)
        if prior is not None:
            old_close, old_raw = prior
            # Lazy first-seen preservation/backfill.
            raw_close = old_raw if old_raw is not None else old_close
            if old_close is not None and abs(old_close - new_close) > LOG_TOL:
                ratio = (new_close / old_close) if old_close else None
                adjustments.append(
                    (symbol, r.date, old_close, new_close, ratio, log_reason, now_iso)
                )
                retro_changes += 1
                if old_close and abs((new_close / old_close) - 1.0) > REBASE_RTOL:
                    rebase_detected = True
        else:
            raw_close = new_close
        rows.append((r.symbol, r.date, r.open, r.high, r.low, new_close, r.volume, raw_close))

    if on_rebase == "abort" and rebase_detected:
        # Persist no price rows; keep only the detection evidence so the series
        # stays on one consistent (old) basis until a full re-fetch replaces it.
        conn.rollback()
        rebase_log = [
            (a[0], a[1], a[2], a[3], a[4], "rebase_overlap", a[6]) for a in adjustments
        ]
        cursor.executemany(
            '''
            INSERT INTO price_adjustment_log (
                symbol, date, old_close, new_close, ratio, reason, detected_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            rebase_log,
        )
        conn.commit()
        conn.close()
        logger.warning(
            "%s: vendor re-base detected in overlap (%d rows); aborting incremental save",
            symbol, len(adjustments),
        )
        return SaveResult(rows_saved=0, retro_changes=retro_changes, rebase_detected=True)

    cursor.executemany('''
        INSERT OR REPLACE INTO stock_prices (symbol, date, open, high, low, close, volume, raw_close)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', rows)

    if adjustments:
        cursor.executemany(
            '''
            INSERT INTO price_adjustment_log (
                symbol, date, old_close, new_close, ratio, reason, detected_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            adjustments,
        )
        logger.warning(
            "%s: %d stored close(s) changed on re-save (reason=%s)",
            symbol, len(adjustments), log_reason,
        )

    conn.commit()
    conn.close()
    return SaveResult(rows_saved=len(rows), retro_changes=retro_changes, rebase_detected=rebase_detected)

def load_from_db(symbol):
    """Loads full history for a symbol."""
    conn = get_db_connection()
    query = """
        SELECT date as Date, open as Open, high as High, low as Low, close as Close, volume as Volume
        FROM stock_prices
        WHERE symbol = ?
        ORDER BY date ASC
    """
    df = pd.read_sql(query, conn, params=(symbol,))
    conn.close()

    if not df.empty:
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")
    return df


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)


def save_market_data_raw(
    *,
    dataset: str,
    source: str,
    payload: Any,
    symbol: Optional[str] = None,
    business_date: Optional[str] = None,
    fetched_at_utc: Optional[str] = None,
    record_count: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    """Persist raw upstream payloads for later audit/replay."""
    init_db()
    conn = get_db_connection()
    cur = conn.cursor()
    fetched_at_utc = fetched_at_utc or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if record_count is None:
        if isinstance(payload, list):
            record_count = len(payload)
        elif isinstance(payload, dict):
            record_count = len(payload)
        else:
            record_count = 1
    cur.execute(
        '''
        INSERT INTO market_data_raw (
            dataset, source, symbol, business_date, fetched_at_utc,
            record_count, payload_json, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            dataset,
            source,
            symbol,
            business_date,
            fetched_at_utc,
            int(record_count),
            _json_dumps(payload),
            _json_dumps(metadata) if metadata is not None else None,
        ),
    )
    raw_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return raw_id


def save_market_quotes(raw_id: int, quotes: Iterable[Dict[str, Any]]) -> int:
    """Persist normalized symbol-level quotes linked to a raw snapshot."""
    rows = []
    for quote in quotes:
        symbol = str(quote.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        rows.append(
            (
                int(raw_id),
                symbol,
                str(quote.get("security_id")) if quote.get("security_id") is not None else None,
                quote.get("security_name"),
                quote.get("last_traded_price"),
                quote.get("close_price"),
                quote.get("previous_close"),
                quote.get("percentage_change"),
                quote.get("total_trade_quantity"),
                str(quote.get("source") or ""),
                str(quote.get("fetched_at_utc") or ""),
            )
        )
    if not rows:
        return 0

    init_db()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.executemany(
        '''
        INSERT OR REPLACE INTO market_quotes (
            raw_id, symbol, security_id, security_name, last_traded_price,
            close_price, previous_close, percentage_change,
            total_trade_quantity, source, fetched_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        rows,
    )
    count = len(rows)
    conn.commit()
    conn.close()
    return count


def load_latest_market_quotes(
    symbols: Iterable[str],
    *,
    max_age_seconds: Optional[int] = None,
) -> Dict[str, Dict[str, Any]]:
    """Load the most recent normalized quote per symbol from SQLite."""
    clean_symbols = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
    if not clean_symbols:
        return {}

    init_db()
    conn = get_db_connection()
    cur = conn.cursor()
    results: Dict[str, Dict[str, Any]] = {}

    age_cutoff = None
    if max_age_seconds is not None:
        age_cutoff = (
            datetime.now(timezone.utc) - pd.Timedelta(seconds=int(max_age_seconds))
        ).replace(microsecond=0).isoformat()

    query = (
        '''
        SELECT symbol, security_id, security_name, last_traded_price, close_price,
               previous_close, percentage_change, total_trade_quantity, source,
               fetched_at_utc
        FROM market_quotes
        WHERE symbol = ?
        '''
        + (" AND fetched_at_utc >= ?" if age_cutoff is not None else "")
        + '''
        ORDER BY fetched_at_utc DESC, raw_id DESC
        LIMIT 1
        '''
    )

    for symbol in clean_symbols:
        params = (symbol, age_cutoff) if age_cutoff is not None else (symbol,)
        cur.execute(query, params)
        row = cur.fetchone()
        if row is None:
            continue
        results[symbol] = {
            "symbol": row[0],
            "security_id": row[1],
            "security_name": row[2],
            "last_traded_price": row[3],
            "close_price": row[4],
            "previous_close": row[5],
            "percentage_change": row[6],
            "total_trade_quantity": row[7],
            "source": row[8],
            "fetched_at_utc": row[9],
        }

    conn.close()
    return results


def save_benchmark_history(
    benchmark: str,
    rows: Iterable[Dict[str, Any]],
    *,
    source: str,
    fetched_at_utc: Optional[str] = None,
) -> int:
    """Persist daily benchmark index history."""
    benchmark = str(benchmark).strip().upper()
    if not benchmark:
        return 0
    fetched_at_utc = fetched_at_utc or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload = []
    for row in rows:
        date_value = row.get("date") or row.get("Date")
        if date_value is None:
            continue
        try:
            date_str = pd.Timestamp(date_value).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        payload.append(
            (
                benchmark,
                date_str,
                row.get("open") if row.get("open") is not None else row.get("Open"),
                row.get("high") if row.get("high") is not None else row.get("High"),
                row.get("low") if row.get("low") is not None else row.get("Low"),
                row.get("close") if row.get("close") is not None else row.get("Close"),
                row.get("volume") if row.get("volume") is not None else row.get("Volume"),
                source,
                fetched_at_utc,
            )
        )
    if not payload:
        return 0

    init_db()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.executemany(
        '''
        INSERT OR REPLACE INTO benchmark_index_history (
            benchmark, date, open, high, low, close, volume, source, fetched_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        payload,
    )
    conn.commit()
    conn.close()
    return len(payload)


def load_benchmark_history(
    benchmark: str,
    *,
    start_date: Optional[object] = None,
    end_date: Optional[object] = None,
) -> pd.DataFrame:
    """Load benchmark history from local SQLite snapshots."""
    benchmark = str(benchmark).strip().upper()
    if not benchmark:
        return pd.DataFrame()

    init_db()
    conn = get_db_connection()
    query = '''
        SELECT date AS Date, open AS Open, high AS High, low AS Low, close AS Close,
               volume AS Volume, source AS Source, fetched_at_utc AS Fetched_At
        FROM benchmark_index_history
        WHERE benchmark = ?
    '''
    params: list[Any] = [benchmark]
    if start_date is not None:
        query += " AND date >= ?"
        params.append(pd.Timestamp(start_date).strftime("%Y-%m-%d"))
    if end_date is not None:
        query += " AND date <= ?"
        params.append(pd.Timestamp(end_date).strftime("%Y-%m-%d"))
    query += " ORDER BY date ASC"
    df = pd.read_sql(query, conn, params=params, parse_dates=["Date"])
    conn.close()
    return df
