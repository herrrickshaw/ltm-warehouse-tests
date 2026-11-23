"""Offline tests for build_stock_exchange_list.py (fake engine; no Postgres)."""
import csv
import sys
from contextlib import contextmanager

import pytest

import build_stock_exchange_list as bs

ROWS = [
    ("IN", 1, "RELIANCE"),
    ("IN", 2, "500325.BO"),
    ("US", 3, "AAPL"),
    ("HK", 4, "3696.HK"),
    ("EU", 5, "AIR.PA"),
    ("EU", 6, "SAP.DE"),
]


class Conn:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.log.append((sql, params))

        class R:
            def fetchall(self_inner):
                return self.rows
        return R()


class Engine:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    @contextmanager
    def begin(self):
        yield Conn(self.log, self.rows)


@pytest.fixture
def run(monkeypatch, tmp_path):
    def _run(*argv, rows=ROWS):
        log = []
        monkeypatch.setattr(bs, "create_engine", lambda url: log.append(("engine", url)) or Engine(log, rows))
        monkeypatch.setenv("DB", "postgresql://fake/db")
        out = tmp_path / "list.csv"
        monkeypatch.setattr(sys, "argv", ["build", "--out", str(out), *argv])
        bs.main()
        with open(out, newline="") as f:
            return log, list(csv.DictReader(f)), out
    return _run


def test_csv_rows_resolve_exchange_and_tickers(run, capsys):
    log, out, path = run()
    assert [r["symbol"] for r in out] == [r[2] for r in ROWS]                 # query order preserved
    by = {r["symbol"]: r for r in out}
    assert by["RELIANCE"] == {"country": "IN", "symbol": "RELIANCE", "symbol_id": "1", "exchange": "NSE",
                              "yahoo_ticker": "RELIANCE.NS", "gfinance_query": "RELIANCE:NSE", "mapped": "True"}
    assert by["500325.BO"]["exchange"] == "BOM" and by["500325.BO"]["yahoo_ticker"] == "500325.BO"
    assert by["AAPL"]["yahoo_ticker"] == "AAPL" and by["AAPL"]["gfinance_query"] == "AAPL:NASDAQ"
    assert by["3696.HK"]["yahoo_ticker"] == "3696.HK"                          # no doubled suffix
    assert by["AIR.PA"] == {"country": "EU", "symbol": "AIR.PA", "symbol_id": "5", "exchange": "",
                            "yahoo_ticker": "AIR.PA", "gfinance_query": "AIR.PA", "mapped": "False"}
    assert open(path).readline().strip() == "country,symbol,symbol_id,exchange,yahoo_ticker,gfinance_query,mapped"
    assert log[0] == ("engine", "postgresql://fake/db")


def test_report_counts_and_breakdown_order(run, capsys):
    run()
    out = capsys.readouterr().out
    assert "Wrote 6 symbols to" in out and "(4 with a resolved exchange, 2 unmapped)." in out
    assert "Unmapped by country: {'EU': 2}" in out
    lines = out.split("Exchange breakdown:\n")[1].splitlines()
    assert lines[0].split() == ["EU", "(unmapped)", "2"]                        # most frequent first
    assert {tuple(l.split()) for l in lines[1:] if l} == {("IN", "NSE", "1"), ("IN", "BOM", "1"), ("US", "NASDAQ", "1"), ("HK", "HKG", "1")}
    assert "Also stored" not in out


def test_all_mapped_prints_no_unmapped_line(run, capsys):
    run(rows=[("US", 1, "AAPL")])
    out = capsys.readouterr().out
    assert "Unmapped by country" not in out and "(1 with a resolved exchange, 0 unmapped)" in out


def test_empty_warehouse_still_writes_header(run, capsys):
    log, out, path = run(rows=[])
    assert out == [] and path.read_text().startswith("country,symbol")
    assert "Wrote 0 symbols" in capsys.readouterr().out


def test_store_table_recreates_and_inserts_all_rows(run, capsys):
    log, out, _ = run("--store-table")
    texts = [entry[0] for entry in log if isinstance(entry[0], str) and entry[0] != "engine"]
    assert texts[1] == "DROP TABLE IF EXISTS ltm_symbol_exchange"
    assert texts[2].startswith("CREATE TABLE ltm_symbol_exchange")
    assert "PRIMARY KEY (symbol_id, country)" in texts[2]
    assert texts[3].startswith("INSERT INTO ltm_symbol_exchange")
    params = log[-1][1]
    assert len(params) == 6 and params[0] == {"country": "IN", "symbol": "RELIANCE", "symbol_id": 1, "exchange": "NSE",
                                              "yahoo_ticker": "RELIANCE.NS", "gfinance_query": "RELIANCE:NSE", "mapped": True}
    assert "Also stored in warehouse table ltm_symbol_exchange (6 rows)." in capsys.readouterr().out


def test_selects_distinct_country_symbol_pairs_ordered(run):
    log, _, _ = run()
    sql = log[1][0]
    assert sql.startswith("SELECT DISTINCT f.country, s.symbol_id, s.symbol FROM ltm_fundamentals_fact f JOIN ltm_symbols s")
    assert sql.endswith("ORDER BY f.country, s.symbol")


def test_requires_db_env(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DB", raising=False)
    monkeypatch.setattr(sys, "argv", ["build", "--out", str(tmp_path / "x.csv")])
    monkeypatch.setattr(bs, "create_engine", lambda url: pytest.fail("must not connect"))
    with pytest.raises(SystemExit) as exc:
        bs.main()
    assert exc.value.code == 1 and "Set DB=postgresql://" in capsys.readouterr().err
    assert not (tmp_path / "x.csv").exists()
