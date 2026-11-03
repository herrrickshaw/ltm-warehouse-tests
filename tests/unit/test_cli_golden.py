"""Run the three CLI scripts against a throwaway Postgres DB and compare to pre-refactor goldens.

Skipped automatically when no local Postgres is reachable (set PG_ADMIN_URL to override).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "tests" / "golden"
ADMIN = os.environ.get("PG_ADMIN_URL", "postgresql://localhost/postgres")
DBNAME = "ltm_pytest_golden"


@pytest.fixture(scope="module")
def db_url():
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        admin = psycopg2.connect(ADMIN)
    except psycopg2.OperationalError:
        pytest.skip("no local Postgres reachable")
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {DBNAME}")
    cur.execute(f"CREATE DATABASE {DBNAME}")
    url = urlsplit(ADMIN)._replace(path=f"/{DBNAME}").geturl()  # keep the admin URL's user/host/port
    conn = psycopg2.connect(url)
    conn.autocommit = True
    conn.cursor().execute((ROOT / "tests" / "setup_test_schema.sql").read_text())
    conn.close()
    env = {**os.environ, "DB": url}
    subprocess.run([sys.executable, str(ROOT / "tests" / "seed_test_data.py")], env=env, check=True, capture_output=True)
    yield url
    cur.execute(f"DROP DATABASE IF EXISTS {DBNAME}")
    admin.close()


def run_script(name, url, cwd, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / name), *args], cwd=cwd,
                          env={**os.environ, "DB": url}, check=True, capture_output=True, text=True).stdout


def test_find_thin_history_matches_golden(db_url, tmp_path):
    out = run_script("find_thin_history_tickers.py", db_url, tmp_path, "--out", "thin.csv")
    assert (tmp_path / "thin.csv").read_text() == (GOLD / "thin.csv").read_text()
    assert out == (GOLD / "thin.out").read_text()


def test_build_stock_exchange_list_matches_golden(db_url, tmp_path):
    out = run_script("build_stock_exchange_list.py", db_url, tmp_path, "--out", "ex.csv", "--store-table")
    assert (tmp_path / "ex.csv").read_text() == (GOLD / "ex.csv").read_text()
    assert out == (GOLD / "ex.out").read_text()
    psycopg2 = pytest.importorskip("psycopg2")
    with psycopg2.connect(db_url) as c, c.cursor() as cur:
        cur.execute("select * from ltm_symbol_exchange order by 1,2")
        got = "\n".join("|".join(str(v) for v in r) for r in cur.fetchall()) + "\n"
    assert got.replace("True", "t") == (GOLD / "ex_table.txt").read_text()
