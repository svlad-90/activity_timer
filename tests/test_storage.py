from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from unittest.mock import patch

from activity_timer.storage import (
    APP_OFF,
    LYING,
    SEMI_SITTING,
    SITTING,
    STANDING,
    ActivityStore,
    InstanceAlreadyRunningError,
)


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class ActivityStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "activity.sqlite3"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _read_intervals(self) -> list[tuple[str, float, float]]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            return connection.execute(
                "SELECT state, started_at, ended_at FROM intervals ORDER BY id"
            ).fetchall()

    def test_first_state_does_not_invent_earlier_off_time(self) -> None:
        clock = MutableClock(100.0)
        store = ActivityStore(self.database_path, clock)
        store.switch_state(LYING)
        clock.value = 130.0
        store.stop()
        store.close()

        self.assertEqual(self._read_intervals(), [(LYING, 100.0, 130.0)])

    def test_second_store_cannot_recover_a_live_interval(self) -> None:
        first = ActivityStore(self.database_path, MutableClock(100.0))
        first.switch_state(LYING)

        with self.assertRaises(InstanceAlreadyRunningError):
            ActivityStore(self.database_path, MutableClock(110.0))

        first.stop(120.0)
        first.close()

        reopened = ActivityStore(self.database_path, MutableClock(130.0))
        reopened.close()
        self.assertEqual(self._read_intervals(), [(LYING, 100.0, 120.0)])

    def test_failed_switch_keeps_memory_and_database_on_previous_state(self) -> None:
        store = ActivityStore(self.database_path, MutableClock(100.0))
        store.switch_state(LYING)

        with patch.object(
            store,
            "_set_last_seen",
            side_effect=OSError("simulated write failure"),
        ):
            with self.assertRaises(OSError):
                store.switch_state(SITTING, 120.0)

        self.assertEqual(store.current_state, LYING)
        store.stop(130.0)
        store.close()
        self.assertEqual(self._read_intervals(), [(LYING, 100.0, 130.0)])

    def test_existing_database_is_migrated_for_semi_sitting(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                connection.executescript(
                    """
                CREATE TABLE intervals (
                    id INTEGER PRIMARY KEY,
                    state TEXT NOT NULL
                        CHECK (state IN ('lying', 'sitting', 'standing', 'app_off')),
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    CHECK (ended_at IS NULL OR ended_at >= started_at)
                );
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT INTO intervals (state, started_at, ended_at)
                VALUES ('lying', 10, 20);
                """
                )

        clock = MutableClock(30.0)
        store = ActivityStore(self.database_path, clock)
        store.switch_state(SEMI_SITTING)
        clock.value = 40.0
        store.stop()
        store.close()

        self.assertEqual(
            self._read_intervals(),
            [(LYING, 10.0, 20.0), (SEMI_SITTING, 30.0, 40.0)],
        )

    def test_elapsed_timer_restarts_at_local_midnight(self) -> None:
        first_day = date(2026, 10, 29)
        start = datetime.combine(
            first_day,
            datetime_time(hour=23, minute=50),
        ).astimezone().timestamp()
        after_midnight = datetime.combine(
            first_day + timedelta(days=1),
            datetime_time(hour=0, minute=10),
        ).astimezone().timestamp()
        clock = MutableClock(start)
        store = ActivityStore(self.database_path, clock)
        store.switch_state(SEMI_SITTING)

        self.assertEqual(store.elapsed_in_current_state(after_midnight), 10 * 60)
        store.stop(after_midnight)
        store.close()

    def test_drinking_events_persist_and_are_counted_per_day(self) -> None:
        first_day = date(2026, 10, 29)
        first_event = datetime.combine(
            first_day,
            datetime_time(hour=23, minute=50),
        ).astimezone().timestamp()
        second_day = first_day + timedelta(days=1)
        second_event = datetime.combine(
            second_day,
            datetime_time(hour=0, minute=10),
        ).astimezone().timestamp()
        last_event = datetime.combine(
            second_day,
            datetime_time(hour=8),
        ).astimezone().timestamp()

        clock = MutableClock(first_event)
        store = ActivityStore(self.database_path, clock)
        store.record_drink()
        store.record_drink(second_event)
        store.close()

        reopened = ActivityStore(self.database_path, MutableClock(last_event))
        reopened.record_drink()

        self.assertEqual(reopened.drink_count(first_day), 1)
        self.assertEqual(reopened.drink_count(second_day), 2)
        self.assertEqual(reopened.last_drink_at(), last_event)
        first_day_slots = reopened.drink_half_hour_slots(first_day)
        second_day_slots = reopened.drink_half_hour_slots(second_day)
        self.assertEqual(len(first_day_slots), 48)
        self.assertEqual(first_day_slots[47], 1)
        self.assertEqual(second_day_slots[0], 1)
        self.assertEqual(second_day_slots[16], 1)
        self.assertEqual(sum(second_day_slots), 2)
        first_day_quarters = reopened.drink_quarter_hour_slots(first_day)
        second_day_quarters = reopened.drink_quarter_hour_slots(second_day)
        self.assertEqual(len(first_day_quarters), 96)
        self.assertEqual(first_day_quarters[95], 1)
        self.assertEqual(second_day_quarters[0], 1)
        self.assertEqual(second_day_quarters[32], 1)
        reopened.close()

    def test_drinking_event_does_not_change_activity_state(self) -> None:
        clock = MutableClock(100.0)
        store = ActivityStore(self.database_path, clock)
        store.switch_state(SEMI_SITTING)
        clock.value = 110.0
        store.record_drink()

        self.assertEqual(store.current_state, SEMI_SITTING)
        self.assertEqual(store.last_drink_at(), 110.0)
        store.stop(120.0)
        store.close()

    def test_activity_timeline_uses_dominant_and_latest_state(self) -> None:
        selected_day = date(2026, 10, 29)

        def at(hour: int, minute: int) -> float:
            return datetime.combine(
                selected_day,
                datetime_time(hour=hour, minute=minute),
            ).astimezone().timestamp()

        clock = MutableClock(at(0, 0))
        store = ActivityStore(self.database_path, clock)
        store.switch_state(LYING)
        store.switch_state(SITTING, at(0, 20))
        store.switch_state(STANDING, at(0, 45))
        store.stop(at(1, 0))
        clock.value = at(1, 10)

        slots = store.activity_half_hour_slots(selected_day)
        quarter_slots = store.activity_quarter_hour_slots(selected_day)
        store.close()

        self.assertEqual(len(slots), 48)
        self.assertEqual(slots[0], LYING)
        self.assertEqual(slots[1], STANDING)
        self.assertEqual(slots[2], APP_OFF)
        self.assertIsNone(slots[3])

        self.assertEqual(len(quarter_slots), 96)
        self.assertEqual(quarter_slots[0], LYING)
        self.assertEqual(quarter_slots[1], SITTING)
        self.assertEqual(quarter_slots[2], SITTING)
        self.assertEqual(quarter_slots[3], STANDING)
        self.assertEqual(quarter_slots[4], APP_OFF)

    def test_state_switch_and_restart_record_off_interval(self) -> None:
        clock = MutableClock(100.0)
        store = ActivityStore(self.database_path, clock)
        store.switch_state(LYING)
        clock.value = 120.0
        store.switch_state(SITTING)
        clock.value = 150.0
        store.stop()
        store.close()

        clock.value = 200.0
        store = ActivityStore(self.database_path, clock)
        store.switch_state(STANDING)
        clock.value = 210.0
        store.stop()
        store.close()

        self.assertEqual(
            self._read_intervals(),
            [
                (LYING, 100.0, 120.0),
                (SITTING, 120.0, 150.0),
                (APP_OFF, 150.0, 200.0),
                (STANDING, 200.0, 210.0),
            ],
        )

    def test_crashed_interval_ends_at_last_heartbeat(self) -> None:
        clock = MutableClock(100.0)
        store = ActivityStore(self.database_path, clock)
        store.switch_state(LYING)
        clock.value = 112.0
        store.heartbeat()
        store.close()  # Simulate abrupt process termination.

        clock.value = 200.0
        recovered = ActivityStore(self.database_path, clock)
        recovered.switch_state(SITTING)
        clock.value = 205.0
        recovered.stop()
        recovered.close()

        self.assertEqual(
            self._read_intervals(),
            [
                (LYING, 100.0, 112.0),
                (APP_OFF, 112.0, 200.0),
                (SITTING, 200.0, 205.0),
            ],
        )

    def test_crash_recovery_keeps_heartbeat_after_clock_rollback(self) -> None:
        clock = MutableClock(100.0)
        store = ActivityStore(self.database_path, clock)
        store.switch_state(LYING)
        store.heartbeat(150.0)
        store.close()

        clock.value = 120.0
        recovered = ActivityStore(self.database_path, clock)
        recovered.switch_state(STANDING)
        recovered.stop(160.0)
        recovered.close()

        self.assertEqual(
            self._read_intervals(),
            [
                (LYING, 100.0, 150.0),
                (STANDING, 150.0, 160.0),
            ],
        )

    def test_clock_rollback_does_not_create_overlapping_intervals(self) -> None:
        clock = MutableClock(100.0)
        store = ActivityStore(self.database_path, clock)
        store.switch_state(LYING)
        clock.value = 150.0
        store.stop()
        store.close()

        clock.value = 120.0
        reopened = ActivityStore(self.database_path, clock)
        reopened.switch_state(STANDING)
        clock.value = 160.0
        reopened.stop()
        reopened.close()

        self.assertEqual(
            self._read_intervals(),
            [
                (LYING, 100.0, 150.0),
                (STANDING, 150.0, 160.0),
            ],
        )

    def test_clock_rollback_does_not_rewind_heartbeat_on_stop(self) -> None:
        clock = MutableClock(100.0)
        store = ActivityStore(self.database_path, clock)
        store.switch_state(LYING)
        store.heartbeat(150.0)
        store.heartbeat(120.0)
        store.stop(130.0)
        store.close()

        clock.value = 200.0
        reopened = ActivityStore(self.database_path, clock)
        reopened.switch_state(SITTING)
        reopened.stop(210.0)
        reopened.close()

        self.assertEqual(
            self._read_intervals(),
            [
                (LYING, 100.0, 150.0),
                (APP_OFF, 150.0, 200.0),
                (SITTING, 200.0, 210.0),
            ],
        )

    def test_clock_rollback_does_not_rewind_heartbeat_on_switch(self) -> None:
        store = ActivityStore(self.database_path, MutableClock(100.0))
        store.switch_state(LYING)
        store.heartbeat(150.0)
        store.switch_state(SITTING, 120.0)
        store.stop(160.0)
        store.close()

        self.assertEqual(
            self._read_intervals(),
            [
                (LYING, 100.0, 150.0),
                (SITTING, 150.0, 160.0),
            ],
        )

    def test_clock_rollback_does_not_shrink_active_read_totals(self) -> None:
        clock = MutableClock(100.0)
        store = ActivityStore(self.database_path, clock)
        store.switch_state(LYING)
        store.heartbeat(150.0)
        clock.value = 120.0

        selected_day = datetime.fromtimestamp(150.0).date()
        totals = store.daily_totals(
            days=1,
            end_date=selected_day,
            now=clock.value,
        )[0]["totals"]
        slots = store.activity_half_hour_slots(selected_day, now=clock.value)

        self.assertEqual(store.elapsed_in_current_state(clock.value), 50.0)
        self.assertEqual(totals[LYING], 50.0)
        self.assertIn(LYING, slots)
        store.stop(160.0)
        store.close()

    def test_daily_totals_split_interval_at_local_midnight(self) -> None:
        first_day = date.today() - timedelta(days=1)
        start = datetime.combine(first_day, datetime_time(23, 30)).astimezone().timestamp()
        midnight = datetime.combine(
            first_day + timedelta(days=1), datetime_time.min
        ).astimezone().timestamp()
        end = datetime.combine(
            first_day + timedelta(days=1), datetime_time(0, 30)
        ).astimezone().timestamp()

        clock = MutableClock(start)
        store = ActivityStore(self.database_path, clock)
        store.switch_state(STANDING)
        clock.value = end
        store.stop()

        totals = store.daily_totals(
            days=2,
            end_date=first_day + timedelta(days=1),
            now=end,
        )
        store.close()

        self.assertEqual(totals[0]["totals"][STANDING], end - midnight)
        self.assertEqual(totals[1]["totals"][STANDING], midnight - start)


if __name__ == "__main__":
    unittest.main()
