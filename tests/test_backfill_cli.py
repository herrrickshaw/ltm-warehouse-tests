"""
Integration smoke test of backfill_missing_history.py's actual CLI
entrypoint (argument parsing, CSV loading, the dry-run/--apply loop) —
not just the process_row logic tested in test_backfill_logic.py.

Expects run_tests.sh to have already run find_thin_history_tickers.py
against TEST_DB to produce the thin_history.csv fixture this reads.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import backfill_missing_history as bf

CSV_PATH = os.path.join(os.path.dirname(__file__), "thin_history.csv")


def fake_yahoo(ticker):
    return None  # force every ticker through the serpapi fallback path for this smoke test


def fake_serpapi(query, api_key, window="5Y"):
    return [("2022-06-01", 42.0)]


bf.fetch_yahoo = fake_yahoo
bf.fetch_serpapi = fake_serpapi
# This is a Yahoo/SerpApi smoke test — stub the NSE tier so an IN-country
# row in the CSV can't trigger a real (network-blocked) NSE call.
bf.fetch_nse = lambda symbol, lookback_years: None

sys.argv = [
    "backfill_missing_history.py",
    "--csv", CSV_PATH,
    "--serpapi-key", "FAKEKEY123",
    "--limit", "2",
    "--sleep", "0",
    # dry-run: no --apply
]
bf.main()
print("\n--- now with --apply ---\n")
sys.argv.append("--apply")
bf.main()
