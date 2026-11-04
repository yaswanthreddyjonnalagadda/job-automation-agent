"""One Postgres connection serves a thread until it breaks (f314).

Every tracker method opened, authenticated and closed its own connection, so
one dashboard page paid a dozen Docker-Postgres handshakes. Connections are now
kept per thread (and per DSN) and reused; a dead one is dropped and the next
use reconnects. Fakes stand in for the server: no live Postgres needed.
"""
import threading

import pytest

import db


class FakeResult:
    def fetchall(self):
        return []

    def fetchone(self):
        return None


class FakeConn:
    def __init__(self):
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def execute(self, *args, **kwargs):
        if self.closed:
            raise db.psycopg.OperationalError("connection is closed")
        return FakeResult()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True
        self.closes += 1


@pytest.fixture
def counting_connect(monkeypatch):
    made = []
    monkeypatch.setattr(db.psycopg, "connect",
                        lambda *a, **k: made.append(FakeConn()) or made[-1])
    db._THREAD_CONNS.__dict__.clear()
    return made


def test_repeated_use_opens_one_connection(counting_connect):
    tracker = db.PostgresTracker("dsn")
    with tracker._connect():
        pass
    with tracker._connect():
        pass
    assert len(counting_connect) == 1
    assert counting_connect[0].commits == 3 and counting_connect[0].closes == 0


def test_an_sql_error_rolls_back_but_keeps_the_connection(counting_connect):
    tracker = db.PostgresTracker("dsn")
    with pytest.raises(ValueError):
        with tracker._connect():
            raise ValueError("bad query")
    assert counting_connect[0].rollbacks == 1
    with tracker._connect():
        pass
    assert len(counting_connect) == 1


def test_a_broken_connection_is_dropped_and_reopened(counting_connect):
    tracker = db.PostgresTracker("dsn")
    with pytest.raises(db.psycopg.OperationalError):
        with tracker._connect():
            raise db.psycopg.OperationalError("server went away")
    with tracker._connect():
        pass
    assert len(counting_connect) == 2


def test_another_thread_gets_its_own_connection(counting_connect):
    tracker = db.PostgresTracker("dsn")
    with tracker._connect():
        pass
    errors = []

    def use():
        try:
            with tracker._connect():
                pass
        except Exception as exc:  # noqa: BLE001 -- reported below
            errors.append(exc)

    worker = threading.Thread(target=use)
    worker.start()
    worker.join()
    assert not errors
    assert len(counting_connect) == 2
