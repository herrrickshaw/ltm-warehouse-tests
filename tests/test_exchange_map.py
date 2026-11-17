"""Offline tests for scripts/exchange_map.py (pure functions)."""
import pytest

from exchange_map import EXCHANGE_MAP, resolve_exchange_label, resolve_ticker


@pytest.mark.parametrize("symbol,country,yahoo,gq", [
    ("RELIANCE", "IN", "RELIANCE.NS", "RELIANCE:NSE"),       # bare India -> default .NS
    ("RELIANCE.NS", "IN", "RELIANCE.NS", "RELIANCE:NSE"),
    ("500325.BO", "IN", "500325.BO", "500325:BOM"),
    ("AAPL", "US", "AAPL", "AAPL:NASDAQ"),                   # US stays bare
    ("3696.HK", "HK", "3696.HK", "3696:HKG"),                # never "3696.HK.HK"
    ("0700", "HK", "0700.HK", "0700:HKG"),
    ("279570.KS", "KR", "279570.KS", "279570:KRX"),
    ("394420.KQ", "KR", "394420.KQ", "394420:KOSDAQ"),
    ("6921.TW", "TW", "6921.TW", "6921:TPE"),
    ("7888.TWO", "TW", "7888.TWO", "7888:TPEX"),             # longest suffix wins over .TW
    ("ZEF.F", "DE", "ZEF.F", "ZEF:FRA"),
    ("OBCK.DE", "DE", "OBCK.DE", "OBCK:ETR"),
    ("CCDS.V", "CA", "CCDS.V", "CCDS:CVE"),
    ("SHOP", "CA", "SHOP.TO", "SHOP:TSE"),
    ("001257.SZ", "CN", "001257.SZ", "001257:SHE"),
    ("BARC", "GB", "BARC.L", "BARC:LON"),
    ("BARC.L", "UK", "BARC.L", "BARC:LON"),
])
def test_resolve_ticker_reuses_existing_suffix_or_applies_default(symbol, country, yahoo, gq):
    assert resolve_ticker(symbol, EXCHANGE_MAP[country]) == (yahoo, gq)


def test_resolve_ticker_never_double_suffixes_any_country():
    for country, mapping in EXCHANGE_MAP.items():
        for suffix in mapping["suffix_to_gfinance"]:
            if not suffix:
                continue
            y, _ = resolve_ticker(f"ABC{suffix}", mapping)
            assert y == f"ABC{suffix}", (country, suffix, y)


def test_every_default_suffix_has_a_gfinance_code():
    for country, m in EXCHANGE_MAP.items():
        assert m["default_suffix"] in m["suffix_to_gfinance"], country


def test_resolve_exchange_label_mapped():
    assert resolve_exchange_label("3696.HK", "HK") == ("3696.HK", "3696:HKG", "HKG", True)


def test_resolve_exchange_label_unmapped_country_is_not_guessed():
    # "EU" is deliberately unmapped: return the symbol untouched, mapped=False
    assert resolve_exchange_label("AIR.PA", "EU") == ("AIR.PA", "AIR.PA", None, False)
