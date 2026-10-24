"""Which tracker the dashboard and the agent use: the one place that decides it.

The applications, their documents, run history, employer accounts and learned answers are kept by a tracker.
Postgres was the only full one, so anyone running the agent needed a database server (Docker) first. Now:

  TRACKER=sqlite     SQLite in data/applications.db -- nothing to install
  TRACKER=postgres   Postgres, from POSTGRES_* in .env
  (not set)          Postgres when POSTGRES_PASSWORD is set in .env (an existing setup keeps its data),
                     otherwise SQLite

A configured Postgres that is down fails loudly, like one that refuses the password or has no database: a quiet
fall-back to SQLite split the history between two databases (21-23 October 2026: about 1,800 fall-backs, and the
answer bank rebuilt from the smaller one). Set TRACKER=sqlite to use SQLite on purpose.
"""
from __future__ import annotations

import os


def chosen() -> str:
    """'postgres' or 'sqlite', from TRACKER or from whether Postgres is configured."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    wanted = (os.getenv("TRACKER") or "").strip().lower()
    if wanted in ("postgres", "sqlite"):
        return wanted
    return "postgres" if os.getenv("POSTGRES_PASSWORD") else "sqlite"


def open_tracker(db_path=None):
    """The tracker to use. `db_path` overrides where SQLite keeps its file."""
    from config import DB_PATH
    from job_tracker import JobTracker
    path = db_path or DB_PATH
    if chosen() == "sqlite":
        return JobTracker(path)
    try:
        import db
    except ImportError as exc:                      # psycopg not installed
        raise RuntimeError(f"TRACKER=postgres but the Postgres driver is not installed: {exc}") from exc
    try:
        return db.get_tracker()
    except Exception as exc:
        if db.is_transient_connection_error(exc):
            raise RuntimeError(f"Postgres is configured but not reachable: {str(exc).splitlines()[0][:120]}. "
                               "Start it with `docker compose up -d`, or set TRACKER=sqlite to use SQLite "
                               "on purpose.") from exc
        raise RuntimeError(f"Postgres tracking failed; refusing to fall back to SQLite: {exc}") from exc
