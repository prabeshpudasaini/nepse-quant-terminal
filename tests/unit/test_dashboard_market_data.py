import sqlite3

import pandas as pd

from backend.quant_pro import dashboard_data


def test_md_refresh_uses_market_quotes_when_only_latest_session_exists(tmp_path, monkeypatch):
    db_path = tmp_path / "dashboard.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE stock_prices (
            symbol TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE market_quotes (
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
            PRIMARY KEY (raw_id, symbol)
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO stock_prices (symbol, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("AAA", "2026-04-07", 110.0, 110.0, 110.0, 110.0, 1000.0),
            ("BBB", "2026-04-07", 90.0, 90.0, 90.0, 90.0, 500.0),
            ("NEPSE", "2026-04-07", 2700.0, 2710.0, 2690.0, 2705.0, 1500.0),
        ],
    )
    conn.executemany(
        """
        INSERT INTO market_quotes (
            raw_id, symbol, security_id, security_name, last_traded_price, close_price,
            previous_close, percentage_change, total_trade_quantity, source, fetched_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "AAA", None, None, 110.0, 110.0, 100.0, 10.0, 1000.0, "test", "2026-04-06T19:55:33+00:00"),
            (1, "BBB", None, None, 90.0, 90.0, 100.0, -10.0, 500.0, "test", "2026-04-06T19:55:33+00:00"),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(dashboard_data, "_db", lambda: sqlite3.connect(str(db_path)))

    md = dashboard_data.MD(5)

    assert md.err is None
    assert md.latest == "2026-04-07"
    assert md.prev_d == "—"
    assert list(md.gainers["symbol"]) == ["AAA", "BBB"]
    assert list(md.losers["symbol"]) == ["BBB", "AAA"]
    assert round(float(md.gainers.iloc[0]["chg"]), 2) == 10.0
    assert round(float(md.losers.iloc[0]["chg"]), 2) == -10.0
    assert len(md.quotes) == 2


def test_md_refresh_dedupes_duplicate_symbol_session_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "dashboard.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE stock_prices (
            symbol TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE market_quotes (
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
            fetched_at_utc TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO stock_prices (symbol, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("AAA", "2026-04-06", 100.0, 100.0, 100.0, 100.0, 1000.0),
            ("AAA", "2026-04-06", 101.0, 101.0, 101.0, 100.0, 1000.0),
            ("BBB", "2026-04-06", 100.0, 100.0, 100.0, 100.0, 1000.0),
            ("AAA", "2026-04-07", 109.0, 111.0, 108.0, 110.0, 1500.0),
            ("AAA", "2026-04-07", 109.0, 111.0, 108.0, 110.0, 1500.0),
            ("BBB", "2026-04-07", 89.0, 91.0, 88.0, 90.0, 2500.0),
            ("BBB", "2026-04-07", 89.0, 91.0, 88.0, 90.0, 2500.0),
            ("NEPSE", "2026-04-07", 2700.0, 2710.0, 2690.0, 2705.0, 1500.0),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(dashboard_data, "_db", lambda: sqlite3.connect(str(db_path)))

    md = dashboard_data.MD(5)

    assert md.err is None
    assert list(md.df["symbol"]) == ["AAA", "BBB"]
    assert list(md.gainers["symbol"]) == ["AAA", "BBB"]
    assert list(md.losers["symbol"]) == ["BBB", "AAA"]
    assert list(md.vol_top["symbol"]) == ["BBB", "AAA"]


def test_load_port_returns_empty_frame_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dashboard_data, "PAPER_PORTFOLIO_FILE", tmp_path / "paper_portfolio.csv")

    port = dashboard_data.load_port()

    assert isinstance(port, pd.DataFrame)
    assert port.empty


def test_exec_buy_then_sell_all_round_trips_paper_book(tmp_path, monkeypatch):
    db_path = tmp_path / "prices.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE stock_prices (
            symbol TEXT,
            date TEXT,
            close REAL
        )
        """
    )
    conn.execute(
        "INSERT INTO stock_prices (symbol, date, close) VALUES (?, ?, ?)",
        ("AAA", "2026-04-07", 100.0),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        dashboard_data, "PAPER_PORTFOLIO_FILE", tmp_path / "paper_portfolio.csv")
    monkeypatch.setattr(dashboard_data, "_db", lambda: sqlite3.connect(str(db_path)))

    res = dashboard_data.exec_buy("AAA", "10", "")
    assert res.startswith("BUY  10xAAA @ 100.0")

    port = dashboard_data.load_port()
    assert len(port) == 1
    row = port.iloc[0]
    assert row["Symbol"] == "AAA"
    assert int(row["Quantity"]) == 10
    assert float(row["Buy_Price"]) == 100.0
    # fees = amt * 0.004 = 1000 * 0.004 = 4.0; cost = amt + fees = 1004.0
    assert float(row["Buy_Fees"]) == 4.0
    assert float(row["Total_Cost_Basis"]) == 1004.0

    res = dashboard_data.exec_sell("AAA", "all", "")
    assert res.startswith("SELL  10xAAA @ 100.0")
    assert dashboard_data.load_port().empty
