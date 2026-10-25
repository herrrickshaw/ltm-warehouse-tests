"""Offline tests for find_thin_history_tickers.main() with a fake engine."""
import csv
import datetime as dt
import sys
from contextlib import contextmanager

import find_thin_history_tickers as ft

D = dt.date


def run(monkeypatch, tmp_path, rows, *args):
    class Conn:
        def execute(self, stmt):
            class R:
                def fetchall(self_inner):
                    return rows
            return R()

    class Engine:
        @contextmanager
        def begin(self):
            yield Conn()

    monkeypatch.setattr(ft, "create_engine", lambda url: Engine())
    monkeypatch.setenv("DB", "postgresql://fake/none")
    out = tmp_path / "thin.csv"
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(out), *args])
    ft.main()
    with open(out) as f:
        return list(csv.DictReader(f))


def test_business_days_between_counts_weekdays_inclusive():
    assert ft.business_days_between(D(2026, 7, 3), D(2026, 7, 6)) == 2   # Fri..Mon
    assert ft.business_days_between(D(2026, 7, 4), D(2026, 7, 5)) == 0   # Sat..Sun


def test_classification_sort_and_exclusions(monkeypatch, tmp_path):
    end = D(2026, 7, 3)
    six_y = end - dt.timedelta(days=365 * 6)
    full = ft.business_days_between(six_y, end)
    rows = [
        ("US", "FULL", six_y, end, full),                               # sufficient -> skipped
        ("US", "GAPPY", six_y, end, full // 5),                         # 20% coverage
        ("US", "NEW", end - dt.timedelta(days=182), end, 130),          # 0.5y
        ("IN", "MID", end - dt.timedelta(days=365 * 3), end, 780),      # 3y
        ("US", "BOND", end, end, 1),                                    # < min_rows
        ("EU", "AIR.PA", end - dt.timedelta(days=100), end, 70),        # unmapped country
    ]
    out = run(monkeypatch, tmp_path, rows)
    assert [r["symbol"] for r in out] == ["NEW", "MID", "GAPPY"]     # most years missing first
    by = {r["symbol"]: r for r in out}
    assert by["GAPPY"]["reason"] == "gappy" and by["NEW"]["reason"] == "short_history"
    assert by["MID"]["yahoo_ticker"] == "MID.NS" and by["MID"]["gfinance_query"] == "MID:NSE"
    assert float(by["GAPPY"]["years_missing"]) == 0.0


def test_nothing_flagged_still_writes_header(monkeypatch, tmp_path):
    end = D(2026, 7, 3)
    start = end - dt.timedelta(days=365 * 6)
    out = run(monkeypatch, tmp_path, [("US", "FULL", start, end, ft.business_days_between(start, end))])
    assert out == []
    assert (tmp_path / "thin.csv").read_text().startswith("country,symbol,reason")
