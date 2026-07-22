# ltm-warehouse-tests

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/ltm-warehouse-tests/blob/main/notebooks/colab_test.ipynb)

Test harness for the LTM warehouse maintenance scripts (`scripts/`) —
exchange resolution, thin-history detection, and the multi-tier
Yahoo/NSE/SerpApi backfill. Every script here was built and verified
against a synthetic Postgres warehouse before ever being run against the
real one; this repo is that verification harness, kept around so changes
to the scripts can be re-checked instead of trusted blind.

## Why this exists

Real warehouse data kept surfacing cases synthetic data alone wouldn't:
tickers already carrying an exchange suffix (`3696.HK`), a symbol tagged
under two different countries, NSE series switching mid-history, bond
ISINs mixed into the ticker universe. Each of those became a test case
here so a future change can't silently reintroduce the same bug.

## What's covered

- `tests/setup_test_schema.sql` — mirrors the real warehouse schema
  (`ltm_symbols`, `ltm_fundamentals_fact`, the `ltm_fundamentals` view).
- `tests/seed_test_data.py` — synthetic symbols covering short history,
  gappy-but-old history, and sufficient history, across multiple
  countries.
- `tests/test_backfill_logic.py` — dedup against existing dates, correct
  routing (Yahoo → `ltm_fundamentals_fact`, SerpApi → the low-fidelity
  `ltm_price_estimate` table), dry-run never persists.
- `tests/test_backfill_cli.py` — smoke test of the actual CLI entrypoint
  (argument parsing, CSV loading), not just the underlying functions.
- `tests/test_nse_tier.py` — the NSE-direct fallback tier: only triggers
  for India, correctly collapses a symbol that switched series mid-range
  to one row per date, and correctly skips dates already covered by
  Yahoo.

## Running

```bash
pip install -r requirements.txt
./run_tests.sh
```

This creates a throwaway `ltm_warehouse_test` database, applies the
schema, seeds it, runs every test, and drops the database afterward —
whether the tests pass or fail. It never touches your real warehouse.

If your local Postgres needs a different admin connection to
create/drop databases:

```bash
PG_ADMIN_URL="postgresql://myuser@localhost/postgres" ./run_tests.sh
```

## Adding a new test

When a change to a `scripts/*.py` file is driven by something the real
warehouse exposed (a new edge case, not just a refactor), add it as a
seeded case in `seed_test_data.py` plus an assertion in the relevant
`test_*.py` — that's what keeps this harness worth trusting.
