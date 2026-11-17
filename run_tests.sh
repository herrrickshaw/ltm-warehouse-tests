#!/usr/bin/env bash
# Spins up a throwaway Postgres database, applies the same schema as the
# real warehouse, seeds synthetic test data covering the tricky cases
# (short history, gappy history, series-switching NSE symbols, symbols
# tagged under two countries, etc.), runs every test in tests/, then
# always drops the throwaway database — even if a test fails.
#
# Usage:
#   ./run_tests.sh
#
# Override the admin connection (used only to CREATE/DROP the throwaway
# database) if your local Postgres needs a different user/host:
#   PG_ADMIN_URL="postgresql://myuser@localhost/postgres" ./run_tests.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTS_DIR="$SCRIPT_DIR/tests"
SCRIPTS_DIR="$SCRIPT_DIR/scripts"

TEST_DB_NAME="${TEST_DB_NAME:-ltm_warehouse_test}"
PG_ADMIN_URL="${PG_ADMIN_URL:-postgresql://localhost/postgres}"

export TEST_DB="postgresql://localhost/${TEST_DB_NAME}"
export DB="$TEST_DB"

cleanup() {
    echo "Dropping test database '$TEST_DB_NAME'..."
    psql "$PG_ADMIN_URL" -c "DROP DATABASE IF EXISTS $TEST_DB_NAME" > /dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Creating test database '$TEST_DB_NAME'..."
psql "$PG_ADMIN_URL" -c "DROP DATABASE IF EXISTS $TEST_DB_NAME"
psql "$PG_ADMIN_URL" -c "CREATE DATABASE $TEST_DB_NAME"

echo "Applying schema..."
psql "$TEST_DB" -f "$TESTS_DIR/setup_test_schema.sql"

echo "Seeding synthetic test data..."
python3 "$TESTS_DIR/seed_test_data.py"

echo "Generating thin_history.csv fixture (also exercises find_thin_history_tickers.py itself)..."
(cd "$TESTS_DIR" && python3 "$SCRIPTS_DIR/find_thin_history_tickers.py" --out thin_history.csv)

echo
echo "Running test_backfill_logic.py..."
python3 "$TESTS_DIR/test_backfill_logic.py"

echo
echo "Running test_backfill_cli.py..."
python3 "$TESTS_DIR/test_backfill_cli.py"

echo
echo "Running test_nse_tier.py..."
python3 "$TESTS_DIR/test_nse_tier.py"

echo
echo "Running offline pytest suite (no DB, no network)..."
python3 -m pytest -q -p no:cacheprovider "$TESTS_DIR"

echo
echo "All tests passed."
