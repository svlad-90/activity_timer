"""Persistent interval storage and daily aggregation."""

from __future__ import annotations

import fcntl
import sqlite3
import time
from collections.abc import Callable
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Dict, List, Optional


LYING = "lying"
SITTING = "sitting"
STANDING = "standing"
SEMI_SITTING = "semi_sitting"
APP_OFF = "app_off"

ACTIVITY_STATES = (LYING, SEMI_SITTING, SITTING, STANDING)
ALL_STATES = ACTIVITY_STATES + (APP_OFF,)


class InstanceAlreadyRunningError(RuntimeError):
    """Raised when another process owns the activity database."""


class ActivityStore:
    """Store activity intervals and recover conservatively after a crash."""

    def __init__(
        self,
        database_path: Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.database_path = database_path
        self._clock = clock
        self._current_id: Optional[int] = None
        self._current_state: Optional[str] = None
        self._current_started_at: Optional[float] = None

        database_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = database_path.with_name(database_path.name + ".lock")
        self._lock_file = lock_path.open("a+")
        try:
            fcntl.flock(
                self._lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as error:
            self._lock_file.close()
            raise InstanceAlreadyRunningError(
                "another activity timer instance is already running"
            ) from error

        try:
            self._connection = sqlite3.connect(database_path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._create_schema()
            self._recover_open_interval()
        except Exception:
            self._release_instance_lock()
            raise

    def _release_instance_lock(self) -> None:
        if self._lock_file.closed:
            return
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        self._lock_file.close()

    def _create_schema(self) -> None:
        states = ", ".join(f"'{state}'" for state in ALL_STATES)
        with self._connection:
            self._connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS intervals (
                    id INTEGER PRIMARY KEY,
                    state TEXT NOT NULL CHECK (state IN ({states})),
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    CHECK (ended_at IS NULL OR ended_at >= started_at)
                );

                CREATE INDEX IF NOT EXISTS intervals_by_time
                    ON intervals (started_at, ended_at);

                CREATE UNIQUE INDEX IF NOT EXISTS one_open_interval
                    ON intervals ((1)) WHERE ended_at IS NULL;

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS drinks (
                    id INTEGER PRIMARY KEY,
                    drank_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS drinks_by_time
                    ON drinks (drank_at);
                """
            )
        schema_row = self._connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'intervals'"
        ).fetchone()
        schema_sql = "" if schema_row is None else str(schema_row["sql"])
        if f"'{SEMI_SITTING}'" not in schema_sql:
            self._migrate_intervals_for_semi_sitting(states)

    def _migrate_intervals_for_semi_sitting(self, states: str) -> None:
        """Expand the state constraint while preserving every existing interval."""
        with self._connection:
            self._connection.execute("DROP INDEX IF EXISTS intervals_by_time")
            self._connection.execute("DROP INDEX IF EXISTS one_open_interval")
            self._connection.execute(
                "ALTER TABLE intervals RENAME TO intervals_before_semi_sitting"
            )
            self._connection.execute(
                f"""
                CREATE TABLE intervals (
                    id INTEGER PRIMARY KEY,
                    state TEXT NOT NULL CHECK (state IN ({states})),
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    CHECK (ended_at IS NULL OR ended_at >= started_at)
                )
                """
            )
            self._connection.execute(
                """
                INSERT INTO intervals (id, state, started_at, ended_at)
                SELECT id, state, started_at, ended_at
                FROM intervals_before_semi_sitting
                """
            )
            self._connection.execute("DROP TABLE intervals_before_semi_sitting")
            self._connection.execute(
                "CREATE INDEX intervals_by_time ON intervals (started_at, ended_at)"
            )
            self._connection.execute(
                """
                CREATE UNIQUE INDEX one_open_interval
                ON intervals ((1)) WHERE ended_at IS NULL
                """
            )

    def _recover_open_interval(self) -> None:
        """Close a crashed session at its last persisted heartbeat."""
        row = self._connection.execute(
            "SELECT id, started_at FROM intervals WHERE ended_at IS NULL"
        ).fetchone()
        if row is None:
            return

        last_seen = self._get_last_seen()
        cutoff = row["started_at"] if last_seen is None else last_seen
        cutoff = max(row["started_at"], cutoff)
        with self._connection:
            self._connection.execute(
                "UPDATE intervals SET ended_at = ? WHERE id = ?",
                (cutoff, row["id"]),
            )
            self._set_last_seen(cutoff)

    def _get_last_seen(self) -> Optional[float]:
        row = self._connection.execute(
            "SELECT value FROM metadata WHERE key = 'last_seen'"
        ).fetchone()
        return None if row is None else float(row["value"])

    def _set_last_seen(self, timestamp: float) -> None:
        self._connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES ('last_seen', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (repr(timestamp),),
        )

    def _clamp_active_timestamp(self, timestamp: float) -> float:
        """Keep active-session timestamps from moving behind persisted data."""
        last_seen = self._get_last_seen()
        return max(
            timestamp,
            self._current_started_at or timestamp,
            last_seen if last_seen is not None else timestamp,
        )

    @property
    def current_state(self) -> Optional[str]:
        if self._current_state is not None:
            return self._current_state
        if self._get_last_seen() is not None:
            return APP_OFF
        return None

    def switch_state(self, state: str, at: Optional[float] = None) -> None:
        """Switch to an activity state, closing the preceding interval."""
        if state not in ACTIVITY_STATES:
            raise ValueError(f"Unsupported activity state: {state}")

        timestamp = self._clock() if at is None else at
        if state == self._current_state:
            self.heartbeat(timestamp)
            return

        with self._connection:
            if self._current_id is not None:
                timestamp = self._clamp_active_timestamp(timestamp)
                self._connection.execute(
                    "UPDATE intervals SET ended_at = ? WHERE id = ?",
                    (timestamp, self._current_id),
                )
            else:
                last_seen = self._get_last_seen()
                if last_seen is not None:
                    timestamp = max(timestamp, last_seen)
                    if timestamp > last_seen:
                        self._connection.execute(
                            """
                            INSERT INTO intervals (state, started_at, ended_at)
                            VALUES (?, ?, ?)
                            """,
                            (APP_OFF, last_seen, timestamp),
                        )

            cursor = self._connection.execute(
                "INSERT INTO intervals (state, started_at) VALUES (?, ?)",
                (state, timestamp),
            )
            new_interval_id = int(cursor.lastrowid)
            self._set_last_seen(timestamp)
        self._current_id = new_interval_id
        self._current_state = state
        self._current_started_at = timestamp

    def heartbeat(self, at: Optional[float] = None) -> None:
        """Persist the latest known-alive time for crash recovery."""
        if self._current_id is None:
            return
        timestamp = self._clock() if at is None else at
        timestamp = self._clamp_active_timestamp(timestamp)
        with self._connection:
            self._set_last_seen(timestamp)

    def record_inactive_gap(self, resumed_at: float) -> None:
        """Split an active interval around a detected process or VM pause."""
        if self._current_id is None or self._current_state is None:
            return
        last_seen = self._get_last_seen()
        gap_start = (
            self._current_started_at
            if last_seen is None
            else max(self._current_started_at or last_seen, last_seen)
        )
        resumed_at = max(resumed_at, gap_start)
        if resumed_at <= gap_start:
            return

        state = self._current_state
        with self._connection:
            self._connection.execute(
                "UPDATE intervals SET ended_at = ? WHERE id = ?",
                (gap_start, self._current_id),
            )
            self._connection.execute(
                """
                INSERT INTO intervals (state, started_at, ended_at)
                VALUES (?, ?, ?)
                """,
                (APP_OFF, gap_start, resumed_at),
            )
            cursor = self._connection.execute(
                "INSERT INTO intervals (state, started_at) VALUES (?, ?)",
                (state, resumed_at),
            )
            new_interval_id = int(cursor.lastrowid)
            self._set_last_seen(resumed_at)
        self._current_id = new_interval_id
        self._current_started_at = resumed_at

    def stop(self, at: Optional[float] = None) -> None:
        """Close the active interval before a normal application exit."""
        if self._current_id is None:
            return
        timestamp = self._clock() if at is None else at
        timestamp = self._clamp_active_timestamp(timestamp)
        with self._connection:
            self._connection.execute(
                "UPDATE intervals SET ended_at = ? WHERE id = ?",
                (timestamp, self._current_id),
            )
            self._set_last_seen(timestamp)
        self._current_id = None
        self._current_state = None
        self._current_started_at = None

    def elapsed_in_current_state(self, now: Optional[float] = None) -> float:
        timestamp = self._clock() if now is None else now
        if self._current_id is not None:
            timestamp = self._clamp_active_timestamp(timestamp)
        today = datetime.fromtimestamp(timestamp).date()
        day_start = self._local_midnight(today)
        if self._current_started_at is not None:
            return max(0.0, timestamp - max(self._current_started_at, day_start))
        last_seen = self._get_last_seen()
        if last_seen is not None:
            return max(0.0, timestamp - max(last_seen, day_start))
        return 0.0

    def record_drink(self, at: Optional[float] = None) -> None:
        """Persist one drinking event without changing the activity state."""
        timestamp = self._clock() if at is None else at
        with self._connection:
            self._connection.execute(
                "INSERT INTO drinks (drank_at) VALUES (?)",
                (timestamp,),
            )

    def last_drink_at(self) -> Optional[float]:
        """Return the most recent drinking event timestamp, if any."""
        row = self._connection.execute(
            "SELECT MAX(drank_at) AS value FROM drinks"
        ).fetchone()
        if row is None or row["value"] is None:
            return None
        return float(row["value"])

    def drink_count(self, day: Optional[date] = None) -> int:
        """Return the number of drinking events in a local calendar day."""
        selected_day = day or datetime.fromtimestamp(self._clock()).date()
        day_start = self._local_midnight(selected_day)
        day_end = self._local_midnight(selected_day + timedelta(days=1))
        row = self._connection.execute(
            """
            SELECT COUNT(*) AS value
            FROM drinks
            WHERE drank_at >= ? AND drank_at < ?
            """,
            (day_start, day_end),
        ).fetchone()
        return 0 if row is None else int(row["value"])

    def drink_half_hour_slots(self, day: date) -> tuple[int, ...]:
        """Return drink counts for the 48 local half-hour slots of a day."""
        return self._drink_time_slots(day, 30)

    def drink_quarter_hour_slots(self, day: date) -> tuple[int, ...]:
        """Return drink counts for the 96 local quarter-hour slots of a day."""
        return self._drink_time_slots(day, 15)

    def _drink_time_slots(self, day: date, slot_minutes: int) -> tuple[int, ...]:
        """Return drink counts grouped into local wall-clock slots."""
        day_start = self._local_midnight(day)
        day_end = self._local_midnight(day + timedelta(days=1))
        rows = self._connection.execute(
            """
            SELECT drank_at
            FROM drinks
            WHERE drank_at >= ? AND drank_at < ?
            ORDER BY drank_at
            """,
            (day_start, day_end),
        ).fetchall()
        slots_per_hour = 60 // slot_minutes
        slots = [0] * (24 * slots_per_hour)
        for row in rows:
            local_time = datetime.fromtimestamp(float(row["drank_at"]))
            slot = (
                local_time.hour * slots_per_hour
                + local_time.minute // slot_minutes
            )
            slots[slot] += 1
        return tuple(slots)

    def activity_half_hour_slots(
        self,
        day: date,
        now: Optional[float] = None,
    ) -> tuple[Optional[str], ...]:
        """Return the dominant activity in each local half-hour slot."""
        return self._activity_time_slots(day, 30, now)

    def activity_quarter_hour_slots(
        self,
        day: date,
        now: Optional[float] = None,
    ) -> tuple[Optional[str], ...]:
        """Return the dominant activity in each local quarter-hour slot."""
        return self._activity_time_slots(day, 15, now)

    def _activity_time_slots(
        self,
        day: date,
        slot_minutes: int,
        now: Optional[float],
    ) -> tuple[Optional[str], ...]:
        """Return dominant activities grouped into local wall-clock slots."""
        current_time = self._clock() if now is None else now
        if self._current_id is not None:
            current_time = self._clamp_active_timestamp(current_time)
        day_start = self._local_midnight(day)
        day_end = self._local_midnight(day + timedelta(days=1))
        rows = self._connection.execute(
            """
            SELECT state, started_at, COALESCE(ended_at, ?) AS effective_end
            FROM intervals
            WHERE started_at < ? AND COALESCE(ended_at, ?) > ?
            ORDER BY started_at
            """,
            (current_time, day_end, current_time, day_start),
        ).fetchall()
        intervals = [
            (row["state"], float(row["started_at"]), float(row["effective_end"]))
            for row in rows
        ]

        if self._current_id is None:
            last_seen = self._get_last_seen()
            if last_seen is not None and current_time > last_seen:
                intervals.append((APP_OFF, last_seen, current_time))

        slots_per_hour = 60 // slot_minutes
        slot_count = 24 * slots_per_hour
        slot_durations: List[Dict[str, float]] = [{} for _ in range(slot_count)]
        slot_latest_start: List[Dict[str, float]] = [{} for _ in range(slot_count)]
        segment_start = day_start
        while segment_start < day_end:
            segment_end = min(day_end, segment_start + slot_minutes * 60)
            local_time = datetime.fromtimestamp((segment_start + segment_end) / 2)
            slot_index = (
                local_time.hour * slots_per_hour
                + local_time.minute // slot_minutes
            )
            durations = slot_durations[slot_index]
            latest_start = slot_latest_start[slot_index]
            for state, started_at, ended_at in intervals:
                overlap_start = max(started_at, segment_start)
                overlap_end = min(ended_at, segment_end)
                if overlap_end <= overlap_start:
                    continue
                durations[state] = durations.get(state, 0.0) + (
                    overlap_end - overlap_start
                )
                latest_start[state] = max(
                    latest_start.get(state, overlap_start),
                    overlap_start,
                )
            segment_start = segment_end

        slots: List[Optional[str]] = []
        for durations, latest_start in zip(
            slot_durations,
            slot_latest_start,
        ):
            if not durations:
                slots.append(None)
                continue
            slots.append(
                max(
                    durations,
                    key=lambda state: (durations[state], latest_start[state]),
                )
            )
        return tuple(slots)

    def daily_totals(
        self,
        days: int = 7,
        end_date: Optional[date] = None,
        now: Optional[float] = None,
    ) -> List[Dict[str, object]]:
        """Return state durations for local calendar days, newest first."""
        if days < 1:
            raise ValueError("days must be positive")

        current_time = self._clock() if now is None else now
        if self._current_id is not None:
            current_time = self._clamp_active_timestamp(current_time)
        last_date = end_date or datetime.fromtimestamp(current_time).date()
        first_date = last_date - timedelta(days=days - 1)
        window_start = self._local_midnight(first_date)
        window_end = self._local_midnight(last_date + timedelta(days=1))

        rows = self._connection.execute(
            """
            SELECT state, started_at, COALESCE(ended_at, ?) AS effective_end
            FROM intervals
            WHERE started_at < ? AND COALESCE(ended_at, ?) > ?
            """,
            (current_time, window_end, current_time, window_start),
        ).fetchall()
        intervals = [
            (row["state"], float(row["started_at"]), float(row["effective_end"]))
            for row in rows
        ]

        if self._current_id is None:
            last_seen = self._get_last_seen()
            if last_seen is not None and current_time > last_seen:
                intervals.append((APP_OFF, last_seen, current_time))

        result: List[Dict[str, object]] = []
        for offset in range(days):
            day = last_date - timedelta(days=offset)
            day_start = self._local_midnight(day)
            day_end = self._local_midnight(day + timedelta(days=1))
            totals: Dict[str, float] = {state: 0.0 for state in ALL_STATES}
            for state, started_at, ended_at in intervals:
                overlap = max(
                    0.0,
                    min(ended_at, day_end) - max(started_at, day_start),
                )
                totals[state] += overlap
            result.append({"date": day, "totals": totals})
        return result

    @staticmethod
    def _local_midnight(day: date) -> float:
        local_midnight = datetime.combine(day, datetime_time.min).astimezone()
        return local_midnight.timestamp()

    def close(self) -> None:
        """Close the database connection without changing interval state."""
        try:
            self._connection.close()
        finally:
            self._release_instance_lock()
