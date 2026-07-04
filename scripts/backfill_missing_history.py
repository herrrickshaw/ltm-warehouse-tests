"""
Backfill missing history for tickers flagged by find_thin_history_tickers.py.

Three-tier strategy, cheapest/best source first:

1. Yahoo Finance (via `yfinance`, free, no API key) — returns REAL daily
   OHLCV. Inserted straight into ltm_fundamentals_fact, same as any other
   warehouse row.
2. NSE direct, India only (via `jugaad_data`, free, no API key) — Yahoo's
   `.NS` index mostly covers NSE main-board stocks and misses SME/Emerge
   platform names. jugaad_data's stock_df() hits NSE's own historical API
   and, per its own code, requests ALL series (not just "EQ") whenever
   you ask for "EQ" — so it picks up SME-platform history Yahoo doesn't
   have. Still REAL OHLCV, so it also goes straight into
   ltm_fundamentals_fact. Only tried for country == "IN", and only after
   Yahoo has already come back empty for that symbol.
3. Google Finance (via SerpApi, requires SERPAPI_KEY, paid beyond free
   tier) — only returns a price/time graph, no open/high/low/volume. Used
   only as a last resort when neither of the above has anything. Inserted
   into a separate ltm_price_estimate table (close price only, tagged
   with source='serpapi') so it can never be mistaken for real OHLCV
   downstream.

Never overwrites existing rows (ON CONFLICT DO NOTHING against the
(country, symbol_id, dt) key) and never estimates a date that's already
covered by real OHLCV.

Usage:
    pip install yfinance requests jugaad-data
    DB=postgresql://... python3 backfill_missing_history.py --csv thin_history.csv
        [--apply] [--serpapi-key KEY] [--sleep 1.0] [--limit 20] [--nse-lookback-years 10]

Dry-run by default — pass --apply to actually write.
"""
import argparse
import csv
import datetime as dt
import os
import sys
import time
from sqlalchemy import create_engine, text

FACT_TABLE = "ltm_fundamentals_fact"
ESTIMATE_TABLE = "ltm_price_estimate"


def ensure_estimate_table(conn):
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {ESTIMATE_TABLE} (
            country CHAR(2) NOT NULL,
            symbol_id INT4 NOT NULL REFERENCES ltm_symbols(symbol_id),
            dt DATE NOT NULL,
            close_p REAL,
            source TEXT NOT NULL,
            fetched_at TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (country, symbol_id, dt)
        )
    """))


def fetch_yahoo(yahoo_ticker):
    """Real OHLCV from Yahoo Finance. Returns a DataFrame indexed by date
    with Open/High/Low/Close/Volume columns, or None if nothing found."""
    import yfinance as yf
    hist = yf.Ticker(yahoo_ticker).history(period="max", auto_adjust=False)
    if hist is None or hist.empty:
        return None
    return hist


def fetch_nse(symbol, lookback_years):
    """Real OHLCV straight from NSE (India only) via jugaad_data, covering
    SME/Emerge-platform history Yahoo's .NS index typically lacks. Returns
    a DataFrame indexed by date with Open/High/Low/Close/Volume columns
    (same shape as fetch_yahoo's output), or None if nothing found."""
    from jugaad_data.nse import stock_df as nse_stock_df

    to_date = dt.date.today()
    from_date = to_date - dt.timedelta(days=365 * lookback_years)
    raw = nse_stock_df(symbol=symbol, from_date=from_date, to_date=to_date, series="EQ")
    if raw is None or raw.empty:
        return None
    raw = raw.rename(columns={
        "DATE": "Date", "OPEN": "Open", "HIGH": "High", "LOW": "Low",
        "CLOSE": "Close", "VOLUME": "Volume",
    })
    raw["Date"] = raw["Date"].apply(lambda d: d.date() if hasattr(d, "date") else d)
    # A symbol can appear under more than one SERIES in the same date range
    # (e.g. moved from an SME series to the main board) — keep one row per
    # date since the fact table's key is (country, symbol_id, dt).
    raw = raw.sort_values("Date").drop_duplicates(subset="Date", keep="last")
    hist = raw.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
    return hist if not hist.empty else None


def fetch_serpapi(gfinance_query, api_key, window="5Y"):
    """Price-only graph points from Google Finance via SerpApi. Returns a
    list of (date, price) tuples, or None if nothing found."""
    import requests
    resp = requests.get(
        "https://serpapi.com/search",
        params={"engine": "google_finance", "q": gfinance_query, "window": window, "api_key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    graph = data.get("graph")
    if not graph:
        return None
    out = []
    for point in graph:
        date_str = point.get("date")
        price = point.get("price")
        if date_str is None or price is None:
            continue
        out.append((date_str, float(price)))
    return out or None


def existing_dates(conn, country, symbol_id, table):
    rows = conn.execute(
        text(f"SELECT dt FROM {table} WHERE country = :c AND symbol_id = :sid"),
        {"c": country, "sid": symbol_id},
    ).fetchall()
    return {r[0] for r in rows}


def insert_ohlcv_rows(conn, country, symbol_id, hist, skip_dates):
    rows = []
    for idx, r in hist.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        if d in skip_dates:
            continue
        rows.append({
            "c": country, "sid": symbol_id, "d": d,
            "o": float(r["Open"]) if r["Open"] == r["Open"] else None,
            "h": float(r["High"]) if r["High"] == r["High"] else None,
            "l": float(r["Low"]) if r["Low"] == r["Low"] else None,
            "cl": float(r["Close"]) if r["Close"] == r["Close"] else None,
            "v": int(r["Volume"]) if r["Volume"] == r["Volume"] else None,
        })
    if not rows:
        return 0
    conn.execute(text(f"""
        INSERT INTO {FACT_TABLE} (country, symbol_id, dt, open_p, high_p, low_p, close_p, volume)
        VALUES (:c, :sid, :d, :o, :h, :l, :cl, :v)
        ON CONFLICT (country, symbol_id, dt) DO NOTHING
    """), rows)
    return len(rows)


def insert_estimate_rows(conn, country, symbol_id, points, skip_dates, source):
    rows = []
    for date_str, price in points:
        try:
            d = date_str if hasattr(date_str, "year") else __import__("datetime").date.fromisoformat(date_str[:10])
        except ValueError:
            continue
        if d in skip_dates:
            continue
        rows.append({"c": country, "sid": symbol_id, "d": d, "cl": price, "src": source})
    if not rows:
        return 0
    conn.execute(text(f"""
        INSERT INTO {ESTIMATE_TABLE} (country, symbol_id, dt, close_p, source)
        VALUES (:c, :sid, :d, :cl, :src)
        ON CONFLICT (country, symbol_id, dt) DO NOTHING
    """), rows)
    return len(rows)


def process_row(conn, row, apply_changes, serpapi_key, sleep_s, nse_lookback_years):
    country, symbol = row["country"], row["symbol"]
    symbol_id = conn.execute(
        text("SELECT symbol_id FROM ltm_symbols WHERE symbol = :s"), {"s": symbol}
    ).scalar()
    if symbol_id is None:
        return {"symbol": symbol, "country": country, "source": "none", "rows_added": 0, "note": "symbol not found"}

    fact_dates = existing_dates(conn, country, symbol_id, FACT_TABLE)

    hist = None
    try:
        hist = fetch_yahoo(row["yahoo_ticker"])
    except Exception as e:
        note_yahoo = f"yahoo error: {e}"
    else:
        note_yahoo = None

    if hist is not None:
        added = insert_ohlcv_rows(conn, country, symbol_id, hist, fact_dates) if apply_changes else \
            sum(1 for idx in hist.index if (idx.date() if hasattr(idx, "date") else idx) not in fact_dates)
        time.sleep(sleep_s)
        return {"symbol": symbol, "country": country, "source": "yahoo", "rows_added": added, "note": ""}

    if country == "IN":
        try:
            hist = fetch_nse(symbol, nse_lookback_years)
        except Exception as e:
            note_yahoo = f"{note_yahoo}; nse error: {e}" if note_yahoo else f"nse error: {e}"
        else:
            if hist is not None:
                added = insert_ohlcv_rows(conn, country, symbol_id, hist, fact_dates) if apply_changes else \
                    sum(1 for idx in hist.index if (idx.date() if hasattr(idx, "date") else idx) not in fact_dates)
                time.sleep(sleep_s)
                return {"symbol": symbol, "country": country, "source": "nse (bhavcopy)", "rows_added": added, "note": ""}
        time.sleep(sleep_s)

    if not serpapi_key:
        return {"symbol": symbol, "country": country, "source": "none", "rows_added": 0,
                "note": note_yahoo or "no yahoo/nse data; no --serpapi-key given for fallback"}

    estimate_dates = existing_dates(conn, country, symbol_id, ESTIMATE_TABLE)
    skip = fact_dates | estimate_dates
    try:
        points = fetch_serpapi(row["gfinance_query"], serpapi_key)
    except Exception as e:
        return {"symbol": symbol, "country": country, "source": "none", "rows_added": 0,
                "note": f"serpapi error: {e}"}
    time.sleep(sleep_s)

    if points is None:
        return {"symbol": symbol, "country": country, "source": "none", "rows_added": 0,
                "note": "no data from any source"}

    added = insert_estimate_rows(conn, country, symbol_id, points, skip, "serpapi") if apply_changes else \
        sum(1 for date_str, _ in points)
    return {"symbol": symbol, "country": country, "source": "serpapi (estimate, close-only)",
            "rows_added": added, "note": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="thin_history.csv")
    ap.add_argument("--apply", action="store_true", help="actually write; default is dry-run")
    ap.add_argument("--serpapi-key", default=os.environ.get("SERPAPI_KEY"))
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--nse-lookback-years", type=int, default=10,
                     help="how far back to request from NSE directly for India symbols Yahoo misses")
    args = ap.parse_args()

    db_url = os.environ.get("DB")
    if not db_url:
        print("Set DB=postgresql://user:pass@host:port/dbname", file=sys.stderr)
        sys.exit(1)

    with open(args.csv) as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[: args.limit]

    engine = create_engine(db_url)
    results = []
    with engine.connect() as conn:
        trans = conn.begin()
        ensure_estimate_table(conn)
        for row in rows:
            results.append(process_row(conn, row, args.apply, args.serpapi_key, args.sleep, args.nse_lookback_years))
        if args.apply:
            trans.commit()
        else:
            trans.rollback()  # dry-run: never persist, even the estimate-table creation

    mode = "APPLIED" if args.apply else "DRY RUN (no changes written; pass --apply to commit)"
    print(f"{mode}\n")
    print(f"{'SYMBOL':<10}{'COUNTRY':<9}{'SOURCE':<32}{'ROWS':<8}NOTE")
    for r in results:
        print(f"{r['symbol']:<10}{r['country']:<9}{r['source']:<32}{r['rows_added']:<8}{r['note']}")

    no_data = [r for r in results if r["source"] == "none"]
    if no_data:
        print(f"\n{len(no_data)} symbols had no data from either source — real gaps, not fixable via this route:")
        for r in no_data:
            print(f"  {r['symbol']} ({r['country']}): {r['note']}")


if __name__ == "__main__":
    main()
