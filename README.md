# LTM warehouse maintenance scripts and test harness

Maintenance scripts for a Postgres "LTM" price warehouse (global equities, daily OHLCV) together with the
synthetic-warehouse harness that verifies them. The scripts resolve each symbol's exchange, find tickers with
short or gappy history, and backfill them from Yahoo Finance, NSE direct (India) or SerpApi. Outputs are
CSV files (`thin_history.csv`, `stock_exchange_list.csv`) and rows in the warehouse tables.

> Status: maintained. The scripts were verified against a synthetic warehouse before being run on the real one;
> no real market data is stored in this repo. Not investment advice.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/ltm-warehouse-tests/blob/main/notebooks/colab_test.ipynb)
New to the repo? See [How to use this repo with AI](HOW_TO_USE_WITH_AI.md).

## What it does
- Maps `(symbol, country)` to a Yahoo ticker and Google Finance query without doubling suffixes (`3696.HK` stays `3696.HK`).
- Flags symbols with under N years of history, or with gappy day-coverage inside their span.
- Backfills in three tiers: Yahoo (real OHLCV) -> NSE via `jugaad-data` (India only) -> SerpApi (close-only estimates).
- Never overwrites existing rows (`ON CONFLICT DO NOTHING`) and is dry-run unless `--apply` is passed.
- Optionally stores the full symbol-to-exchange reference in `ltm_symbol_exchange`.

## Quick start
```bash
git clone https://github.com/herrrickshaw/ltm-warehouse-tests.git
cd ltm-warehouse-tests
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest ruff
./run_tests.sh        # needs a local Postgres; creates and drops a throwaway database
```

Running the scripts against a real warehouse (from `scripts/`):
```bash
export DB=postgresql://user:pass@host:5432/dbname
python find_thin_history_tickers.py --min-years 5 --gap-threshold 0.85 --out thin_history.csv
python backfill_missing_history.py --csv thin_history.csv [--apply] [--serpapi-key KEY] [--sleep 1.0] [--limit 20] [--nse-lookback-years 10]
python build_stock_exchange_list.py --out stock_exchange_list.csv [--store-table]
```

## How it works
| Stage | Module | Output |
|---|---|---|
| Country/suffix -> exchange | `scripts/exchange_map.py` | `(yahoo_ticker, gfinance_query)` |
| Detect thin/gappy history | `scripts/find_thin_history_tickers.py` (`classify_history`) | `thin_history.csv` |
| Backfill (Yahoo -> NSE -> SerpApi) | `scripts/backfill_missing_history.py` | rows in `ltm_fundamentals_fact` / `ltm_price_estimate` |
| Reference list | `scripts/build_stock_exchange_list.py` | `stock_exchange_list.csv`, `ltm_symbol_exchange` |

Design notes: SerpApi only returns a price graph, so it writes to a separate `ltm_price_estimate` table
tagged `source='serpapi'` and can never be mistaken for real OHLCV. Countries missing from `EXCHANGE_MAP`
are excluded rather than guessed, because a bare ticker can match an unrelated company.

## Repository layout
| Path | Purpose |
|---|---|
| `scripts/` | The four warehouse scripts |
| `tests/setup_test_schema.sql`, `tests/seed_test_data.py` | Synthetic schema and seed data (short, gappy, sufficient history; multiple countries) |
| `tests/test_*.py`, `run_tests.sh` | Original end-to-end scripts run against a throwaway Postgres DB |
| `tests/unit/` | pytest suite (offline unit tests plus DB-backed golden tests) |
| `tests/golden/` | Outputs captured from the pre-refactor scripts; the refactor must reproduce them |
| `notebooks/colab_test.ipynb` | Colab runner for the harness |
| `.github/workflows/` | `ci.yml` (ruff + pytest with Postgres), `quality-gates.yml` (gitleaks) |

## Data sources
| Source | What is taken | Access route | Licence / caveat |
|---|---|---|---|
| Yahoo Finance | Daily OHLCV | `yfinance`, no key | Unofficial API; personal use |
| NSE India | Daily OHLCV incl. SME series | `jugaad-data` | Unofficial; India only |
| Google Finance | Close-price graph | SerpApi (`SERPAPI_KEY`) | Paid beyond free tier; estimates only |

Tests never touch the network; sources are stubbed.

## Configuration
- `DB` - SQLAlchemy URL of the warehouse (required by the scripts).
- `SERPAPI_KEY` - optional, or `--serpapi-key`. Never commit it.
- `PG_ADMIN_URL` - admin connection used to create/drop the throwaway test DB (default `postgresql://localhost/postgres`).
- `TEST_DB_NAME` - throwaway DB name (default `ltm_warehouse_test`).

## Testing
```bash
pytest -q          # tests/unit; DB-backed golden tests skip if Postgres is unreachable
./run_tests.sh     # original end-to-end scripts
ruff check .
```
When a change is driven by a new real-warehouse edge case, add it to `seed_test_data.py` plus an assertion.

## Limitations and known issues
- Requires `sqlalchemy<2.1`: 2.1 defaults `postgresql://` to psycopg3, which is not a dependency.
- `tests/test_*.py` are scripts that execute on import, so pytest only collects `tests/unit`.
- Symbols tagged `EU` (not a real country code) are unmapped by design; fix the tagging at the source.
- Yahoo `.NS` coverage misses many NSE SME names; the NSE tier exists for that reason.

## Licence
No licence file yet - all rights reserved.
