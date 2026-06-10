"""Unit tests for the unified corporate_actions schema and refresh path."""

import sqlite3
from datetime import datetime, date

import pytest


def _reset_db(tmp_path, monkeypatch, name="test.db"):
    db_file = tmp_path / name
    monkeypatch.setenv("NEPSE_DB_FILE", str(db_file))
    import backend.quant_pro.database as db_mod
    db_mod._wal_initialized = False
    return db_file


def _row(symbol="TEST", bookclose=date(2024, 6, 1), description="Cash Dividend 10%"):
    from backend.quant_pro.corporate_actions import CorporateActionRow
    return CorporateActionRow(
        symbol=symbol,
        fiscal_year="2080/81",
        bookclose_date_ad=bookclose,
        description=description,
        agenda="AGM",
        cash_dividend_pct=10.0,
        bonus_share_pct=5.0,
        right_share_ratio="1:5",
        source_url="https://merolagani.com/CompanyDetail.aspx?symbol=TEST",
        scraped_at_utc=datetime(2024, 1, 1, 0, 0, 0),
    )


class TestUnifiedSchema:
    def test_corp_actions_roundtrip_unified_schema(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import init_db, get_db_connection
        from backend.quant_pro.corporate_actions import (
            upsert_corporate_actions, load_latest_corporate_actions,
        )
        from backend.backtesting.simple_backtest import load_corporate_actions

        init_db()
        rows = [
            _row(bookclose=date(2024, 6, 1), description="Cash Dividend 10%"),
            _row(bookclose=None, description="AGM Notice"),
        ]
        n = upsert_corporate_actions(rows)
        assert n == 2

        loaded = load_latest_corporate_actions("TEST")
        assert loaded
        first = loaded[0]
        for field in (
            "symbol", "fiscal_year", "bookclose_date", "description", "agenda",
            "cash_dividend_pct", "bonus_share_pct", "right_share_ratio",
            "source_url", "scraped_at_utc",
        ):
            assert field in first

        conn = get_db_connection()
        df = load_corporate_actions(conn)
        conn.close()
        assert not df.empty
        assert "bookclose_date" in df.columns


class TestMigration:
    def test_corp_actions_migration_on_legacy_schema_a(self, tmp_path, monkeypatch):
        db_file = _reset_db(tmp_path, monkeypatch)

        # Schema-A table with a pre-existing row (no description/source_url/scraped_at_utc).
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            '''
            CREATE TABLE corporate_actions (
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
            '''
        )
        conn.execute(
            "INSERT INTO corporate_actions (symbol, fiscal_year, bookclose_date, "
            "cash_dividend_pct, bonus_share_pct, agenda) "
            "VALUES ('OLD', '2079/80', '2023-06-01', 8.0, 0.0, 'AGM')"
        )
        conn.commit()
        conn.close()

        from backend.quant_pro.database import init_db
        init_db()

        from backend.quant_pro.corporate_actions import upsert_corporate_actions

        conn = sqlite3.connect(str(db_file))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(corporate_actions)")}
        assert {"description", "source_url", "scraped_at_utc"}.issubset(cols)
        # Old row readable with description IS NULL.
        old = conn.execute(
            "SELECT description FROM corporate_actions WHERE symbol='OLD'"
        ).fetchone()
        assert old[0] is None
        conn.close()

        # The previously-crashing upsert path now succeeds.
        n = upsert_corporate_actions([_row()])
        assert n == 1

    def test_corp_actions_migration_on_legacy_schema_b(self, tmp_path, monkeypatch):
        db_file = _reset_db(tmp_path, monkeypatch)

        # Schema-B table (created standalone by the old scraper DDL).
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            '''
            CREATE TABLE corporate_actions (
                symbol TEXT NOT NULL,
                fiscal_year TEXT,
                bookclose_date DATE,
                description TEXT,
                agenda TEXT,
                cash_dividend_pct REAL,
                bonus_share_pct REAL,
                right_share_ratio TEXT,
                source_url TEXT,
                scraped_at_utc TEXT NOT NULL,
                PRIMARY KEY (symbol, fiscal_year, bookclose_date, description)
            )
            '''
        )
        conn.commit()
        conn.close()

        from backend.quant_pro.corporate_actions import (
            upsert_corporate_actions, load_latest_corporate_actions,
        )
        from backend.backtesting.simple_backtest import load_corporate_actions
        from backend.quant_pro.database import get_db_connection

        # Guard adds nothing fatal; upsert + both readers work.
        n = upsert_corporate_actions([_row()])
        assert n == 1
        loaded = load_latest_corporate_actions("TEST")
        assert loaded

        conn = get_db_connection()
        df = load_corporate_actions(conn)
        conn.close()
        assert not df.empty


class TestRefreshIdempotent:
    def test_corp_actions_refresh_idempotent(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import init_db, get_db_connection
        from backend.quant_pro.corporate_actions import upsert_corporate_actions

        init_db()
        rows = [
            _row(bookclose=date(2024, 6, 1), description="Cash Dividend 10%"),
            _row(bookclose=None, description="AGM Notice"),
        ]
        upsert_corporate_actions(rows)
        conn = get_db_connection()
        count1 = conn.execute("SELECT COUNT(*) FROM corporate_actions WHERE symbol='TEST'").fetchone()[0]
        conn.close()

        upsert_corporate_actions(rows)
        conn = get_db_connection()
        count2 = conn.execute("SELECT COUNT(*) FROM corporate_actions WHERE symbol='TEST'").fetchone()[0]
        conn.close()
        assert count1 == count2


class TestMarketService:
    def test_market_service_reads_description(self, tmp_path, monkeypatch):
        db_file = _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import init_db, get_db_connection

        init_db()
        # Insert an upcoming bookclose so upcoming_corporate_actions returns it.
        from datetime import timedelta
        future = (datetime.now().date() + timedelta(days=5)).isoformat()
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO corporate_actions (symbol, fiscal_year, bookclose_date, "
            "cash_dividend_pct, bonus_share_pct, right_share_ratio, description, "
            "scraped_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("TEST", "2080/81", future, 10.0, 5.0, "1:5", "Cash Dividend 10%", "2024-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        from backend.core.services.market import MarketService
        svc = MarketService(db_path=db_file)
        actions = svc.upcoming_corporate_actions(days=30)
        assert any(a["symbol"] == "TEST" and a["description"] == "Cash Dividend 10%" for a in actions)
