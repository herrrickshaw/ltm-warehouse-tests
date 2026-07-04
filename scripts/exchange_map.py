"""
Shared country -> exchange resolution logic, used by both
find_thin_history_tickers.py and build_stock_exchange_list.py so the two
scripts can never drift out of sync on ticker formatting.

For most countries in this warehouse, the `symbol` column already IS a
fully-qualified Yahoo ticker (e.g. CN "001257.SZ", HK "3696.HK", KR
"279570.KS" vs "394420.KQ", TW "6921.TW" vs "7888.TWO", DE "OBCK.DE" vs
"ZEF.F", CA "CCDS.V"). Only US and IN symbols in this data are bare (no
suffix). So per country we record every Yahoo suffix that might already
be present (mapped to its Google Finance exchange code) plus a
default_suffix to append ONLY when the symbol has none of them yet —
never blindly append on top of an existing suffix (that would turn
"3696.HK" into the broken "3696.HK.HK").
"""

EXCHANGE_MAP = {
    "US": {"suffix_to_gfinance": {"": "NASDAQ"}, "default_suffix": ""},
    "IN": {"suffix_to_gfinance": {"": "NSE", ".NS": "NSE", ".BO": "BOM"}, "default_suffix": ".NS"},
    "GB": {"suffix_to_gfinance": {".L": "LON"}, "default_suffix": ".L"},
    "UK": {"suffix_to_gfinance": {".L": "LON"}, "default_suffix": ".L"},  # alias — same country as GB, different code in this data
    "CA": {"suffix_to_gfinance": {".TO": "TSE", ".V": "CVE"}, "default_suffix": ".TO"},
    "AU": {"suffix_to_gfinance": {".AX": "ASX"}, "default_suffix": ".AX"},
    "DE": {"suffix_to_gfinance": {".DE": "ETR", ".F": "FRA"}, "default_suffix": ".DE"},
    "FR": {"suffix_to_gfinance": {".PA": "EPA"}, "default_suffix": ".PA"},
    "JP": {"suffix_to_gfinance": {".T": "TYO"}, "default_suffix": ".T"},
    "HK": {"suffix_to_gfinance": {".HK": "HKG"}, "default_suffix": ".HK"},
    "SG": {"suffix_to_gfinance": {".SI": "SGX"}, "default_suffix": ".SI"},
    "CN": {"suffix_to_gfinance": {".SS": "SHA", ".SZ": "SHE"}, "default_suffix": ".SS"},
    "BR": {"suffix_to_gfinance": {".SA": "BVMF"}, "default_suffix": ".SA"},
    "ZA": {"suffix_to_gfinance": {".JO": "JSE"}, "default_suffix": ".JO"},
    "CH": {"suffix_to_gfinance": {".SW": "SWX"}, "default_suffix": ".SW"},
    "DK": {"suffix_to_gfinance": {".CO": "CPH"}, "default_suffix": ".CO"},
    "FI": {"suffix_to_gfinance": {".HE": "HEL"}, "default_suffix": ".HE"},
    "SE": {"suffix_to_gfinance": {".ST": "STO"}, "default_suffix": ".ST"},
    "KR": {"suffix_to_gfinance": {".KS": "KRX", ".KQ": "KOSDAQ"}, "default_suffix": ".KS"},
    "SA": {"suffix_to_gfinance": {".SR": "TADAWUL"}, "default_suffix": ".SR"},
    "TW": {"suffix_to_gfinance": {".TW": "TPE", ".TWO": "TPEX"}, "default_suffix": ".TW"},
    "NO": {"suffix_to_gfinance": {".OL": "OSL"}, "default_suffix": ".OL"},
    # "EU" deliberately left unmapped: not a real country/exchange code. Some
    # symbols tagged "EU" carry a recognizable suffix anyway (e.g. ".PA",
    # ".OL") which hints at their real country, but that's a data-tagging
    # problem to fix at the source, not something to paper over here.
}


def resolve_ticker(symbol, mapping):
    """Returns (yahoo_ticker, gfinance_query) for a symbol given its
    country's EXCHANGE_MAP entry, reusing any suffix already present in
    the symbol instead of appending a second one on top of it."""
    for suffix in sorted(mapping["suffix_to_gfinance"], key=len, reverse=True):
        if suffix and symbol.endswith(suffix):
            base = symbol[: -len(suffix)]
            return symbol, f"{base}:{mapping['suffix_to_gfinance'][suffix]}"
    # no known suffix present — apply the default
    default_suffix = mapping["default_suffix"]
    gfinance_exchange = mapping["suffix_to_gfinance"].get(default_suffix) or mapping["suffix_to_gfinance"][""]
    return f"{symbol}{default_suffix}", f"{symbol}:{gfinance_exchange}"


def resolve_exchange_label(symbol, country):
    """Human-readable (yahoo_ticker, gfinance_query, exchange_label,
    mapped: bool) for one (symbol, country) pair. exchange_label is the
    Google Finance exchange code actually matched (or appended)."""
    mapping = EXCHANGE_MAP.get(country)
    if mapping is None:
        return symbol, symbol, None, False
    yahoo_ticker, gfinance_query = resolve_ticker(symbol, mapping)
    exchange_label = gfinance_query.split(":", 1)[1]
    return yahoo_ticker, gfinance_query, exchange_label, True
