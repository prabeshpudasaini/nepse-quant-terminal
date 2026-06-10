"""Unit tests for vendor re-base splice detection in incremental ingestion."""

import sqlite3

import pandas as pd
import pytest


def _reset_db(tmp_path, monkeypatch, name="test.db"):
    db_file = tmp_path / name
    monkeypatch.setenv("NEPSE_DB_FILE", str(db_file))
    import backend.quant_pro.database as db_mod
    db_mod._wal_initialized = False
    return db_file


def _history_df(dates, base=100.0):
    closes = [base + i for i in range(len(dates))]
    return pd.DataFrame({
        "Date": pd.to_datetime(dates),
        "Open": closes,
        "High": [c + 1 for c in closes],
        "Low": [c - 1 for c in closes],
        "Close": closes,
        "Volume": [1000.0] * len(dates),
    })


def _seed_30_bars(monkeypatch):
    from backend.quant_pro.database import init_db, save_to_db
    init_db()
    # 30 trading-ish bars ending a few days ago (before today).
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=5), periods=30)
    df = _history_df([d.strftime("%Y-%m-%d") for d in dates])
    save_to_db(df, "TEST")
    return dates


class TestSpliceDetection:
    def test_splice_detection_triggers_full_refetch(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        import backend.quant_pro.data_io as dio
        from backend.quant_pro.database import get_db_connection

        dates = _seed_30_bars(monkeypatch)
        # Capture the original closes (old basis).
        old_df = _history_df([d.strftime("%Y-%m-%d") for d in dates])
        old_closes = {d.strftime("%Y-%m-%d"): c for d, c in zip(dates, old_df["Close"])}

        scale = 1.0 / 1.2  # simulated 20% bonus re-base

        # Overlap (last 5 bars) + 2 new bars, all on the new (scaled) basis.
        new_dates = list(dates[-5:]) + list(
            pd.bdate_range(start=dates[-1] + pd.Timedelta(days=1), periods=2)
        )
        new_dates = [d.strftime("%Y-%m-%d") for d in new_dates]
        incr = _history_df(new_dates)
        incr["Close"] = incr["Close"] * scale
        incr["Open"] = incr["Open"] * scale
        incr["High"] = incr["High"] * scale
        incr["Low"] = incr["Low"] * scale
        # The overlap bars must carry the SAME closes that the existing bars had
        # (scaled), so the detector sees a consistent re-base ratio.
        overlap_scaled = [old_closes[d] * scale for d in new_dates[:5]]
        for i, c in enumerate(overlap_scaled):
            incr.iloc[i, incr.columns.get_loc("Close")] = c

        # Full 10y history on the new basis.
        full = _history_df([d.strftime("%Y-%m-%d") for d in dates] + new_dates[5:])
        full["Close"] = full["Close"] * scale

        calls = {"full": 0, "incr": 0}

        def fake_fetch_chunk(symbol, start_ts, end_ts):
            # 10-year range starts far in the past; the overlap range is recent.
            ten_years_ago = (pd.Timestamp.now() - pd.Timedelta(days=365 * 9)).timestamp()
            if start_ts < ten_years_ago:
                calls["full"] += 1
                return full.copy()
            calls["incr"] += 1
            return incr.copy()

        monkeypatch.setattr(dio, "fetch_chunk", fake_fetch_chunk)
        monkeypatch.setattr(dio, "_is_streamlit_active", lambda: False)

        dio._fetch_dynamic_data("TEST")

        assert calls["incr"] >= 1
        assert calls["full"] == 1  # full re-fetch happened

        conn = get_db_connection()
        rows = conn.execute(
            "SELECT date, close, raw_close FROM stock_prices WHERE symbol='TEST' ORDER BY date"
        ).fetchall()
        # Every stored close on the new (scaled) basis.
        for date_str, close, raw_close in rows:
            if date_str in old_closes:
                assert close == pytest.approx(old_closes[date_str] * scale)
                # raw_close keeps the original basis for the pre-existing bars.
                assert raw_close == pytest.approx(old_closes[date_str])

        reasons = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT reason FROM price_adjustment_log WHERE symbol='TEST'"
            )
        }
        conn.close()
        assert "rebase_overlap" in reasons
        assert "full_resync" in reasons

    def test_no_rebase_normal_tail_append(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        import backend.quant_pro.data_io as dio
        from backend.quant_pro.database import get_db_connection

        dates = _seed_30_bars(monkeypatch)
        old_df = _history_df([d.strftime("%Y-%m-%d") for d in dates])
        old_closes = {d.strftime("%Y-%m-%d"): c for d, c in zip(dates, old_df["Close"])}

        # Overlap matches exactly; append 2 fresh bars.
        new_dates = list(dates[-5:]) + list(
            pd.bdate_range(start=dates[-1] + pd.Timedelta(days=1), periods=2)
        )
        new_dates = [d.strftime("%Y-%m-%d") for d in new_dates]
        incr = _history_df(new_dates)
        for i, d in enumerate(new_dates[:5]):
            incr.iloc[i, incr.columns.get_loc("Close")] = old_closes[d]

        calls = {"full": 0, "incr": 0}

        def fake_fetch_chunk(symbol, start_ts, end_ts):
            ten_years_ago = (pd.Timestamp.now() - pd.Timedelta(days=365 * 9)).timestamp()
            if start_ts < ten_years_ago:
                calls["full"] += 1
            else:
                calls["incr"] += 1
            return incr.copy()

        monkeypatch.setattr(dio, "fetch_chunk", fake_fetch_chunk)
        monkeypatch.setattr(dio, "_is_streamlit_active", lambda: False)

        dio._fetch_dynamic_data("TEST")

        assert calls["full"] == 0  # no full re-fetch

        conn = get_db_connection()
        log_count = conn.execute(
            "SELECT COUNT(*) FROM price_adjustment_log WHERE symbol='TEST'"
        ).fetchone()[0]
        conn.close()
        assert log_count == 0  # overlap matched exactly

    def test_full_refetch_failure_fail_closed(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        import backend.quant_pro.data_io as dio
        from backend.quant_pro.database import get_db_connection

        dates = _seed_30_bars(monkeypatch)
        old_df = _history_df([d.strftime("%Y-%m-%d") for d in dates])
        old_closes = {d.strftime("%Y-%m-%d"): c for d, c in zip(dates, old_df["Close"])}

        scale = 1.0 / 1.2
        new_dates = list(dates[-5:]) + list(
            pd.bdate_range(start=dates[-1] + pd.Timedelta(days=1), periods=2)
        )
        new_dates = [d.strftime("%Y-%m-%d") for d in new_dates]
        incr = _history_df(new_dates)
        for col in ["Open", "High", "Low", "Close"]:
            incr[col] = incr[col] * scale
        for i, d in enumerate(new_dates[:5]):
            incr.iloc[i, incr.columns.get_loc("Close")] = old_closes[d] * scale

        def fake_fetch_chunk(symbol, start_ts, end_ts):
            ten_years_ago = (pd.Timestamp.now() - pd.Timedelta(days=365 * 9)).timestamp()
            if start_ts < ten_years_ago:
                return pd.DataFrame()  # full re-fetch fails
            return incr.copy()

        monkeypatch.setattr(dio, "fetch_chunk", fake_fetch_chunk)
        monkeypatch.setattr(dio, "_is_streamlit_active", lambda: False)

        dio._fetch_dynamic_data("TEST")

        conn = get_db_connection()
        # DB still on old basis, no tail rows appended.
        rows = conn.execute(
            "SELECT date, close FROM stock_prices WHERE symbol='TEST' ORDER BY date"
        ).fetchall()
        stored = {d: c for d, c in rows}
        for d, c in old_closes.items():
            assert stored[d] == pytest.approx(c)
        # New tail bars were never written.
        assert new_dates[5] not in stored

        pending = conn.execute(
            "SELECT symbol FROM pending_full_resync"
        ).fetchall()
        conn.close()
        assert ("TEST",) in pending


class TestIngestionHook:
    def test_ingestion_marks_failed_and_requeues(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        import backend.quant_pro.data_io as dio
        from scripts.ingestion import deterministic_daily_ingestion as ddi
        from backend.quant_pro.database import get_db_connection

        dates = _seed_30_bars(monkeypatch)
        old_df = _history_df([d.strftime("%Y-%m-%d") for d in dates])
        old_closes = {d.strftime("%Y-%m-%d"): c for d, c in zip(dates, old_df["Close"])}

        scale = 1.0 / 1.2
        new_dates = list(dates[-5:]) + list(
            pd.bdate_range(start=dates[-1] + pd.Timedelta(days=1), periods=2)
        )
        new_dates = [d.strftime("%Y-%m-%d") for d in new_dates]
        incr = _history_df(new_dates)
        for col in ["Open", "High", "Low", "Close"]:
            incr[col] = incr[col] * scale
        for i, d in enumerate(new_dates[:5]):
            incr.iloc[i, incr.columns.get_loc("Close")] = old_closes[d] * scale

        # ddi fetches via fetch_ohlcv_chunk; resync uses data_io.fetch_chunk (fails).
        monkeypatch.setattr(ddi, "fetch_ohlcv_chunk", lambda symbol, start_ts, end_ts: incr.copy())
        monkeypatch.setattr(dio, "fetch_chunk", lambda symbol, start_ts, end_ts: pd.DataFrame())

        conn = get_db_connection()
        ddi.execute_ingestion(
            conn=conn,
            symbols=["TEST"],
            source="db",
            history_days=3650,
            backfill_days=5,
            max_staleness_days=7,
            sleep_ms=0,
            strict=False,
        )

        row = conn.execute(
            "SELECT status, error FROM ingestion_run_symbols WHERE symbol='TEST'"
        ).fetchone()
        assert row[0] == "FAILED"
        assert "vendor_rebase_detected_resync_pending" in (row[1] or "")

        pending = conn.execute("SELECT symbol FROM pending_full_resync").fetchall()
        assert ("TEST",) in pending

        # Next run drains the pending queue (now the full fetch succeeds).
        full = _history_df([d.strftime("%Y-%m-%d") for d in dates] + new_dates[5:])
        for col in ["Open", "High", "Low", "Close"]:
            full[col] = full[col] * scale
        monkeypatch.setattr(dio, "fetch_chunk", lambda symbol, start_ts, end_ts: full.copy())
        # Make the next run's tail fetch a clean (matching) overlap to avoid a new rebase.
        clean_incr = _history_df([d.strftime("%Y-%m-%d") for d in dates[-5:]])
        for col in ["Open", "High", "Low", "Close"]:
            clean_incr[col] = clean_incr[col] * scale
        for i, d in enumerate([dd.strftime("%Y-%m-%d") for dd in dates[-5:]]):
            clean_incr.iloc[i, clean_incr.columns.get_loc("Close")] = old_closes[d] * scale
        monkeypatch.setattr(ddi, "fetch_ohlcv_chunk", lambda symbol, start_ts, end_ts: clean_incr.copy())

        ddi.execute_ingestion(
            conn=conn,
            symbols=["TEST"],
            source="db",
            history_days=3650,
            backfill_days=5,
            max_staleness_days=7,
            sleep_ms=0,
            strict=False,
        )
        pending_after = conn.execute("SELECT symbol FROM pending_full_resync").fetchall()
        conn.close()
        assert ("TEST",) not in pending_after  # drained

    def test_max_full_resyncs_cap(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        import backend.quant_pro.data_io as dio
        from scripts.ingestion import deterministic_daily_ingestion as ddi
        from backend.quant_pro.database import init_db, save_to_db, get_db_connection

        init_db()
        scale = 1.0 / 1.2
        symbols = ["AAA", "BBB", "CCC"]
        per_symbol = {}
        for sym in symbols:
            dates = pd.bdate_range(
                end=pd.Timestamp.now().normalize() - pd.Timedelta(days=5), periods=30
            )
            df = _history_df([d.strftime("%Y-%m-%d") for d in dates])
            save_to_db(df, sym)
            old_closes = {d.strftime("%Y-%m-%d"): c for d, c in zip(dates, df["Close"])}
            new_dates = [d.strftime("%Y-%m-%d") for d in dates[-5:]] + [
                d.strftime("%Y-%m-%d")
                for d in pd.bdate_range(start=dates[-1] + pd.Timedelta(days=1), periods=2)
            ]
            incr = _history_df(new_dates)
            for col in ["Open", "High", "Low", "Close"]:
                incr[col] = incr[col] * scale
            for i, d in enumerate(new_dates[:5]):
                incr.iloc[i, incr.columns.get_loc("Close")] = old_closes[d] * scale
            per_symbol[sym] = incr

        def fake_ohlcv(symbol, start_ts, end_ts):
            return per_symbol[symbol].copy()

        # Resync full fetch always succeeds (returns scaled history).
        def fake_full(symbol, start_ts, end_ts):
            dates = pd.bdate_range(
                end=pd.Timestamp.now().normalize() - pd.Timedelta(days=3), periods=32
            )
            df = _history_df([d.strftime("%Y-%m-%d") for d in dates])
            for col in ["Open", "High", "Low", "Close"]:
                df[col] = df[col] * scale
            return df

        monkeypatch.setattr(ddi, "fetch_ohlcv_chunk", fake_ohlcv)
        monkeypatch.setattr(dio, "fetch_chunk", fake_full)

        conn = get_db_connection()
        ddi.execute_ingestion(
            conn=conn,
            symbols=symbols,
            source="db",
            history_days=3650,
            backfill_days=5,
            max_staleness_days=7,
            sleep_ms=0,
            strict=False,
            max_full_resyncs=1,
        )
        pending = {r[0] for r in conn.execute("SELECT symbol FROM pending_full_resync")}
        conn.close()
        # One resynced inline, the other two queued.
        assert len(pending) == 2


class TestBackfillSymbol:
    def test_backfill_symbol_abort_and_resync(self, tmp_path, monkeypatch):
        _reset_db(tmp_path, monkeypatch)
        import setup_data
        import backend.quant_pro.data_io as dio
        from backend.quant_pro.database import init_db, save_to_db, get_db_connection
        import backend.quant_pro.vendor_api as vendor_api

        init_db()
        # Deep DB (older history).
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=5), periods=60)
        df = _history_df([d.strftime("%Y-%m-%d") for d in dates])
        save_to_db(df, "TEST")
        old_closes = {d.strftime("%Y-%m-%d"): c for d, c in zip(dates, df["Close"])}

        scale = 1.0 / 1.2
        # 760-day window fetch returns a re-based slice that overlaps stored bars.
        window = _history_df([d.strftime("%Y-%m-%d") for d in dates[-20:]])
        for col in ["Open", "High", "Low", "Close"]:
            window[col] = window[col] * scale
        for i, d in enumerate([dd.strftime("%Y-%m-%d") for dd in dates[-20:]]):
            window.iloc[i, window.columns.get_loc("Close")] = old_closes[d] * scale

        resync_called = {"n": 0}

        def fake_full(symbol, start_ts, end_ts):
            resync_called["n"] += 1
            full = _history_df([d.strftime("%Y-%m-%d") for d in dates])
            for col in ["Open", "High", "Low", "Close"]:
                full[col] = full[col] * scale
            return full

        monkeypatch.setattr(vendor_api, "fetch_ohlcv_chunk", lambda symbol, s, e: window.copy())
        monkeypatch.setattr(
            setup_data, "fetch_ohlcv_chunk", lambda symbol, s, e: window.copy(), raising=False
        )
        monkeypatch.setattr(dio, "fetch_chunk", fake_full)

        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=760)
        setup_data.backfill_symbol("TEST", start, end)

        assert resync_called["n"] == 1  # full resync invoked

        conn = get_db_connection()
        # No window-boundary splice: all stored closes on the new basis.
        rows = conn.execute(
            "SELECT date, close FROM stock_prices WHERE symbol='TEST' ORDER BY date"
        ).fetchall()
        for d, c in rows:
            if d in old_closes:
                assert c == pytest.approx(old_closes[d] * scale)
        conn.close()
