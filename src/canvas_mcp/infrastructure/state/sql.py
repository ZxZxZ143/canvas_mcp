"""SQLite and PostgreSQL adapters share a compact schema and repository contract.

Every check locks before collecting Canvas. SQLite locks the local writer;
PostgreSQL locks an owner row. No request implicitly creates/migrates tables.
Blocking DB work runs outside the event loop, with cancellation joined before rollback.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.resources import files
import sqlite3
from typing import Any, TypeVar, cast

from canvas_mcp.domain.errors import StateStoreUnavailableError
from canvas_mcp.domain.grade_state import GradeFact
from canvas_mcp.domain.models import EntityId
from canvas_mcp.infrastructure.state.settings import StateSettings
from canvas_mcp.ports.state import StateTransaction
from canvas_mcp.infrastructure.logging.events import SENSITIVE_STATE, protect_state_logging

T = TypeVar("T")


async def joined(call: Callable[[], T]) -> T:
    protect_state_logging()
    token = SENSITIVE_STATE.set(True)
    try:
        task = asyncio.create_task(asyncio.to_thread(call))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # A thread cannot be cancelled; do not race rollback/close against it.
            await task
            raise
    finally:
        SENSITIVE_STATE.reset(token)


class SqlTransaction:
    def __init__(self, connection: Any, owner: str, postgres: bool) -> None:
        self.connection, self.owner, self.postgres = connection, owner, postgres

    async def query(self, sql: str, params: tuple[object, ...] = ()) -> list[Any]:
        statement = sql.replace("?", "%s") if self.postgres else sql

        def run() -> list[Any]:
            with (
                self.connection.cursor()
                if self.postgres
                else _SQLiteCursor(self.connection) as cursor
            ):
                cursor.execute(statement, params)
                return list(cursor.fetchall()) if cursor.description is not None else []

        return await joined(run)

    async def insert_facts(self, rows: list[tuple[object, ...]]) -> None:
        statement = "INSERT INTO grade_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        if self.postgres:
            statement = statement.replace("?", "%s")

        def run() -> None:
            with (
                self.connection.cursor()
                if self.postgres
                else _SQLiteCursor(self.connection) as cursor
            ):
                cursor.executemany(statement, rows)

        await joined(run)

    async def initialized(self) -> bool:
        rows = await self.query("SELECT initialized FROM state_owners WHERE owner=?", (self.owner,))
        return rows[0][0] == 1

    async def baseline(self, course_id: EntityId) -> tuple[GradeFact, ...] | None:
        courses = await self.query(
            "SELECT course_id FROM state_courses WHERE owner=? AND course_id=?",
            (self.owner, course_id),
        )
        if not courses:
            return None
        rows = await self.query(
            "SELECT course_id,assignment_id,attempt,score,grade,points_possible,graded_at,posted_at,"
            "workflow,visibility FROM grade_state WHERE owner=? AND snapshot='baseline' "
            "AND course_id=? ORDER BY assignment_id",
            (self.owner, course_id),
        )
        return tuple(
            GradeFact(
                EntityId(row[0]),
                EntityId(row[1]),
                row[2],
                row[3],
                row[4],
                row[5],
                None if row[6] is None else datetime.fromisoformat(row[6]),
                None if row[7] is None else datetime.fromisoformat(row[7]),
                row[8],
                row[9],
            )
            for row in rows
        )

    async def replace_course(
        self,
        course_id: EntityId,
        facts: tuple[GradeFact, ...],
        checked_at: datetime,
        *,
        baseline: tuple[GradeFact, ...] | None = None,
    ) -> None:
        prior = await self.query(
            "SELECT assignment_id,first_seen_at,state_hash,state_version FROM grade_state "
            "WHERE owner=? AND snapshot='latest' AND course_id=?",
            (self.owner, course_id),
        )
        old = {row[0]: row[1:] for row in prior}
        stamp = checked_at.isoformat()
        await self.query(
            "DELETE FROM grade_state WHERE owner=? AND course_id=?", (self.owner, course_id)
        )
        await self.query(
            "INSERT INTO state_courses(owner,course_id) VALUES (?,?) ON CONFLICT DO NOTHING",
            (self.owner, course_id),
        )
        rows: list[tuple[object, ...]] = []
        for snapshot, values in (
            ("latest", facts),
            ("baseline", baseline if baseline is not None else facts),
        ):
            for fact in values:
                previous = old.get(fact.assignment_id)
                first = previous[0] if previous else stamp
                version = previous[2] + int(previous[1] != fact.state_hash) if previous else 1
                rows.append(
                    (
                        self.owner,
                        snapshot,
                        course_id,
                        fact.assignment_id,
                        fact.attempt,
                        fact.score,
                        fact.grade,
                        fact.points_possible,
                        fact.graded_at.isoformat() if fact.graded_at else None,
                        fact.posted_at.isoformat() if fact.posted_at else None,
                        fact.workflow,
                        fact.visibility,
                        first,
                        stamp,
                        fact.state_hash,
                        version,
                    ),
                )
        await self.insert_facts(rows)

    async def mark_checked(self) -> None:
        await self.query("UPDATE state_owners SET initialized=1 WHERE owner=?", (self.owner,))


class _SQLiteCursor:
    def __init__(self, connection: sqlite3.Connection):
        self.cursor = connection.cursor()

    def __enter__(self) -> sqlite3.Cursor:
        return self.cursor

    def __exit__(self, *args: object) -> None:
        self.cursor.close()


class SqlRepository:
    def __init__(self, settings: StateSettings):
        self._settings = settings
        self._postgres = settings.backend == "postgresql"

    def _connect(self) -> Any:
        if self._postgres:
            import psycopg

            return psycopg.connect(self._settings.url, connect_timeout=5, autocommit=False)
        assert self._settings.sqlite_path is not None
        db = sqlite3.connect(self._settings.sqlite_path, timeout=75, check_same_thread=False)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    async def _verify(self, tx: SqlTransaction) -> None:
        if await tx.query("SELECT version FROM state_schema") != [(1,)]:
            raise StateStoreUnavailableError()

    @asynccontextmanager
    async def transaction(self, owner: str) -> AsyncIterator[StateTransaction]:
        connection = None
        try:
            connection = await joined(self._connect)
            tx = SqlTransaction(connection, owner, self._postgres)
            if self._postgres:
                await tx.query("SET LOCAL lock_timeout='75s'")
                await tx.query("SET LOCAL statement_timeout='80s'")
                await tx.query("SET LOCAL idle_in_transaction_session_timeout='90s'")
            else:
                await tx.query("BEGIN IMMEDIATE")
            await self._verify(tx)
            await tx.query(
                "INSERT INTO state_owners(owner) VALUES (?) ON CONFLICT DO NOTHING", (owner,)
            )
            if self._postgres:
                await tx.query("SELECT owner FROM state_owners WHERE owner=? FOR UPDATE", (owner,))
            try:
                yield tx
                await joined(connection.commit)
            except BaseException:
                await joined(connection.rollback)
                raise
        except (sqlite3.Error, OSError, ImportError):
            raise StateStoreUnavailableError() from None
        except Exception as error:
            # DB driver failures must not retain/log DSNs or unsafe exception text.
            if type(error).__module__.startswith("psycopg"):
                raise StateStoreUnavailableError() from None
            raise
        finally:
            if connection is not None:
                await joined(connection.close)

    async def migrate(self) -> None:
        """Operator-only explicit migration; idempotent, fail closed for unknown versions."""
        connection = None
        try:
            connection = await joined(self._connect)
            tx = SqlTransaction(connection, "", self._postgres)
            if self._postgres:
                await tx.query("SELECT pg_advisory_xact_lock(721001)")
                exists = await tx.query("SELECT to_regclass('state_schema')")
                installed = exists[0][0] is not None
            else:
                await tx.query("BEGIN IMMEDIATE")
                installed = bool(
                    await tx.query(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='state_schema'"
                    )
                )
            if installed:
                await self._verify(tx)
            else:
                script = files("canvas_mcp.infrastructure.state").joinpath("001.sql").read_text()
                for statement in script.split(";"):
                    if statement.strip():
                        await tx.query(statement)
            await joined(connection.commit)
        except Exception:
            if connection is not None:
                await joined(connection.rollback)
            raise StateStoreUnavailableError() from None
        finally:
            if connection is not None:
                await joined(connection.close)

    async def reset(self, owner: str) -> None:
        async with self.transaction(owner) as transaction:
            tx = cast(SqlTransaction, transaction)
            await tx.query("DELETE FROM grade_state WHERE owner=?", (owner,))
            await tx.query("DELETE FROM state_courses WHERE owner=?", (owner,))
            await tx.query("UPDATE state_owners SET initialized=0 WHERE owner=?", (owner,))


class SQLiteStateRepository(SqlRepository):
    def __init__(self, settings: StateSettings):
        if settings.backend != "sqlite":
            raise StateStoreUnavailableError()
        super().__init__(settings)


class PostgreSQLStateRepository(SqlRepository):
    def __init__(self, settings: StateSettings):
        if settings.backend != "postgresql":
            raise StateStoreUnavailableError()
        super().__init__(settings)


class UnavailableStateRepository:
    @asynccontextmanager
    async def transaction(self, owner: str) -> AsyncIterator[StateTransaction]:
        raise StateStoreUnavailableError()
        yield  # pragma: no cover

    async def reset(self, owner: str) -> None:
        raise StateStoreUnavailableError()
