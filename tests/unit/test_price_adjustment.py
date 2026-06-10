"""Unit tests for the vendor re-base splice protection in the save path."""

import sqlite3

import pandas as pd
import pytest


def _reset_db(tmp_path, monkeypatch, name="test.db"):
    db_file = tmp_path / name
    monkeypatch.setenv("NEPSE_DB_FILE", str(db_file))
    import backend.quant_pro.database as db_mod
    db_mod._wal_initialized = False
    return db_file


def _one_row_df(date, close):
    return pd.DataFrame({
        "Date": pd.to_datetime([date]),
        "Open": [close],
        "High": [close],
        "Low": [close],
        "Close": [close],
        "Volume": [1000.0],
    })


class TestMigration:
    def test_migration_idempotent_on_legacy_db(self, tmp_path, monkeypatch):
        db_file = _reset_db(tmp_path, monkeypatch)

        # Hand-create a legacy 7-column stock_prices with rows (pre-migration shape).
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            '''
            CREATE TABLE stock_prices (
                symbol TEXT, date DATE, open REAL, high REAL, low REAL,
                close REAL, volume REAL, PRIMARY KEY (symbol, date)
            )
            '''
        )
        conn.execute(
            "INSERT INTO stock_prices VALUES ('TEST', '2024-01-07', 100, 105, 99, 103, 1000)"
        )
        conn.commit()
        conn.close()

        from backend.quant_pro.database import init_db
        init_db()
        init_db()  # second run must be a no-op (idempotent)

        conn = sqlite3.connect(str(db_file))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(stock_prices)")}
        assert "raw_close" in cols

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "price_adjustment_log" in tables
        assert "pending_full_resync" in tables

        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_price_adjustment_log_symbol" in indexes

        # Legacy row should have been eager-backfilled raw_close == close.
        row = conn.execute(
            "SELECT close, raw_close FROM stock_prices WHERE symbol='TEST'"
        ).fetchone()
        assert row[0] == 103.0
        assert row[1] == 103.0
        conn.close()

    def test_save_to_db_runs_migration_itself(self, tmp_path, monkeypatch):
        db_file = _reset_db(tmp_path, monkeypatch)

        # Fresh DB created only via raw DDL — no init_db.
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            '''
            CREATE TABLE stock_prices (
                symbol TEXT, date DATE, open REAL, high REAL, low REAL,
                close REAL, volume REAL, PRIMARY KEY (symbol, date)
            )
            '''
        )
        conn.commit()
        conn.close()

        from backend.quant_pro.database import save_to_db, load_from_db
        result = save_to_db(_one_row_df("2024-01-07", 100.0), "TEST")
        assert result.rows_saved == 1
        loaded = load_from_db("TEST")
        assert not loaded.empty


class TestRawClose:
    def test_raw_close_survives_rebase(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import init_db, save_to_db, get_db_connection
        init_db()

        save_to_db(_one_row_df("2024-01-07", 100.0), "TEST")
        save_to_db(_one_row_df("2024-01-07", 90.9), "TEST", on_rebase="log")

        conn = get_db_connection()
        row = conn.execute(
            "SELECT close, raw_close FROM stock_prices WHERE symbol='TEST'"
        ).fetchone()
        assert row[0] == 90.9
        assert row[1] == 100.0

        save_to_db(_one_row_df("2024-01-07", 82.6), "TEST", on_rebase="log")
        row = conn.execute(
            "SELECT close, raw_close FROM stock_prices WHERE symbol='TEST'"
        ).fetchone()
        assert row[0] == 82.6
        assert row[1] == 100.0  # never overwritten
        conn.close()


class TestAdjustmentLog:
    def test_adjustment_log_row_correct(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import init_db, save_to_db, get_db_connection
        init_db()

        save_to_db(_one_row_df("2024-01-07", 100.0), "TEST")
        save_to_db(_one_row_df("2024-01-07", 90.9), "TEST", on_rebase="log")

        conn = get_db_connection()
        rows = conn.execute(
            "SELECT old_close, new_close, ratio, reason, detected_at_utc "
            "FROM price_adjustment_log WHERE symbol='TEST'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        old_close, new_close, ratio, reason, detected_at = rows[0]
        assert old_close == 100.0
        assert new_close == 90.9
        assert ratio == pytest.approx(0.909)
        assert reason == "resave"
        # Parseable ISO-8601 timestamp.
        from datetime import datetime
        datetime.fromisoformat(detected_at)

    def test_identical_resave_logs_nothing(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import init_db, save_to_db, get_db_connection
        init_db()

        save_to_db(_one_row_df("2024-01-07", 100.0), "TEST")
        result = save_to_db(_one_row_df("2024-01-07", 100.0), "TEST")
        assert result.retro_changes == 0

        conn = get_db_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM price_adjustment_log WHERE symbol='TEST'"
        ).fetchone()[0]
        conn.close()
        assert count == 0

    def test_minor_correction_does_not_flag_rebase(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import init_db, save_to_db, get_db_connection
        init_db()

        save_to_db(_one_row_df("2024-01-07", 100.0), "TEST")
        # +0.0001% change: beyond LOG_TOL but far below REBASE_RTOL.
        result = save_to_db(
            _one_row_df("2024-01-07", 100.0001), "TEST", on_rebase="abort"
        )
        assert result.retro_changes == 1
        assert result.rebase_detected is False
        assert result.rows_saved == 1

        conn = get_db_connection()
        close = conn.execute(
            "SELECT close FROM stock_prices WHERE symbol='TEST'"
        ).fetchone()[0]
        conn.close()
        assert close == pytest.approx(100.0001)


class TestAbortMode:
    def test_abort_mode_rolls_back_on_rebase(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import init_db, save_to_db, get_db_connection
        init_db()

        save_to_db(_one_row_df("2024-01-07", 100.0), "TEST")
        # >0.1% change in an overlap row -> classified as a vendor re-base.
        result = save_to_db(_one_row_df("2024-01-07", 90.0), "TEST", on_rebase="abort")
        assert result.rows_saved == 0
        assert result.rebase_detected is True

        conn = get_db_connection()
        close = conn.execute(
            "SELECT close FROM stock_prices WHERE symbol='TEST'"
        ).fetchone()[0]
        assert close == 100.0  # prices unchanged

        log_rows = conn.execute(
            "SELECT reason FROM price_adjustment_log WHERE symbol='TEST'"
        ).fetchall()
        conn.close()
        assert log_rows  # detection evidence WAS committed
        assert all(r[0] == "rebase_overlap" for r in log_rows)


class TestSaveResult:
    def test_save_result_backward_compatible(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import init_db, save_to_db, SaveResult
        init_db()

        # Default-mode call signature still works and returns a SaveResult.
        result = save_to_db(_one_row_df("2024-01-07", 100.0), "TEST")
        assert isinstance(result, SaveResult)
        assert result.rows_saved == 1


class TestOverlapStartDate:
    def test_get_overlap_start_date(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import (
            init_db, save_to_db, get_overlap_start_date,
        )
        init_db()

        # No rows -> None.
        assert get_overlap_start_date("TEST", 5) is None

        dates = pd.bdate_range("2024-01-01", periods=10)
        df = pd.DataFrame({
            "Date": dates,
            "Open": [100.0] * 10,
            "High": [100.0] * 10,
            "Low": [100.0] * 10,
            "Close": [float(100 + i) for i in range(10)],
            "Volume": [1000.0] * 10,
        })
        save_to_db(df, "TEST")

        # 5th-from-last of 10 bars.
        expected = dates[-5].strftime("%Y-%m-%d")
        assert get_overlap_start_date("TEST", 5) == expected

    def test_get_overlap_start_date_fewer_bars(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import (
            init_db, save_to_db, get_overlap_start_date,
        )
        init_db()

        dates = pd.bdate_range("2024-01-01", periods=3)
        df = pd.DataFrame({
            "Date": dates,
            "Open": [100.0] * 3,
            "High": [100.0] * 3,
            "Low": [100.0] * 3,
            "Close": [100.0, 101.0, 102.0],
            "Volume": [1000.0] * 3,
        })
        save_to_db(df, "TEST")

        # Fewer than n_bars stored -> oldest bar.
        assert get_overlap_start_date("TEST", 5) == dates[0].strftime("%Y-%m-%d")


class TestPendingResync:
    def test_pending_resync_helpers(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        from backend.quant_pro.database import (
            init_db, register_pending_resync, list_pending_resyncs,
            clear_pending_resync, get_db_connection,
        )
        init_db()

        register_pending_resync("AAA")
        register_pending_resync("BBB")
        assert set(list_pending_resyncs()) == {"AAA", "BBB"}

        # Re-register bumps attempts.
        register_pending_resync("AAA")
        conn = get_db_connection()
        attempts = conn.execute(
            "SELECT attempts FROM pending_full_resync WHERE symbol='AAA'"
        ).fetchone()[0]
        conn.close()
        assert attempts == 2

        clear_pending_resync("AAA")
        assert list_pending_resyncs() == ["BBB"]
