"""
Build a full reference list of every stock in the warehouse alongside its
resolved exchange — a persistent answer to "what exchange is this symbol
on" so future scripts (backfill, screeners, anything else) can look it up
directly instead of re-guessing from country + ticker format every time.

Covers every symbol currently in the warehouse (not just the thin-history
subset from find_thin_history_tickers.py).

Usage:
    DB=postgresql://user:pass@host:port/dbname python3 build_stock_exchange_list.py \
        [--out stock_exchange_list.csv] [--store-table]

--store-table also writes the result into a new ltm_symbol_exchange table
in the warehouse (symbol_id, country, exchange, yahoo_ticker,
gfinance_query, mapped), so other code in the warehouse can join against
it instead of importing this script's mapping logic.
"""
import argparse
import csv
import os
import sys
from sqlalchemy import create_engine, text

from exchange_map import resolve_exchange_label

FACT_TABLE = "ltm_fundamentals_fact"
SYMBOLS_TABLE = "ltm_symbols"
OUTPUT_TABLE = "ltm_symbol_exchange"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="stock_exchange_list.csv")
    ap.add_argument("--store-table", action="store_true",
                     help="also write the result into ltm_symbol_exchange in the warehouse")
    args = ap.parse_args()

    db_url = os.environ.get("DB")
    if not db_url:
        print("Set DB=postgresql://user:pass@host:port/dbname", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(db_url)
    with engine.begin() as conn:
        rows = conn.execute(text(f"""
            SELECT DISTINCT f.country, s.symbol_id, s.symbol
            FROM {FACT_TABLE} f
            JOIN {SYMBOLS_TABLE} s ON s.symbol_id = f.symbol_id
            ORDER BY f.country, s.symbol
        """)).fetchall()

    out_rows = []
    unmapped_countries = {}
    for country, symbol_id, symbol in rows:
        yahoo_ticker, gfinance_query, exchange, mapped = resolve_exchange_label(symbol, country)
        out_rows.append({
            "country": country,
            "symbol": symbol,
            "symbol_id": symbol_id,
            "exchange": exchange or "",
            "yahoo_ticker": yahoo_ticker,
            "gfinance_query": gfinance_query,
            "mapped": mapped,
        })
        if not mapped:
            unmapped_countries[country] = unmapped_countries.get(country, 0) + 1

    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "country", "symbol", "symbol_id", "exchange", "yahoo_ticker", "gfinance_query", "mapped",
        ])
        writer.writeheader()
        writer.writerows(out_rows)

    mapped_count = sum(1 for r in out_rows if r["mapped"])
    print(f"Wrote {len(out_rows)} symbols to {args.out} ({mapped_count} with a resolved exchange, "
          f"{len(out_rows) - mapped_count} unmapped).")
    if unmapped_countries:
        print(f"Unmapped by country: {unmapped_countries}")

    print("\nExchange breakdown:")
    counts = {}
    for r in out_rows:
        key = (r["country"], r["exchange"] or "(unmapped)")
        counts[key] = counts.get(key, 0) + 1
    for (country, exchange), count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {country:<5}{exchange:<12}{count}")

    if args.store_table:
        with engine.begin() as conn:
            # Recomputed fully each run — drop and recreate rather than
            # TRUNCATE, so a stale schema from a previous failed run (e.g.
            # the old single-column symbol_id PK) can't linger and cause a
            # silent CREATE TABLE IF NOT EXISTS no-op.
            conn.execute(text(f"DROP TABLE IF EXISTS {OUTPUT_TABLE}"))
            conn.execute(text(f"""
                CREATE TABLE {OUTPUT_TABLE} (
                    symbol_id INT4 NOT NULL REFERENCES {SYMBOLS_TABLE}(symbol_id),
                    country CHAR(2) NOT NULL,
                    exchange TEXT,
                    yahoo_ticker TEXT NOT NULL,
                    gfinance_query TEXT NOT NULL,
                    mapped BOOLEAN NOT NULL,
                    PRIMARY KEY (symbol_id, country)
                )
            """))
            conn.execute(text(f"""
                INSERT INTO {OUTPUT_TABLE} (symbol_id, country, exchange, yahoo_ticker, gfinance_query, mapped)
                VALUES (:symbol_id, :country, :exchange, :yahoo_ticker, :gfinance_query, :mapped)
            """), out_rows)
        print(f"\nAlso stored in warehouse table {OUTPUT_TABLE} ({len(out_rows)} rows).")


if __name__ == "__main__":
    main()
