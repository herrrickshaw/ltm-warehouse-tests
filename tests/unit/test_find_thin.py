"""Pure-function tests for find_thin_history_tickers."""
from __future__ import annotations

import datetime as dt

import pytest

from find_thin_history_tickers import business_days_between, classify_history


def test_business_days_one_week():
    assert business_days_between(dt.date(2026, 7, 6), dt.date(2026, 7, 10)) == 5


def test_business_days_skips_weekend():
    assert business_days_between(dt.date(2026, 7, 3), dt.date(2026, 7, 6)) == 2  # Fri + Mon


def test_business_days_single_weekend_day_is_zero():
    assert business_days_between(dt.date(2026, 7, 4), dt.date(2026, 7, 4)) == 0


@pytest.mark.parametrize("span,cov,expected", [
    (0.49, 1.0, "short_history"),
    (4.99, 0.1, "short_history"),   # short wins over gappy
    (5.0, 0.84, "gappy"),
    (5.0, 0.85, None),
    (10.0, 1.0, None),
])
def test_classify_history(span, cov, expected):
    assert classify_history(span, cov, 5.0, 0.85) == expected
