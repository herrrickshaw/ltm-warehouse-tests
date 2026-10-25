"""Offline unit tests for backfill_missing_history.py — no Postgres, no network.

The DB connection is a small fake; fetchers are stubbed. The DB-backed
behaviour (real INSERT ... ON CONFLICT) stays covered by run_tests.sh.
"""
import datetime as dt
import sys
import types

import pandas as pd
import pytest

import backfill_missing_history as bf


class FakeResult:
    def __init__(self, scalar=None, rows=()):
        self._scalar, self._rows = scalar, list(rows)

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return self._rows


class FakeConn:
    """Records executes; answers the symbol_id lookup and existing-dates query."""

    def __init__(self, symbol_id=7, fact_dates=(), estimate_dates=()):
        self.symbol_id = symbol_id
        self.dates = {bf.FACT_TABLE: list(fact_dates), bf.ESTIMATE_TABLE: list(estimate_dates)}
        self.inserts = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM ltm_symbols" in sql:
            return FakeResult(scalar=self.symbol_id)
        if sql.strip().startswith("SELECT dt FROM"):
            table = sql.split("FROM")[1].split()[0]
            return FakeResult(rows=[(d,) for d in self.dates[table]])
        if "INSERT INTO" in sql:
            self.inserts.append((sql, params))
            return FakeResult()
        raise AssertionError(f"unexpected SQL: {sql}")


ROW = {"country": "US", "symbol": "NEWCO", "yahoo_ticker": "NEWCO", "gfinance_query": "NEWCO:NASDAQ"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network fetcher called without a stub")
    monkeypatch.setattr(bf, "fetch_yahoo", boom)
    monkeypatch.setattr(bf, "fetch_nse", boom)
    monkeypatch.setattr(bf, "fetch_serpapi", boom)
    monkeypatch.setattr(bf.time, "sleep", lambda s: None)


def yahoo_frame(dates, close=10.0):
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                         "Volume": 100}, index=idx)


# ── insert_ohlcv_rows ────────────────────────────────────────────────────────
def test_insert_ohlcv_skips_existing_dates_and_maps_nan_to_null():
    hist = yahoo_frame(["2026-01-05", "2026-01-06", "2026-01-07"])
    hist.loc[hist.index[2], ["Close", "Volume"]] = float("nan")
    conn = FakeConn()
    n = bf.insert_ohlcv_rows(conn, "US", 7, hist, {dt.date(2026, 1, 5)})
    assert n == 2
    rows = conn.inserts[0][1]
    assert [r["d"] for r in rows] == [dt.date(2026, 1, 6), dt.date(2026, 1, 7)]
    assert rows[1]["cl"] is None and rows[1]["v"] is None     # NaN -> NULL, never 0
    assert rows[0]["cl"] == 10.0 and rows[0]["v"] == 100


def test_insert_ohlcv_nothing_new_issues_no_insert():
    conn = FakeConn()
    assert bf.insert_ohlcv_rows(conn, "US", 7, yahoo_frame(["2026-01-05"]), {dt.date(2026, 1, 5)}) == 0
    assert conn.inserts == []


# ── insert_estimate_rows ─────────────────────────────────────────────────────
def test_insert_estimate_parses_iso_and_datetime_strings_and_drops_garbage():
    conn = FakeConn()
    pts = [("2021-01-04", 1.0), ("2021-01-05 16:00:00", 2.0), ("not-a-date", 3.0),
           (dt.date(2021, 1, 6), 4.0), ("2021-01-07", 5.0)]
    n = bf.insert_estimate_rows(conn, "US", 7, pts, {dt.date(2021, 1, 7)}, "serpapi")
    assert n == 3
    rows = conn.inserts[0][1]
    assert [r["d"] for r in rows] == [dt.date(2021, 1, 4), dt.date(2021, 1, 5), dt.date(2021, 1, 6)]
    assert {r["src"] for r in rows} == {"serpapi"}


# ── fetch_serpapi (requests stubbed) ─────────────────────────────────────────
def _stub_requests(monkeypatch, payload, status_ok=True):
    calls = {}

    class Resp:
        def raise_for_status(self):
            if not status_ok:
                raise RuntimeError("HTTP 500")

        def json(self):
            return payload

    def get(url, params, timeout):
        calls.update(url=url, params=params, timeout=timeout)
        return Resp()

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=get))
    return calls


def test_fetch_serpapi_keeps_complete_points_only(monkeypatch):
    monkeypatch.undo()  # restore the real fetch_serpapi, then re-stub requests
    calls = _stub_requests(monkeypatch, {"graph": [
        {"date": "2021-01-04", "price": "12.5"}, {"date": None, "price": 1},
        {"date": "2021-01-05"}, {"date": "2021-01-06", "price": 13}]})
    assert bf.fetch_serpapi("X:NASDAQ", "k") == [("2021-01-04", 12.5), ("2021-01-06", 13.0)]
    assert calls["params"]["engine"] == "google_finance" and calls["timeout"] == 30


def test_fetch_serpapi_empty_graph_is_none(monkeypatch):
    monkeypatch.undo()
    _stub_requests(monkeypatch, {"graph": []})
    assert bf.fetch_serpapi("X:NASDAQ", "k") is None


# ── fetch_nse (jugaad_data stubbed) ──────────────────────────────────────────
def test_fetch_nse_collapses_series_switch_to_one_row_per_date(monkeypatch):
    monkeypatch.undo()
    raw = pd.DataFrame([
        {"DATE": pd.Timestamp("2019-01-02"), "SERIES": "SM", "OPEN": 1, "HIGH": 1, "LOW": 1, "CLOSE": 100.5, "VOLUME": 1},
        {"DATE": pd.Timestamp("2019-01-02"), "SERIES": "EQ", "OPEN": 1, "HIGH": 1, "LOW": 1, "CLOSE": 100.6, "VOLUME": 2},
        {"DATE": pd.Timestamp("2019-01-01"), "SERIES": "EQ", "OPEN": 1, "HIGH": 1, "LOW": 1, "CLOSE": 99.0, "VOLUME": 3},
    ])
    nse = types.ModuleType("jugaad_data.nse")
    nse.stock_df = lambda symbol, from_date, to_date, series: raw.copy()
    pkg = types.ModuleType("jugaad_data")
    pkg.nse = nse
    monkeypatch.setitem(sys.modules, "jugaad_data", pkg)
    monkeypatch.setitem(sys.modules, "jugaad_data.nse", nse)
    hist = bf.fetch_nse("OLDIN", 1)
    assert list(hist.index) == [dt.date(2019, 1, 1), dt.date(2019, 1, 2)]
    assert hist.loc[dt.date(2019, 1, 2), "Close"] == 100.6
    assert list(hist.columns) == ["Open", "High", "Low", "Close", "Volume"]


# ── process_row routing ──────────────────────────────────────────────────────
def test_unknown_symbol_is_reported_not_fetched():
    r = bf.process_row(FakeConn(symbol_id=None), ROW, True, "k", 0, 10)
    assert r["source"] == "none" and r["note"] == "symbol not found"


def test_yahoo_hit_dry_run_counts_only_new_dates_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: yahoo_frame(["2026-01-05", "2026-01-06"]))
    conn = FakeConn(fact_dates=[dt.date(2026, 1, 5)])
    r = bf.process_row(conn, ROW, False, None, 0, 10)
    assert (r["source"], r["rows_added"]) == ("yahoo", 1)
    assert conn.inserts == []


def test_nse_tier_never_tried_outside_india(monkeypatch):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: None)
    r = bf.process_row(FakeConn(), dict(ROW, country="GB"), True, None, 0, 10)
    assert r["source"] == "none"          # fetch_nse stub would have raised


def test_yahoo_and_nse_errors_are_both_reported(monkeypatch):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: (_ for _ in ()).throw(RuntimeError("y")))
    monkeypatch.setattr(bf, "fetch_nse", lambda s, y: (_ for _ in ()).throw(RuntimeError("n")))
    r = bf.process_row(FakeConn(), dict(ROW, country="IN"), True, None, 0, 10)
    assert r["note"] == "yahoo error: y; nse error: n"


def test_serpapi_goes_to_estimate_table_never_fact_table(monkeypatch):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: None)
    monkeypatch.setattr(bf, "fetch_serpapi", lambda q, k: [("2021-01-04", 5.0)])
    conn = FakeConn()
    r = bf.process_row(conn, ROW, True, "k", 0, 10)
    assert r["source"].startswith("serpapi") and r["rows_added"] == 1
    assert all(bf.ESTIMATE_TABLE in sql and bf.FACT_TABLE not in sql for sql, _ in conn.inserts)


def test_serpapi_dry_run_reports_the_same_count_as_apply(monkeypatch):
    # One offered date is already real OHLCV, one already estimated, one unparseable:
    # --apply inserts 1 row, so the dry run must also report 1 — a dry run that
    # overstates the work is how a no-op backfill gets approved.
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: None)
    pts = [("2021-01-04", 1.0), ("2021-01-05", 2.0), ("2021-01-06", 3.0), ("garbage", 4.0)]
    monkeypatch.setattr(bf, "fetch_serpapi", lambda q, k: pts)
    kw = dict(fact_dates=[dt.date(2021, 1, 5)], estimate_dates=[dt.date(2021, 1, 6)])
    applied = bf.process_row(FakeConn(**kw), ROW, True, "k", 0, 10)
    dry = bf.process_row(FakeConn(**kw), ROW, False, "k", 0, 10)
    assert applied["rows_added"] == 1
    assert dry["rows_added"] == applied["rows_added"]
