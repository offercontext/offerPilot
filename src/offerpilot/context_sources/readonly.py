"""Small SQLAlchemy adapter for a transaction-owned read-only SQLite handle.

The context projector owns the SQLite transaction and its connection lease.  A
few domain readers already expose a Session-bound API, so this module lets
those readers use the exact same snapshot without opening a second connection
or taking ownership of commit/rollback.  The DBAPI facade deliberately rejects
mutating SQL and makes transaction lifecycle methods no-ops.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
from typing import Any, NoReturn, cast

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool


def _check_read_sql(statement: object) -> str:
    if not isinstance(statement, str):
        raise TypeError("readonly SQL must be text")
    stripped = statement.lstrip().upper()
    if not (stripped.startswith("SELECT ") or stripped == "SELECT" or stripped.startswith("EXPLAIN SELECT ")):
        raise sqlite3.OperationalError("readonly context source rejected mutating SQL")
    return statement


class _ReadonlyCursor:
    """DBAPI cursor proxy that permits only read statements."""

    __slots__ = ("_cursor",)

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    def execute(self, statement: object, parameters: object = ()) -> _ReadonlyCursor:
        self._cursor.execute(_check_read_sql(statement), cast(Any, parameters))
        return self

    def executemany(self, statement: object, parameters: object) -> _ReadonlyCursor:
        _check_read_sql(statement)
        raise sqlite3.OperationalError("readonly context source rejected executemany")

    def executescript(self, script: object) -> _ReadonlyCursor:
        _check_read_sql(script)
        raise sqlite3.OperationalError("readonly context source rejected executescript")

    def fetchone(self) -> tuple[object, ...] | None:
        return cast(tuple[object, ...] | None, self._cursor.fetchone())

    def fetchmany(self, size: int | None = None) -> list[tuple[object, ...]]:
        rows = self._cursor.fetchmany() if size is None else self._cursor.fetchmany(size)
        return [tuple(row) for row in rows]

    def fetchall(self) -> list[tuple[object, ...]]:
        return [tuple(row) for row in self._cursor.fetchall()]

    def close(self) -> None:
        self._cursor.close()

    def __iter__(self) -> Iterator[tuple[object, ...]]:
        return iter(self._cursor)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class ReadonlyConnectionFacade:
    """Pool-like facade understood by :class:`sqlalchemy.engine.Connection`.

    ``Connection`` expects a pool proxied connection with a handful of
    lifecycle attributes.  The real object is intentionally not exposed to
    SQLAlchemy's pool: closing or rolling back the facade must leave the outer
    ContextSourceLoader transaction untouched.
    """

    __slots__ = ("_connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def is_valid(self) -> bool:
        return True

    @property
    def is_detached(self) -> bool:
        return False

    def detach(self) -> None:
        return None

    def cursor(self, *args: Any, **kwargs: Any) -> _ReadonlyCursor:
        return _ReadonlyCursor(self._connection.cursor(*args, **kwargs))

    def execute(self, statement: object, parameters: object = ()) -> _ReadonlyCursor:
        cursor = self.cursor()
        return cursor.execute(statement, parameters)

    def executemany(self, statement: object, parameters: object) -> _ReadonlyCursor:
        cursor = self.cursor()
        return cursor.executemany(statement, parameters)

    def executescript(self, script: object) -> _ReadonlyCursor:
        cursor = self.cursor()
        return cursor.executescript(script)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


@contextmanager
def readonly_session(connection: sqlite3.Connection) -> Iterator[Session]:
    """Yield a Session reading from ``connection`` without owning its UoW."""

    facade = ReadonlyConnectionFacade(connection)
    # The creator is never called because Connection receives ``facade``
    # directly.  It still gives SQLAlchemy's SQLite dialect a normal Engine so
    # ORM statements compile and execute against the facade.
    def forbidden_connection() -> NoReturn:
        raise RuntimeError("readonly context source must use the outer connection")

    engine = create_engine(
        "sqlite://",
        creator=forbidden_connection,
        poolclass=NullPool,
    )
    sql_connection = Connection(
        engine,
        cast(Any, facade),
        _has_events=False,
    )
    session = Session(bind=sql_connection, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        sql_connection.close()
        engine.dispose()


__all__ = ["ReadonlyConnectionFacade", "readonly_session"]
