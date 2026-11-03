"""Offline unit tests for backfill_missing_history helpers (no DB, no network)."""
from __future__ import annotations

import datetime as dt
import sys
import types

import pandas as pd

import backfill_missing_history as bf


class FakeResult:
    def __init__(self, value=None, rows=None):
        self._value, self._rows = value, rows or []

    def scalar(self):
        return self._value

    def fetchall(self):
        return self._rows


class FakeConn:
    """Records executes; answers symbol lookups and existing-date queries."""

    def __init__(self, symbol_id=1, fact_dates=()):
        self.symbol_id, self.fact_dates = symbol_id, fact_dates
        self.writes = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "SELECT symbol_id" in sql:
            return FakeResult(value=self.symbol_id)
        if "SELECT dt" in sql:
            return FakeResult(rows=[(d,) for d in self.fact_dates] if "fundamentals" in sql else [])
        self.writes.append((sql, params))
        return FakeResult()


def frame(*days):
    idx = pd.to_datetime([f"2026-01-{d:02d}" for d in days])
    return pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 10}, index=idx)


def test_as_date_handles_timestamp_and_date():
    assert bf._as_date(pd.Timestamp("2026-01-05")) == dt.date(2026, 1, 5)
    assert bf._as_date(dt.date(2026, 1, 5)) == dt.date(2026, 1, 5)


def test_count_new_rows_skips_known_dates():
    assert bf.count_new_rows(frame(5, 6, 7), {dt.date(2026, 1, 6)}) == 2


def test_insert_ohlcv_rows_dedups_and_maps_nan_to_none():
    hist = frame(5, 6)
    hist.loc[hist.index[1], "Volume"] = float("nan")
    conn = FakeConn()
    assert bf.insert_ohlcv_rows(conn, "US", 1, hist, {dt.date(2026, 1, 5)}) == 1
    (_, rows), = conn.writes
    assert rows[0]["d"] == dt.date(2026, 1, 6) and rows[0]["v"] is None


def test_insert_ohlcv_rows_nothing_to_write():
    conn = FakeConn()
    assert bf.insert_ohlcv_rows(conn, "US", 1, frame(5), {dt.date(2026, 1, 5)}) == 0
    assert conn.writes == []


def test_insert_estimate_rows_parses_iso_strings_and_skips_bad_dates():
    conn = FakeConn()
    pts = [("2022-06-01 00:00:00", 4.0), ("garbage", 1.0), (dt.date(2022, 6, 2), 5.0), ("2022-06-03", 6.0)]
    assert bf.insert_estimate_rows(conn, "US", 1, pts, {dt.date(2022, 6, 3)}, "serpapi") == 2
    assert [r["d"] for r in conn.writes[0][1]] == [dt.date(2022, 6, 1), dt.date(2022, 6, 2)]


def test_fetch_serpapi_parses_graph(monkeypatch):
    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"graph": [{"date": "2022-06-01", "price": "3.5"}, {"date": None, "price": 1}]}
    seen = {}
    def fake_get(url, params, timeout):
        seen.update(url=url, params=params, timeout=timeout)
        return Resp()
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get))
    assert bf.fetch_serpapi("AAPL:NASDAQ", "K") == [("2022-06-01", 3.5)]
    assert seen["url"] == bf.SERPAPI_URL and seen["params"]["window"] == "5Y"


def test_fetch_nse_collapses_series_switch(monkeypatch):
    raw = pd.DataFrame({
        "DATE": pd.to_datetime(["2026-01-05", "2026-01-05", "2026-01-06"]),
        "OPEN": [1, 2, 3], "HIGH": [1, 2, 3], "LOW": [1, 2, 3], "CLOSE": [1, 2, 3], "VOLUME": [1, 2, 3],
    })
    mod = types.ModuleType("jugaad_data.nse")
    mod.stock_df = lambda **kw: raw
    monkeypatch.setitem(sys.modules, "jugaad_data", types.ModuleType("jugaad_data"))
    monkeypatch.setitem(sys.modules, "jugaad_data.nse", mod)
    hist = bf.fetch_nse("X", 1)
    assert len(hist) == 2 and hist.loc[dt.date(2026, 1, 5), "Close"] == 2  # keep="last"


ROW = {"country": "US", "symbol": "ZZ", "yahoo_ticker": "ZZ", "gfinance_query": "ZZ:NASDAQ"}


def run(conn, monkeypatch, yahoo=None, serpapi=None, key=None, row=ROW, apply=False):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: yahoo)
    monkeypatch.setattr(bf, "fetch_nse", lambda s, y: None)
    monkeypatch.setattr(bf, "fetch_serpapi", lambda q, k: serpapi)
    return bf.process_row(conn, row, apply, key, 0, 1)


def test_process_row_symbol_missing(monkeypatch):
    r = run(FakeConn(symbol_id=None), monkeypatch)
    assert (r["source"], r["note"]) == ("none", "symbol not found")


def test_process_row_yahoo_dry_run_counts_without_writing(monkeypatch):
    conn = FakeConn(fact_dates=[dt.date(2026, 1, 5)])
    r = run(conn, monkeypatch, yahoo=frame(5, 6))
    assert (r["source"], r["rows_added"]) == ("yahoo", 1) and conn.writes == []


def test_process_row_no_key_reports_gap(monkeypatch):
    r = run(FakeConn(), monkeypatch)
    assert r["note"] == "no yahoo/nse data; no --serpapi-key given for fallback"


def test_process_row_serpapi_fallback(monkeypatch):
    r = run(FakeConn(), monkeypatch, serpapi=[("2022-06-01", 1.0)], key="K")
    assert (r["source"], r["rows_added"]) == ("serpapi (estimate, close-only)", 1)


def test_process_row_yahoo_error_noted(monkeypatch):
    def boom(t): raise RuntimeError("down")
    monkeypatch.setattr(bf, "fetch_yahoo", boom)
    r = bf.process_row(FakeConn(), ROW, False, None, 0, 1)
    assert r["note"] == "yahoo error: down"
