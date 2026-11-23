"""exchange_map.resolve_ticker: matching rules that the real EXCHANGE_MAP data cannot distinguish."""
from exchange_map import EXCHANGE_MAP, resolve_exchange_label, resolve_ticker


def test_longest_matching_suffix_wins_when_one_suffix_ends_the_other():
    mapping = {"suffix_to_gfinance": {"": "MAIN", ".A": "SHORT", ".B.A": "LONG"}, "default_suffix": ".A"}
    assert resolve_ticker("FOO.B.A", mapping) == ("FOO.B.A", "FOO:LONG")
    assert resolve_ticker("FOO.A", mapping) == ("FOO.A", "FOO:SHORT")


def test_suffix_must_be_at_the_end_of_the_symbol():
    hk = EXCHANGE_MAP["HK"]
    assert resolve_ticker("A.HKB", hk) == ("A.HKB.HK", "A.HKB:HKG")
    assert resolve_ticker("0.HK1", hk) == ("0.HK1.HK", "0.HK1:HKG")


def test_empty_suffix_never_matches_as_an_existing_suffix():
    us = EXCHANGE_MAP["US"]
    assert resolve_ticker("AAPL", us) == ("AAPL", "AAPL:NASDAQ")


def test_default_suffix_without_its_own_exchange_falls_back_to_bare_entry():
    mapping = {"suffix_to_gfinance": {"": "BARE", ".X": "XEX"}, "default_suffix": ".ZZ"}
    assert resolve_ticker("ABC", mapping) == ("ABC.ZZ", "ABC:BARE")


def test_uk_alias_resolves_exactly_like_gb():
    assert resolve_ticker("BARC", EXCHANGE_MAP["UK"]) == resolve_ticker("BARC", EXCHANGE_MAP["GB"]) == ("BARC.L", "BARC:LON")


def test_label_for_unmapped_country_is_the_bare_symbol():
    assert resolve_exchange_label("AIR.PA", "EU") == ("AIR.PA", "AIR.PA", None, False)
    assert resolve_exchange_label("ZZZ", "") == ("ZZZ", "ZZZ", None, False)


def test_label_for_mapped_country_returns_matched_exchange():
    assert resolve_exchange_label("RELIANCE", "IN") == ("RELIANCE.NS", "RELIANCE:NSE", "NSE", True)
    assert resolve_exchange_label("394420.KQ", "KR") == ("394420.KQ", "394420:KOSDAQ", "KOSDAQ", True)
