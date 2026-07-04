import os
import datetime as dt
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DB"])

TODAY = dt.date(2026, 7, 4)  # fixed "today" for reproducible test


def business_days(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


with engine.begin() as conn:
    symbols = ["AAPL", "MSFT", "NEWCO", "GAPCO", "RELI", "OLDIN", "BARC"]
    country_of = {
        "AAPL": "US", "MSFT": "US", "NEWCO": "US", "GAPCO": "US",
        "RELI": "IN", "OLDIN": "IN", "BARC": "GB",
    }
    ids = {}
    for s in symbols:
        r = conn.execute(text("INSERT INTO ltm_symbols (symbol) VALUES (:s) RETURNING symbol_id"), {"s": s})
        ids[s] = r.scalar()

    rows = []

    # AAPL, MSFT: full 6-year history, US -> "sufficient"
    for sym in ["AAPL", "MSFT"]:
        start = TODAY - dt.timedelta(days=365 * 6)
        for d in business_days(start, TODAY):
            rows.append((country_of[sym], ids[sym], d, 100.0, 101.0, 99.0, 100.5, 1_000_000))

    # NEWCO: only 6 months of history -> "short_history"
    start = TODAY - dt.timedelta(days=182)
    for d in business_days(start, TODAY):
        rows.append((country_of["NEWCO"], ids["NEWCO"], d, 10.0, 10.5, 9.5, 10.2, 50_000))

    # GAPCO: spans 6 years but only ~40% of business days present -> "gappy"
    start = TODAY - dt.timedelta(days=365 * 6)
    for i, d in enumerate(business_days(start, TODAY)):
        if i % 5 == 0:  # keep only 1 in 5 days
            rows.append((country_of["GAPCO"], ids["GAPCO"], d, 20.0, 20.5, 19.5, 20.2, 200_000))

    # RELI: full 5.5-year history, IN -> "sufficient"
    start = TODAY - dt.timedelta(days=365 * 5 + 200)
    for d in business_days(start, TODAY):
        rows.append((country_of["RELI"], ids["RELI"], d, 2500.0, 2550.0, 2480.0, 2510.0, 300_000))

    # OLDIN: 3-year history, IN -> "short_history"
    start = TODAY - dt.timedelta(days=365 * 3)
    for d in business_days(start, TODAY):
        rows.append((country_of["OLDIN"], ids["OLDIN"], d, 500.0, 505.0, 495.0, 502.0, 80_000))

    # BARC: 4.5-year history, GB -> "short_history"
    start = TODAY - dt.timedelta(days=365 * 4 + 180)
    for d in business_days(start, TODAY):
        rows.append((country_of["BARC"], ids["BARC"], d, 150.0, 152.0, 148.0, 151.0, 60_000))

    conn.execute(
        text(
            "INSERT INTO ltm_fundamentals_fact (country, symbol_id, dt, open_p, high_p, low_p, close_p, volume) "
            "VALUES (:c, :sid, :d, :o, :h, :l, :cl, :v)"
        ),
        [
            {"c": c, "sid": sid, "d": d, "o": o, "h": h, "l": l, "cl": cl, "v": v}
            for (c, sid, d, o, h, l, cl, v) in rows
        ],
    )

print(f"Seeded {len(rows)} rows across {len(symbols)} symbols")
