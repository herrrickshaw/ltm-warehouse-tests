"""Offline tests for the rest of backfill_missing_history.py: Yahoo fetcher, table DDL, process_row tiers
that test_backfill_units does not reach, and the main() CLI driver (fake engine; no Postgres, no network)."""
import csv
import datetime as dt
import sys
import types

import pandas as pd
import pytest

import backfill_missing_history as bf

ROW = {"country": "US", "symbol": "NEWCO", "yahoo_ticker": "NEWCO", "gfinance_query": "NEWCO:NASDAQ"}


class Result:
    def __init__(self, scalar=None, rows=()):
        self._s, self._r = scalar, list(rows)

    def scalar(self):
        return self._s

    def fetchall(self):
        return self._r


class Conn:
    def __init__(self, symbol_id=7, fact_dates=(), estimate_dates=()):
        self.symbol_id = symbol_id
        self.dates = {bf.FACT_TABLE: list(fact_dates), bf.ESTIMATE_TABLE: list(estimate_dates)}
        self.executed = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.executed.append((sql, params))
        if "FROM ltm_symbols" in sql:
            return Result(scalar=self.symbol_id)
        if sql.startswith("SELECT dt FROM"):
            return Result(rows=[(d,) for d in self.dates[sql.split("FROM")[1].split()[0]]])
        return Result()

    @property
    def inserts(self):
        return [(s, p) for s, p in self.executed if "INSERT INTO" in s]


def frame(dates, close=5.0):
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 10}, index=idx)


@pytest.fixture
def slept(monkeypatch):
    calls = []
    monkeypatch.setattr(bf.time, "sleep", lambda s: calls.append(s))
    return calls


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("unstubbed fetcher called")
    for name in ("fetch_yahoo", "fetch_nse", "fetch_serpapi"):
        monkeypatch.setattr(bf, name, boom)


# ---- DDL and fetch_yahoo ------------------------------------------------------------------------

def test_ensure_estimate_table_creates_if_missing():
    conn = Conn()
    bf.ensure_estimate_table(conn)
    sql, _ = conn.executed[0]
    assert sql.startswith("CREATE TABLE IF NOT EXISTS ltm_price_estimate")
    assert "PRIMARY KEY (country, symbol_id, dt)" in sql and "REFERENCES ltm_symbols(symbol_id)" in sql


def stub_yfinance(monkeypatch, history):
    calls = {}

    class Ticker:
        def __init__(self, t):
            calls["ticker"] = t

        def history(self, **kw):
            calls["kw"] = kw
            return history

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))
    return calls


def test_fetch_yahoo_requests_max_unadjusted_history(monkeypatch):
    monkeypatch.undo()                      # restore the real fetch_yahoo replaced by the autouse fixture
    hist = frame(["2026-01-05"])
    calls = stub_yfinance(monkeypatch, hist)
    assert bf.fetch_yahoo("AAA.NS") is hist
    assert calls == {"ticker": "AAA.NS", "kw": {"period": "max", "auto_adjust": False}}


@pytest.mark.parametrize("empty", [None, pd.DataFrame()])
def test_fetch_yahoo_none_or_empty_means_no_data(monkeypatch, empty):
    monkeypatch.undo()
    stub_yfinance(monkeypatch, empty)
    assert bf.fetch_yahoo("AAA") is None


# ---- process_row tiers ----------------------------------------------------------------------------

def test_yahoo_hit_applies_inserts_and_sleeps_once(monkeypatch, slept):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: frame(["2026-01-05", "2026-01-06"]))
    conn = Conn(fact_dates=[dt.date(2026, 1, 5)])
    r = bf.process_row(conn, ROW, True, "key", 2.5, 10)
    assert r == {"symbol": "NEWCO", "country": "US", "source": "yahoo", "rows_added": 1, "note": ""}
    assert len(conn.inserts) == 1 and "ltm_fundamentals_fact" in conn.inserts[0][0]
    assert slept == [2.5]


def test_india_falls_back_to_nse_when_yahoo_is_empty(monkeypatch, slept):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: None)
    seen = {}
    monkeypatch.setattr(bf, "fetch_nse", lambda s, y: seen.update(symbol=s, years=y) or frame(["2019-01-01", "2019-01-02"]))
    conn = Conn(fact_dates=[dt.date(2019, 1, 1)])
    r = bf.process_row(conn, dict(ROW, country="IN", symbol="OLDIN"), True, None, 1.0, 7)
    assert seen == {"symbol": "OLDIN", "years": 7}
    assert (r["source"], r["rows_added"]) == ("nse (bhavcopy)", 1)
    assert slept == [1.0]                                           # one sleep after the NSE hit, none before
    assert "ltm_fundamentals_fact" in conn.inserts[0][0]


def test_nse_dry_run_counts_without_writing(monkeypatch, slept):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: None)
    monkeypatch.setattr(bf, "fetch_nse", lambda s, y: frame(["2019-01-01", "2019-01-02", "2019-01-03"]))
    conn = Conn(fact_dates=[dt.date(2019, 1, 2)])
    r = bf.process_row(conn, dict(ROW, country="IN"), False, None, 0, 10)
    assert (r["source"], r["rows_added"]) == ("nse (bhavcopy)", 2) and conn.inserts == []


def test_nse_miss_then_serpapi_sleeps_after_each_attempt(monkeypatch, slept):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: None)
    monkeypatch.setattr(bf, "fetch_nse", lambda s, y: None)
    monkeypatch.setattr(bf, "fetch_serpapi", lambda q, k: [("2021-01-04", 5.0)])
    r = bf.process_row(Conn(), dict(ROW, country="IN"), True, "k", 3.0, 10)
    assert r["source"].startswith("serpapi") and slept == [3.0, 3.0]


def test_no_serpapi_key_reports_yahoo_error_or_default_note(monkeypatch):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    r = bf.process_row(Conn(), ROW, True, None, 0, 10)
    assert (r["source"], r["rows_added"], r["note"]) == ("none", 0, "yahoo error: boom")
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: None)
    r = bf.process_row(Conn(), ROW, True, "", 0, 10)
    assert r["note"] == "no yahoo/nse data; no --serpapi-key given for fallback"


def test_nse_error_alone_is_reported_without_yahoo_prefix(monkeypatch):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: None)
    monkeypatch.setattr(bf, "fetch_nse", lambda s, y: (_ for _ in ()).throw(ValueError("bad series")))
    r = bf.process_row(Conn(), dict(ROW, country="IN"), True, None, 0, 10)
    assert r["note"] == "nse error: bad series"


def test_serpapi_error_and_no_data_notes(monkeypatch):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: None)
    monkeypatch.setattr(bf, "fetch_serpapi", lambda q, k: (_ for _ in ()).throw(RuntimeError("HTTP 429")))
    r = bf.process_row(Conn(), ROW, True, "k", 0, 10)
    assert (r["source"], r["note"]) == ("none", "serpapi error: HTTP 429")
    monkeypatch.setattr(bf, "fetch_serpapi", lambda q, k: None)
    r = bf.process_row(Conn(), ROW, True, "k", 0, 10)
    assert (r["source"], r["note"]) == ("none", "no data from any source")


def test_serpapi_skips_dates_in_fact_and_estimate_tables(monkeypatch):
    monkeypatch.setattr(bf, "fetch_yahoo", lambda t: None)
    seen = {}
    monkeypatch.setattr(bf, "fetch_serpapi", lambda q, k: seen.update(q=q, k=k) or [("2021-01-04", 1.0), ("2021-01-05", 2.0), ("2021-01-06", 3.0)])
    conn = Conn(fact_dates=[dt.date(2021, 1, 4)], estimate_dates=[dt.date(2021, 1, 5)])
    r = bf.process_row(conn, ROW, True, "sekret", 0, 10)
    assert seen == {"q": "NEWCO:NASDAQ", "k": "sekret"}
    assert r["rows_added"] == 1
    assert [p["d"] for p in conn.inserts[0][1]] == [dt.date(2021, 1, 6)]
    assert conn.inserts[0][1][0]["src"] == "serpapi"


def test_estimate_insert_skipped_when_nothing_new():
    conn = Conn()
    assert bf.insert_estimate_rows(conn, "US", 7, [("2021-01-04", 1.0)], {dt.date(2021, 1, 4)}, "serpapi") == 0
    assert conn.inserts == []
    assert bf.new_estimate_points([("2021-01-04T00:00:00", 1.0)], set()) == [(dt.date(2021, 1, 4), 1.0)]


def test_existing_dates_returns_a_set_scoped_to_country_and_symbol():
    conn = Conn(fact_dates=[dt.date(2026, 1, 5), dt.date(2026, 1, 5), dt.date(2026, 1, 6)])
    assert bf.existing_dates(conn, "US", 7, bf.FACT_TABLE) == {dt.date(2026, 1, 5), dt.date(2026, 1, 6)}
    assert conn.executed[0][1] == {"c": "US", "sid": 7}


# ---- main() --------------------------------------------------------------------------------------------

class Trans:
    def __init__(self, log):
        self.log = log

    def commit(self):
        self.log.append("commit")

    def rollback(self):
        self.log.append("rollback")


class Engine:
    def __init__(self, log):
        self.log = log

    def connect(self):
        log = self.log

        class CM:
            def __enter__(self_inner):
                class C:
                    def begin(self):
                        return Trans(log)
                self_inner.conn = C()
                return self_inner.conn

            def __exit__(self_inner, *a):
                log.append("closed")
        return CM()


@pytest.fixture
def harness(monkeypatch, tmp_path):
    log, calls = [], []
    monkeypatch.setattr(bf, "create_engine", lambda url: log.append(("engine", url)) or Engine(log))
    monkeypatch.setattr(bf, "ensure_estimate_table", lambda conn: log.append("ensure"))

    def fake_process(conn, row, apply_changes, key, sleep_s, years):
        calls.append(dict(symbol=row["symbol"], apply=apply_changes, key=key, sleep=sleep_s, years=years))
        if row["symbol"] == "GAP":
            return {"symbol": "GAP", "country": row["country"], "source": "none", "rows_added": 0, "note": "no data from any source"}
        return {"symbol": row["symbol"], "country": row["country"], "source": "yahoo", "rows_added": 3, "note": ""}

    monkeypatch.setattr(bf, "process_row", fake_process)
    path = tmp_path / "thin.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["country", "symbol", "yahoo_ticker", "gfinance_query"])
        w.writeheader()
        w.writerows([{"country": "US", "symbol": s, "yahoo_ticker": s, "gfinance_query": f"{s}:NASDAQ"} for s in ("AAA", "BBB", "GAP")])
    monkeypatch.setenv("DB", "postgresql://fake/db")
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["backfill", "--csv", str(path), *argv])
        bf.main()
    return types.SimpleNamespace(run=run, log=log, calls=calls, csv=path)


def test_main_dry_run_rolls_back_and_reports(harness, capsys):
    harness.run()
    out = capsys.readouterr().out
    assert harness.log == [("engine", "postgresql://fake/db"), "ensure", "rollback", "closed"]
    assert out.startswith("DRY RUN (no changes written; pass --apply to commit)")
    assert [c["apply"] for c in harness.calls] == [False, False, False]
    assert harness.calls[0] == dict(symbol="AAA", apply=False, key=None, sleep=1.0, years=10)
    assert f"{'SYMBOL':<10}{'COUNTRY':<9}{'SOURCE':<32}{'ROWS':<8}NOTE" in out
    assert f"{'AAA':<10}{'US':<9}{'yahoo':<32}{3:<8}" in out
    assert "1 symbols had no data from either source" in out and "  GAP (US): no data from any source" in out


def test_main_apply_commits(harness, capsys):
    harness.run("--apply", "--sleep", "0.25", "--nse-lookback-years", "4", "--serpapi-key", "abc")
    assert harness.log[-2:] == ["commit", "closed"] and "rollback" not in harness.log
    assert harness.calls[1] == dict(symbol="BBB", apply=True, key="abc", sleep=0.25, years=4)
    assert capsys.readouterr().out.startswith("APPLIED")


def test_main_limit_and_env_key_default(harness, monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "from-env")
    harness.run("--limit", "2")
    assert [c["symbol"] for c in harness.calls] == ["AAA", "BBB"]
    assert harness.calls[0]["key"] == "from-env"


def test_main_without_failures_prints_no_gap_section(harness, capsys):
    harness.run("--limit", "2")
    assert "no data from either source" not in capsys.readouterr().out


def test_main_requires_db_env(harness, monkeypatch, capsys):
    monkeypatch.delenv("DB")
    with pytest.raises(SystemExit) as exc:
        harness.run()
    assert exc.value.code == 1
    assert "Set DB=postgresql://" in capsys.readouterr().err
    assert harness.log == [] and harness.calls == []                  # nothing touched before the check


def stub_nse(monkeypatch, raw):
    nse = types.ModuleType("jugaad_data.nse")
    nse.stock_df = lambda symbol, from_date, to_date, series: raw
    pkg = types.ModuleType("jugaad_data")
    pkg.nse = nse
    monkeypatch.setitem(sys.modules, "jugaad_data", pkg)
    monkeypatch.setitem(sys.modules, "jugaad_data.nse", nse)


@pytest.mark.parametrize("raw", [None, pd.DataFrame()])
def test_fetch_nse_none_or_empty_is_no_data(monkeypatch, raw):
    monkeypatch.undo()
    stub_nse(monkeypatch, raw)
    assert bf.fetch_nse("X", 3) is None


def test_fetch_nse_requests_eq_series_over_lookback_window(monkeypatch):
    monkeypatch.undo()
    seen = {}
    nse = types.ModuleType("jugaad_data.nse")

    def stock_df(symbol, from_date, to_date, series):
        seen.update(symbol=symbol, from_date=from_date, to_date=to_date, series=series)
        return pd.DataFrame({"DATE": [pd.Timestamp("2024-01-02")], "OPEN": [1], "HIGH": [1], "LOW": [1], "CLOSE": [1], "VOLUME": [1]})
    nse.stock_df = stock_df
    pkg = types.ModuleType("jugaad_data")
    pkg.nse = nse
    monkeypatch.setitem(sys.modules, "jugaad_data", pkg)
    monkeypatch.setitem(sys.modules, "jugaad_data.nse", nse)
    bf.fetch_nse("OLDIN", 4)
    assert seen["symbol"] == "OLDIN" and seen["series"] == "EQ"
    assert seen["to_date"] == dt.date.today()
    assert (seen["to_date"] - seen["from_date"]).days == 365 * 4
