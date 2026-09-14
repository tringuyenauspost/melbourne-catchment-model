"""Connect to the Optilogic PostgreSQL endpoint.

Reads credentials from the gitignored `.env` at the repo root. Run it directly
for a smoke test; import `connect()` / `query()` from anything else.

    python utilities/db_connect.py            # smoke test
    python utilities/db_connect.py "select 1" # run one statement
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"

# Port 6432 is PgBouncer in transaction-pooling mode: server-side prepared
# statements do not survive between statements, hence prepare_threshold=None.
POOLER_ARGS = {"prepare_threshold": None}


def _load_env() -> dict[str, str]:
    if not ENV.exists():
        sys.exit(f"missing {ENV} - see README for the required keys")
    env = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    if not env.get("PGPASSWORD"):
        sys.exit(f"PGPASSWORD is empty in {ENV} - paste it from Optilogic first")
    return env


def connect(**kwargs) -> psycopg.Connection:
    env = _load_env()
    return psycopg.connect(
        host=env["PGHOST"],
        port=int(env["PGPORT"]),
        dbname=env["PGDATABASE"],
        user=env["PGUSER"],
        password=env["PGPASSWORD"],
        sslmode=env.get("PGSSLMODE", "require"),
        connect_timeout=15,
        **POOLER_ARGS,
        **kwargs,
    )


def query(sql: str, params=None) -> list[tuple]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _smoke_test() -> int:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("select current_user, current_database(), version()")
            user, db, version = cur.fetchone()
            print(f"connected  user={user}  db={db}")
            print(f"server     {version.split(' on ')[0]}")

            cur.execute("""
                select table_schema, count(*)
                from information_schema.tables
                where table_schema not in ('pg_catalog', 'information_schema')
                group by table_schema order by 2 desc
            """)
            rows = cur.fetchall()
            if rows:
                print("schemas    " + ", ".join(f"{s} ({n} tables)" for s, n in rows))
            else:
                print("schemas    none visible - database is empty")
    except psycopg.OperationalError as exc:
        print(f"FAILED to connect: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for row in query(sys.argv[1]):
            print(row)
        sys.exit(0)
    sys.exit(_smoke_test())
