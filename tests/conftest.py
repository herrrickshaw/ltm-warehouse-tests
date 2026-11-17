"""pytest config. The DB-backed tests (test_backfill_logic/cli, test_nse_tier,
seed_test_data) are plain scripts that need the throwaway Postgres that
run_tests.sh creates and they run in a fixed order, so pytest leaves them to
run_tests.sh. Everything pytest collects here is offline and DB-free."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

collect_ignore = [
    "seed_test_data.py",
    "test_backfill_logic.py",
    "test_backfill_cli.py",
    "test_nse_tier.py",
]
