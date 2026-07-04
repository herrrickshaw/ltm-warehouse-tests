"""
Find symbols in the warehouse with insufficient history (< N years) or with
"gappy" history (spans N years but is missing a large fraction of trading
days inside that span). Attaches the exchange info needed to backfill each
one from Yahoo Finance / Google Finance, based on the country already
recorded on the fact table.

Usage:
    DB=postgresql://user:pass@host:port/dbname python3 find_thin_history_tickers.py \
        [--min-years 5] [--gap-threshold 0.85] [--out thin_history.csv]

Output: prints a summary table and writes a CSV that
backfill_missing_history.py consumes directly.
"""
import argparse
import csv
import os
import sys
import datetime as dt
from sqlalchemy import create_engine, text

from exchange_map import EXCHANGE_MAP, resolve_ticker

FACT_TABLE = "ltm_fundamentals_fact"
SYMBOLS_TABLE = "ltm_symbols"


def business_days_between(start, end):
    days = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            days += 1
        d += dt.timedelta(days=1)
    return days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-years", type=float, default=5.0)
    ap.add_argument("--gap-threshold", type=float, default=0.85,
                     help="flag as gappy if actual/expected trading days falls below this")
    ap.add_argument("--min-rows", type=int, default=20,
                     help="exclude symbols with fewer than this many total rows — a single-digit "
                          "row count with 0-day span is far more likely a bond/NCD ISIN or other "
                          "non-equity instrument masquerading as a 'new' stock than real short history")
    ap.add_argument("--out", default="thin_history.csv")
    args = ap.parse_args()

    db_url = os.environ.get("DB")
    if not db_url:
        print("Set DB=postgresql://user:pass@host:port/dbname", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(db_url)
    with engine.begin() as conn:
        rows = conn.execute(text(f"""
            SELECT f.country, s.symbol, MIN(f.dt) AS min_dt, MAX(f.dt) AS max_dt, COUNT(*) AS row_count
            FROM {FACT_TABLE} f
            JOIN {SYMBOLS_TABLE} s ON s.symbol_id = f.symbol_id
            GROUP BY f.country, s.symbol
            ORDER BY f.country, s.symbol
        """)).fetchall()

    flagged = []
    excluded_thin_snapshot = 0
    excluded_unmapped = 0
    unmapped_countries = set()
    for country, symbol, min_dt, max_dt, row_count in rows:
        span_years = (max_dt - min_dt).days / 365.25
        expected_days = business_days_between(min_dt, max_dt) or 1
        coverage_pct = row_count / expected_days

        if span_years < args.min_years:
            reason = "short_history"
        elif coverage_pct < args.gap_threshold:
            reason = "gappy"
        else:
            continue  # sufficient, skip

        if row_count < args.min_rows:
            excluded_thin_snapshot += 1
            continue

        mapping = EXCHANGE_MAP.get(country)
        if mapping is None:
            # Do NOT fall back to a bare-symbol query — an unqualified ticker
            # can silently match an unrelated company on a different
            # exchange and corrupt the fact table with the wrong prices
            # under this symbol_id. Exclude until the country is mapped.
            unmapped_countries.add(country)
            excluded_unmapped += 1
            continue
        yahoo_ticker, gfinance_query = resolve_ticker(symbol, mapping)

        flagged.append({
            "country": country,
            "symbol": symbol,
            "reason": reason,
            "min_dt": min_dt,
            "max_dt": max_dt,
            "span_years": round(span_years, 2),
            "row_count": row_count,
            "expected_days": expected_days,
            "coverage_pct": round(coverage_pct * 100, 1),
            "years_missing": round(max(0.0, args.min_years - span_years), 2),
            "yahoo_ticker": yahoo_ticker,
            "gfinance_query": gfinance_query,
        })

    flagged.sort(key=lambda r: (-r["years_missing"], r["coverage_pct"]))

    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(flagged[0].keys()) if flagged else [
            "country", "symbol", "reason", "min_dt", "max_dt", "span_years", "row_count",
            "expected_days", "coverage_pct", "years_missing", "yahoo_ticker", "gfinance_query",
        ])
        writer.writeheader()
        writer.writerows(flagged)

    total = len(rows)
    print(f"Checked {total} symbols. Flagged {len(flagged)} with < {args.min_years}y "
          f"history or < {args.gap_threshold:.0%} day-coverage within their span.")
    if excluded_thin_snapshot:
        print(f"Excluded {excluded_thin_snapshot} more that had fewer than {args.min_rows} total rows — "
              f"these look like bond/NCD ISINs or other non-equity entries (single-day snapshot data), "
              f"not stocks with genuinely short history. Lower --min-rows if that's wrong for your data.")
    if unmapped_countries:
        print(f"Excluded {excluded_unmapped} symbols in unmapped countries ({sorted(unmapped_countries)}) "
              f"rather than guessing an exchange — add them to EXCHANGE_MAP first if you want those covered.")
    print()

    if flagged:
        print(f"{'SYMBOL':<10}{'COUNTRY':<9}{'REASON':<15}{'SPAN(y)':<9}{'COVERAGE':<10}{'MISSING(y)':<11}")
        for r in flagged[:50]:
            print(f"{r['symbol']:<10}{r['country']:<9}{r['reason']:<15}{r['span_years']:<9}"
                  f"{r['coverage_pct']:<10}{r['years_missing']:<11}")
        if len(flagged) > 50:
            print(f"... and {len(flagged) - 50} more (see {args.out})")

    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
