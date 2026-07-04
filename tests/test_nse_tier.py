import datetime as dt
import os
import sys
import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import backfill_missing_history as bf

DB_URL = os.environ["TEST_DB"]
engine = create_engine(DB_URL)


def fake_yahoo_none(ticker):
    return None


def fake_nse_stock_df(symbol, from_date, to_date, series="EQ"):
    """Mimics jugaad_data's real column layout, including a symbol that
    switched SERIES mid-range (duplicate DATE under two series) to verify
    the real fetch_nse() dedup/rename logic, not just a mocked shortcut."""
    if symbol != "OLDIN":
        return pd.DataFrame()  # empty -> None after fetch_nse's .empty check
    rows = [
        {"DATE": dt.date(2019, 1, 2), "SERIES": "SM", "OPEN": 100.0, "HIGH": 101.0, "LOW": 99.0,
         "PREV. CLOSE": 99.5, "LTP": 100.5, "CLOSE": 100.5, "VWAP": 100.2, "VOLUME": 1000,
         "VALUE": 100200.0, "NO OF TRADES": 10, "DELIVERY QTY": 500, "DELIVERY %": 50.0, "SYMBOL": "OLDIN"},
        # same date, second series entry (simulates a series switch) — must collapse to ONE row
        {"DATE": dt.date(2019, 1, 2), "SERIES": "EQ", "OPEN": 100.0, "HIGH": 101.0, "LOW": 99.0,
         "PREV. CLOSE": 99.5, "LTP": 100.6, "CLOSE": 100.6, "VWAP": 100.3, "VOLUME": 1200,
         "VALUE": 120360.0, "NO OF TRADES": 12, "DELIVERY QTY": 600, "DELIVERY %": 50.0, "SYMBOL": "OLDIN"},
        {"DATE": dt.date(2019, 1, 3), "SERIES": "EQ", "OPEN": 101.0, "HIGH": 102.0, "LOW": 100.0,
         "PREV. CLOSE": 100.6, "LTP": 101.5, "CLOSE": 101.5, "VWAP": 101.2, "VOLUME": 900,
         "VALUE": 91080.0, "NO OF TRADES": 9, "DELIVERY QTY": 400, "DELIVERY %": 44.0, "SYMBOL": "OLDIN"},
        # a date already present in the seeded warehouse for OLDIN -> must be skipped on insert
        {"DATE": dt.date(2026, 6, 1), "SERIES": "EQ", "OPEN": 500.0, "HIGH": 505.0, "LOW": 495.0,
         "PREV. CLOSE": 500.0, "LTP": 502.0, "CLOSE": 502.0, "VWAP": 501.0, "VOLUME": 300,
         "VALUE": 150300.0, "NO OF TRADES": 5, "DELIVERY QTY": 200, "DELIVERY %": 66.0, "SYMBOL": "OLDIN"},
    ]
    return pd.DataFrame(rows)


def row_count(table, symbol):
    with engine.begin() as conn:
        return conn.execute(text(f"""
            SELECT COUNT(*) FROM {table} t
            JOIN ltm_symbols s ON s.symbol_id = t.symbol_id
            WHERE s.symbol = :sym
        """), {"sym": symbol}).scalar()


def test_nse_tier_used_only_for_india_and_dedups_series_switch():
    bf.fetch_yahoo = fake_yahoo_none
    bf.fetch_nse = lambda symbol, lookback_years: bf.fetch_nse.__wrapped__(symbol, lookback_years) \
        if hasattr(bf.fetch_nse, "__wrapped__") else None
    # patch the real fetch_nse's internal import target instead, so the
    # actual rename/dedup transformation code runs for real
    import jugaad_data.nse as jn
    jn.stock_df = fake_nse_stock_df
    # reload fetch_nse to its original (un-monkeypatched) implementation
    import importlib
    importlib.reload(bf)
    bf.fetch_yahoo = fake_yahoo_none
    jn.stock_df = fake_nse_stock_df

    before = row_count("ltm_fundamentals_fact", "OLDIN")
    with engine.connect() as conn:
        trans = conn.begin()
        row = {"country": "IN", "symbol": "OLDIN", "yahoo_ticker": "OLDIN.NS", "gfinance_query": "OLDIN:NSE"}
        result = bf.process_row(conn, row, apply_changes=True, serpapi_key=None, sleep_s=0, nse_lookback_years=10)
        trans.commit()
    after = row_count("ltm_fundamentals_fact", "OLDIN")
    added = after - before
    assert result["source"] == "nse (bhavcopy)", result
    # 4 raw rows in, 1 is a duplicate DATE (series switch) -> collapses to 1,
    # 1 more overlaps an existing fact date (2026-06-01) -> skipped entirely.
    # Expect exactly 2 new rows: 2019-01-02 (deduped) and 2019-01-03.
    assert added == 2, (added, result)
    assert result["rows_added"] == 2, result
    print(f"OK nse_tier_used_only_for_india_and_dedups_series_switch: added {added} rows (expected 2)")

    # Non-IN country must never call fetch_nse at all
    with engine.connect() as conn:
        trans = conn.begin()
        row2 = {"country": "US", "symbol": "NEWCO", "yahoo_ticker": "NEWCO", "gfinance_query": "NEWCO:NASDAQ"}
        result2 = bf.process_row(conn, row2, apply_changes=True, serpapi_key=None, sleep_s=0, nse_lookback_years=10)
        trans.commit()
    assert result2["source"] != "nse (bhavcopy)", result2
    print(f"OK non_india_country_skips_nse_tier: source={result2['source']!r}")


def test_india_symbol_with_no_data_anywhere_falls_through_cleanly():
    bf.fetch_yahoo = fake_yahoo_none
    import jugaad_data.nse as jn
    jn.stock_df = fake_nse_stock_df  # returns empty DataFrame for anything but OLDIN
    with engine.connect() as conn:
        trans = conn.begin()
        row = {"country": "IN", "symbol": "BARC", "yahoo_ticker": "BARC.NS", "gfinance_query": "BARC:NSE"}
        # BARC isn't actually an IN symbol in our seed data but that's fine for this check —
        # we just need a symbol that exists in ltm_symbols under some country.
        result = bf.process_row(conn, {**row, "symbol": "NEWCO"}, apply_changes=True, serpapi_key=None,
                                 sleep_s=0, nse_lookback_years=10)
        trans.rollback()
    assert result["source"] == "none", result
    assert "no yahoo/nse data" in result["note"], result
    print(f"OK india_symbol_with_no_data_anywhere: note={result['note']!r}")


if __name__ == "__main__":
    test_nse_tier_used_only_for_india_and_dedups_series_switch()
    test_india_symbol_with_no_data_anywhere_falls_through_cleanly()
    print("\nAll NSE-tier tests passed.")
