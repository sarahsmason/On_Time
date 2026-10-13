"""Persistence for On_Time.

`Repository` is the interface the tools use; `SqliteRepository` is the local implementation.
Records are stored as JSON documents keyed by id, so a DynamoDB implementation (Phase 4)
maps one-to-one.
"""

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from on_time.models import Adjustment, CheckIn, CheckInStatus, Light, Location, Routine

M = TypeVar("M", bound=BaseModel)


class Repository(Protocol):
    def get_location(self) -> Location | None: ...
    def set_location(self, location: Location) -> None: ...

    def list_lights(self) -> list[Light]: ...
    def get_light(self, light_id: str) -> Light | None: ...
    def upsert_light(self, light: Light) -> None: ...

    def list_routines(self) -> list[Routine]: ...
    def get_routine(self, routine_id: str) -> Routine | None: ...
    def upsert_routine(self, routine: Routine) -> None: ...
    def delete_routine(self, routine_id: str) -> bool: ...

    def add_adjustment(self, adjustment: Adjustment) -> None: ...
    def list_adjustments(
        self, *, light_id: str | None = None, routine_id: str | None = None, since: datetime | None = None
    ) -> list[Adjustment]: ...

    def list_checkins(self, *, status: CheckInStatus | None = None) -> list[CheckIn]: ...
    def get_checkin(self, checkin_id: str) -> CheckIn | None: ...
    def upsert_checkin(self, checkin: CheckIn) -> None: ...

    def get_setting(self, key: str) -> str | None: ...
    def set_setting(self, key: str, value: str | None) -> None: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lights (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS routines (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS adjustments (
    id TEXT PRIMARY KEY,
    light_id TEXT NOT NULL,
    routine_id TEXT,
    at_utc TEXT NOT NULL,
    doc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS adjustments_light_at ON adjustments (light_id, at_utc);
CREATE INDEX IF NOT EXISTS adjustments_routine_at ON adjustments (routine_id, at_utc);
CREATE TABLE IF NOT EXISTS checkins (id TEXT PRIMARY KEY, status TEXT NOT NULL, created_utc TEXT NOT NULL, doc TEXT NOT NULL);
"""

_LOCATION_KEY = "location"


def _utc_key(dt: datetime) -> str:
    """Sortable UTC string for range queries on aware datetimes."""
    return dt.astimezone(UTC).isoformat()


class SqliteRepository:
    """SQLite-backed repository. Use ':memory:' for tests."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- helpers ---------------------------------------------------------------

    def _one(self, sql: str, params: tuple, model: type[M]) -> M | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return model.model_validate_json(row[0]) if row else None

    def _many(self, sql: str, params: tuple, model: type[M]) -> list[M]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [model.model_validate_json(r[0]) for r in rows]

    def _write(self, sql: str, params: tuple) -> int:
        with self._lock, self._conn:
            return self._conn.execute(sql, params).rowcount

    # -- settings / location ---------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str | None) -> None:
        if value is None:
            self._write("DELETE FROM settings WHERE key = ?", (key,))
        else:
            self._write("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    def get_location(self) -> Location | None:
        raw = self.get_setting(_LOCATION_KEY)
        return Location.model_validate_json(raw) if raw else None

    def set_location(self, location: Location) -> None:
        self.set_setting(_LOCATION_KEY, location.model_dump_json())

    # -- lights ----------------------------------------------------------------

    def list_lights(self) -> list[Light]:
        return self._many("SELECT doc FROM lights ORDER BY id", (), Light)

    def get_light(self, light_id: str) -> Light | None:
        return self._one("SELECT doc FROM lights WHERE id = ?", (light_id,), Light)

    def upsert_light(self, light: Light) -> None:
        self._write("INSERT OR REPLACE INTO lights (id, doc) VALUES (?, ?)", (light.id, light.model_dump_json()))

    # -- routines --------------------------------------------------------------

    def list_routines(self) -> list[Routine]:
        return self._many("SELECT doc FROM routines ORDER BY id", (), Routine)

    def get_routine(self, routine_id: str) -> Routine | None:
        return self._one("SELECT doc FROM routines WHERE id = ?", (routine_id,), Routine)

    def upsert_routine(self, routine: Routine) -> None:
        self._write(
            "INSERT OR REPLACE INTO routines (id, doc) VALUES (?, ?)", (routine.id, routine.model_dump_json())
        )

    def delete_routine(self, routine_id: str) -> bool:
        return self._write("DELETE FROM routines WHERE id = ?", (routine_id,)) > 0

    # -- adjustments -----------------------------------------------------------

    def add_adjustment(self, adjustment: Adjustment) -> None:
        self._write(
            "INSERT INTO adjustments (id, light_id, routine_id, at_utc, doc) VALUES (?, ?, ?, ?, ?)",
            (
                adjustment.id,
                adjustment.light_id,
                adjustment.routine_id,
                _utc_key(adjustment.at),
                adjustment.model_dump_json(),
            ),
        )

    def list_adjustments(
        self, *, light_id: str | None = None, routine_id: str | None = None, since: datetime | None = None
    ) -> list[Adjustment]:
        clauses, params = [], []
        if light_id is not None:
            clauses.append("light_id = ?")
            params.append(light_id)
        if routine_id is not None:
            clauses.append("routine_id = ?")
            params.append(routine_id)
        if since is not None:
            clauses.append("at_utc >= ?")
            params.append(_utc_key(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._many(f"SELECT doc FROM adjustments {where} ORDER BY at_utc", tuple(params), Adjustment)

    # -- check-ins -------------------------------------------------------------

    def list_checkins(self, *, status: CheckInStatus | None = None) -> list[CheckIn]:
        if status is None:
            return self._many("SELECT doc FROM checkins ORDER BY created_utc", (), CheckIn)
        return self._many(
            "SELECT doc FROM checkins WHERE status = ? ORDER BY created_utc", (status.value,), CheckIn
        )

    def get_checkin(self, checkin_id: str) -> CheckIn | None:
        return self._one("SELECT doc FROM checkins WHERE id = ?", (checkin_id,), CheckIn)

    def upsert_checkin(self, checkin: CheckIn) -> None:
        self._write(
            "INSERT OR REPLACE INTO checkins (id, status, created_utc, doc) VALUES (?, ?, ?, ?)",
            (checkin.id, checkin.status.value, _utc_key(checkin.created_at), checkin.model_dump_json()),
        )
