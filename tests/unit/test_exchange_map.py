"""Characterisation tests for exchange_map, pinned to output of the pre-refactor code."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from exchange_map import EXCHANGE_MAP, resolve_exchange_label, resolve_ticker

GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "golden" / "resolve_exchange_label.json").read_text())


@pytest.mark.parametrize("key", sorted(GOLDEN))
def test_resolve_exchange_label_matches_golden(key):
    country, symbol = key.split("|", 1)
    assert list(resolve_exchange_label(symbol, country)) == GOLDEN[key]


def test_existing_suffix_is_never_doubled():
    assert resolve_ticker("3696.HK", EXCHANGE_MAP["HK"]) == ("3696.HK", "3696:HKG")


def test_bare_symbol_gets_default_suffix():
    assert resolve_ticker("RELI", EXCHANGE_MAP["IN"]) == ("RELI.NS", "RELI:NSE")
    assert resolve_ticker("AAPL", EXCHANGE_MAP["US"]) == ("AAPL", "AAPL:NASDAQ")


def test_korea_kosdaq_vs_kospi():
    assert resolve_ticker("394420.KQ", EXCHANGE_MAP["KR"])[1] == "394420:KOSDAQ"
    assert resolve_ticker("279570.KS", EXCHANGE_MAP["KR"])[1] == "279570:KRX"


def test_unmapped_country_is_flagged_not_guessed():
    assert resolve_exchange_label("X.PA", "EU") == ("X.PA", "X.PA", None, False)
