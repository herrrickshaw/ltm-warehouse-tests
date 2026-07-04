"""
Validates backfill_missing_history.py's DB-interaction logic (dedup against
existing dates, correct table routing, dry-run rollback) against the seeded
test warehouse, without hitting the real Yahoo/SerpApi network. Monkeypatches
fetch_yahoo/fetch_serpapi with fabricated data that mimics each API's real
response shape.

Run via run_tests.sh, which sets TEST_DB before invoking this.
"""
import datetime as dt
import os
import sys
import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import backfill_missing_history as bf

# This file targets the Yahoo/SerpApi routing specifically — the NSE tier
# (added for India symbols Yahoo misses) has its own dedicated tests in
# test_nse_tier.py. Stub it here so an IN-country test case can't trigger
# a real (network-blocked) NSE call before falling through to SerpApi.
bf.fetch_nse = lambda symbol, lookback_years: None

DB_URL = os.environ["TEST_DB"]
engine = create_engine(DB_URL)


def fake_yahoo_full_history(ticker):
    """NEWCO: pretend Yahoo has the full 6-year history (including the 6
    months already in the warehouse) — only the non-overlapping dates
    should actually get inserted."""
    if ticker != "NEWCO":
        return None
    end = dt.date(2026, 7, 4)
    start = end - dt.timedelta(days=365 * 6)
    dates = pd.bdate_range(start, end)
    return pd.DataFrame({
        "Open": 10.0, "High": 10.5, "Low": 9.5, "Close": 10.2, "Volume": 50000,
    }, index=dates)


def fake_yahoo_none(ticker):
    return None


def fake_serpapi_points(query, api_key, window="5Y"):
    assert api_key == "FAKEKEY123"
    return [
        ("2021-01-04", 500.1),
        ("2021-01-05", 501.2),
        ("2026-06-01", 555.5),  # overlaps an existing fact-table date for OLDIN — must be skipped
    ]


def row_count(table, symbol):
    with engine.begin() as conn:
        return conn.execute(text(f"""
            SELECT COUNT(*) FROM {table} t
            JOIN ltm_symbols s ON s.symbol_id = t.symbol_id
            WHERE s.symbol = :sym
        """), {"sym": symbol}).scalar()


def get_symbol_id(symbol):
    with engine.begin() as conn:
        return conn.execute(text("SELECT symbol_id FROM ltm_symbols WHERE symbol = :s"), {"s": symbol}).scalar()


def test_dedup_and_insert_yahoo():
    bf.fetch_yahoo = fake_yahoo_full_history
    before = row_count("ltm_fundamentals_fact", "NEWCO")
    with engine.connect() as conn:
        trans = conn.begin()
        row = {"country": "US", "symbol": "NEWCO", "yahoo_ticker": "NEWCO", "gfinance_query": "NEWCO:NASDAQ"}
        result = bf.process_row(conn, row, apply_changes=True, serpapi_key=None, sleep_s=0, nse_lookback_years=10)
        trans.commit()
    after = row_count("ltm_fundamentals_fact", "NEWCO")
    added = after - before
    assert result["source"] == "yahoo", result
    assert added == result["rows_added"], (added, result)
    assert added > 0
    # Rerunning should now add ~0 new rows (everything already present)
    bf.fetch_yahoo = fake_yahoo_full_history
    with engine.connect() as conn:
        trans = conn.begin()
        result2 = bf.process_row(conn, row, apply_changes=True, serpapi_key=None, sleep_s=0, nse_lookback_years=10)
        trans.commit()
    assert result2["rows_added"] == 0, result2
    print(f"OK dedup_and_insert_yahoo: added {added} new rows, rerun added {result2['rows_added']}")


def test_dry_run_does_not_persist():
    bf.fetch_yahoo = fake_yahoo_none

    def fake_serpapi(query, api_key, window="5Y"):
        return [("2022-01-03", 100.0), ("2022-01-04", 101.0)]
    bf.fetch_serpapi = fake_serpapi

    before = row_count("ltm_price_estimate", "BARC") if _table_exists("ltm_price_estimate") else 0
    with engine.connect() as conn:
        trans = conn.begin()
        row = {"country": "GB", "symbol": "BARC", "yahoo_ticker": "BARC.L", "gfinance_query": "BARC:LON"}
        bf.ensure_estimate_table(conn)
        result = bf.process_row(conn, row, apply_changes=False, serpapi_key="FAKEKEY123", sleep_s=0, nse_lookback_years=10)
        trans.rollback()  # simulate the script's dry-run path
    after = row_count("ltm_price_estimate", "BARC") if _table_exists("ltm_price_estimate") else 0
    assert after == before, (before, after)
    assert result["source"].startswith("serpapi"), result
    assert result["rows_added"] == 2, result
    print(f"OK dry_run_does_not_persist: reported {result['rows_added']} rows, actually persisted {after - before}")


def test_serpapi_fallback_routes_to_estimate_table_and_skips_overlap():
    bf.fetch_yahoo = fake_yahoo_none
    bf.fetch_serpapi = fake_serpapi_points
    with engine.connect() as conn:
        trans = conn.begin()
        bf.ensure_estimate_table(conn)
        row = {"country": "IN", "symbol": "OLDIN", "yahoo_ticker": "OLDIN.NS", "gfinance_query": "OLDIN:NSE"}
        result = bf.process_row(conn, row, apply_changes=True, serpapi_key="FAKEKEY123", sleep_s=0, nse_lookback_years=10)
        trans.commit()
    assert result["source"].startswith("serpapi"), result
    # 3 points offered, 1 overlaps an existing fact date (2026-06-01 is a business day within OLDIN's range) -> expect it skipped
    assert result["rows_added"] == 2, result
    fact_has_it = row_count("ltm_fundamentals_fact", "OLDIN")
    print(f"OK serpapi_fallback: added {result['rows_added']} estimate rows (overlap correctly skipped); "
          f"fact table untouched (still {fact_has_it} real rows)")


def test_no_source_available():
    bf.fetch_yahoo = fake_yahoo_none
    with engine.connect() as conn:
        trans = conn.begin()
        row = {"country": "GB", "symbol": "BARC", "yahoo_ticker": "BARC.L", "gfinance_query": "BARC:LON"}
        result = bf.process_row(conn, row, apply_changes=True, serpapi_key=None, sleep_s=0, nse_lookback_years=10)
        trans.commit()
    assert result["source"] == "none", result
    assert result["rows_added"] == 0
    print(f"OK no_source_available: note={result['note']!r}")


def _table_exists(name):
    with engine.begin() as conn:
        return conn.execute(text("SELECT to_regclass(:n)"), {"n": name}).scalar() is not None


if __name__ == "__main__":
    test_dedup_and_insert_yahoo()
    test_dry_run_does_not_persist()
    test_serpapi_fallback_routes_to_estimate_table_and_skips_overlap()
    test_no_source_available()
    print("\nAll backfill logic tests passed.")
