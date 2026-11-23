"""find_thin_history_tickers: argument defaults, threshold boundaries, console report (fake engine)."""
import csv
import datetime as dt
import sys
from contextlib import contextmanager

import pytest

import find_thin_history_tickers as ft

D = dt.date
END = D(2026, 7, 3)


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


def years_ago(n):
    return END - dt.timedelta(days=round(365.25 * n))


def test_requires_db_env(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DB", raising=False)
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(tmp_path / "t.csv")])
    with pytest.raises(SystemExit) as exc:
        ft.main()
    assert exc.value.code == 1 and "Set DB=" in capsys.readouterr().err


def test_span_exactly_min_years_is_not_short(monkeypatch, tmp_path):
    start = END - dt.timedelta(days=1826)                      # 1826 / 365.25 = 4.9993 -> short
    short = run(monkeypatch, tmp_path, [("US", "A", start, END, ft.business_days_between(start, END))])
    assert [r["reason"] for r in short] == ["short_history"]
    start = END - dt.timedelta(days=1827)                      # 1827 / 365.25 = 5.002 -> long enough
    assert run(monkeypatch, tmp_path, [("US", "A", start, END, ft.business_days_between(start, END))]) == []


def test_coverage_exactly_at_threshold_is_sufficient(monkeypatch, tmp_path):
    start = years_ago(6)
    expected = ft.business_days_between(start, END)
    assert run(monkeypatch, tmp_path, [("US", "A", start, END, int(expected * 0.85) + 1)]) == []
    gappy = run(monkeypatch, tmp_path, [("US", "A", start, END, int(expected * 0.85) - 1)])
    assert [r["reason"] for r in gappy] == ["gappy"]


def test_custom_thresholds_change_classification(monkeypatch, tmp_path):
    start = years_ago(3)
    rows = [("US", "A", start, END, ft.business_days_between(start, END))]
    assert [r["reason"] for r in run(monkeypatch, tmp_path, rows)] == ["short_history"]
    assert run(monkeypatch, tmp_path, rows, "--min-years", "2") == []
    gappy_rows = [("US", "A", years_ago(6), END, 100)]
    assert run(monkeypatch, tmp_path, gappy_rows, "--gap-threshold", "0.001") == []


def test_min_rows_boundary_excludes_only_below(monkeypatch, tmp_path, capsys):
    short = [("US", "A", END - dt.timedelta(days=30), END, 19), ("US", "B", END - dt.timedelta(days=30), END, 20)]
    out = run(monkeypatch, tmp_path, short)
    assert [r["symbol"] for r in out] == ["B"]
    assert "Excluded 1 more that had fewer than 20 total rows" in capsys.readouterr().out
    out = run(monkeypatch, tmp_path, short, "--min-rows", "1")
    assert {r["symbol"] for r in out} == {"A", "B"}


def test_zero_day_span_does_not_divide_by_zero(monkeypatch, tmp_path):
    out = run(monkeypatch, tmp_path, [("US", "A", D(2026, 7, 4), D(2026, 7, 4), 25)])     # a Saturday: 0 business days
    assert [r["reason"] for r in out] == ["short_history"]
    assert float(out[0]["coverage_pct"]) == 2500.0 and out[0]["expected_days"] == "1"


def test_console_report_lists_flagged_and_unmapped(monkeypatch, tmp_path, capsys):
    start = END - dt.timedelta(days=200)
    rows = [("US", "NEW", start, END, 100), ("EU", "AIR.PA", start, END, 100)]
    run(monkeypatch, tmp_path, rows)
    out = capsys.readouterr().out
    assert "Checked 2 symbols. Flagged 1 with < 5.0y history or < 85% day-coverage within their span." in out
    assert "Excluded 1 symbols in unmapped countries (['EU'])" in out
    assert f"{'SYMBOL':<10}{'COUNTRY':<9}{'REASON':<15}{'SPAN(y)':<9}{'COVERAGE':<10}{'MISSING(y)':<11}" in out
    assert out.rstrip().endswith(f"Wrote {tmp_path / 'thin.csv'}")


def test_console_report_truncates_after_50_rows(monkeypatch, tmp_path, capsys):
    start = END - dt.timedelta(days=200)
    rows = [("US", f"S{i:03d}", start, END, 100 + i) for i in range(53)]
    out = run(monkeypatch, tmp_path, rows)
    assert len(out) == 53
    printed = capsys.readouterr().out
    assert "... and 3 more (see" in printed
    assert printed.count(" short_history ") == 50


def test_ties_sort_by_coverage_then_input_order(monkeypatch, tmp_path):
    start = END - dt.timedelta(days=200)
    rows = [("US", "HI", start, END, 130), ("US", "LO", start, END, 90), ("US", "MID", start, END, 110)]
    assert [r["symbol"] for r in run(monkeypatch, tmp_path, rows)] == ["LO", "MID", "HI"]


def test_span_exactly_equal_to_min_years_is_not_short(monkeypatch, tmp_path):
    start = END - dt.timedelta(days=1461)                       # 1461 / 365.25 == 4.0 exactly
    rows = [("US", "A", start, END, ft.business_days_between(start, END))]
    assert run(monkeypatch, tmp_path, rows, "--min-years", "4") == []
    assert [r["reason"] for r in run(monkeypatch, tmp_path, rows, "--min-years", "4.01")] == ["short_history"]


def test_coverage_exactly_equal_to_threshold_is_not_gappy(monkeypatch, tmp_path):
    start = years_ago(6)
    expected = ft.business_days_between(start, END)
    count = expected // 2
    threshold = count / expected
    rows = [("US", "A", start, END, count)]
    assert run(monkeypatch, tmp_path, rows, "--gap-threshold", repr(threshold)) == []
    assert [r["reason"] for r in run(monkeypatch, tmp_path, rows, "--gap-threshold", repr(threshold + 0.001))] == ["gappy"]
