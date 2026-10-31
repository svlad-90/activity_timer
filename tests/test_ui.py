import curses
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from activity_timer.storage import APP_OFF, LYING, SEMI_SITTING, SITTING, STANDING
from activity_timer.ui import (
    CAT_ART,
    COMPACT_CAT_ROWS,
    DRINK_KEY_CODE,
    ENTER_KEY_CODES,
    ESCAPE_KEY_CODE,
    IDLE_CAT,
    LARGE_IDLE_CAT,
    STATE_KEY_CODES,
    TAB_KEY_CODE,
    LARGE_CAT_ART,
    LARGE_CAT_MIN_ROWS,
    STATE_PANEL_COLOR_PAIRS,
    STATISTICS_ITEMS,
    _initialize_terminal,
    _draw_state_zones,
    activity_timeline_rows,
    activity_zone_status,
    cat_art_for_size,
    cat_art_block_rows,
    centered_art_column,
    compact_activity_zone_text,
    day_heading,
    default_database_path,
    drink_recency,
    drink_timeline_rows,
    key_code,
    next_activity_state,
    percentage,
    progress_bar,
    statistics_summary_line,
)


class ActivityCarouselTest(unittest.TestCase):
    def test_optional_terminal_capabilities_have_safe_fallbacks(self) -> None:
        window = MagicMock()
        with patch("activity_timer.ui.curses.curs_set", side_effect=curses.error):
            with patch("activity_timer.ui.curses.has_colors", return_value=True):
                with patch("activity_timer.ui.curses.start_color"):
                    with patch(
                        "activity_timer.ui.curses.use_default_colors",
                        side_effect=curses.error,
                    ):
                        with patch(
                            "activity_timer.ui.curses.COLOR_PAIRS",
                            8,
                            create=True,
                        ):
                            with patch(
                                "activity_timer.ui.curses.init_pair"
                            ) as init_pair:
                                _initialize_terminal(window)

        self.assertIn(
            call(2, curses.COLOR_CYAN, curses.COLOR_BLACK),
            init_pair.call_args_list,
        )
        self.assertNotIn(
            call(8, curses.COLOR_MAGENTA, curses.COLOR_BLACK),
            init_pair.call_args_list,
        )
        window.timeout.assert_called_once_with(250)
        window.keypad.assert_called_once_with(True)

    def test_narrow_state_zones_leave_room_for_compact_cat(self) -> None:
        window = MagicMock()
        window.getmaxyx.return_value = (24, 60)
        totals = {state: 1.0 for state in STATE_PANEL_COLOR_PAIRS}

        with patch("activity_timer.ui.curses.has_colors", return_value=False):
            first_free_row = _draw_state_zones(
                window,
                3,
                LYING,
                totals,
                4.0,
            )

        self.assertEqual(first_free_row, 10)
        self.assertGreaterEqual(21 - (first_free_row + 1), 9)

    def test_empty_xdg_data_home_uses_standard_fallback(self) -> None:
        with patch.dict("os.environ", {"XDG_DATA_HOME": ""}):
            with patch(
                "activity_timer.ui.Path.home",
                return_value=Path("/home/tester"),
            ):
                database_path = default_database_path()

        self.assertEqual(
            database_path,
            Path("/home/tester/.local/share/activity-timer/activity.sqlite3"),
        )

    def test_state_panels_reuse_their_statistics_colors(self) -> None:
        statistics_colors = {
            state: color_pair
            for state, _, _, color_pair in STATISTICS_ITEMS
            if state in STATE_PANEL_COLOR_PAIRS
        }
        self.assertEqual(STATE_PANEL_COLOR_PAIRS, statistics_colors)
        self.assertEqual(len(set(STATE_PANEL_COLOR_PAIRS.values())), 4)

    def test_state_panels_show_daily_time_and_proportional_bars(self) -> None:
        self.assertEqual(
            activity_zone_status(True, 3723, 7200, 20),
            "● 01:02:03 █████░░░░",
        )
        self.assertEqual(
            activity_zone_status(False, 0, 7200, 20),
            "  00:00:00 ░░░░░░░░░",
        )
        self.assertEqual(
            compact_activity_zone_text("2", "ПС", True, 3723, 7200, 24),
            "●2 ПС 01:02:03 █████░░░░",
        )

    def test_enter_selects_lying_when_no_activity_is_selected(self) -> None:
        self.assertEqual(next_activity_state(None), LYING)
        self.assertEqual(next_activity_state(APP_OFF), LYING)

    def test_enter_cycles_through_all_activity_states(self) -> None:
        self.assertEqual(next_activity_state(LYING), SEMI_SITTING)
        self.assertEqual(next_activity_state(SEMI_SITTING), SITTING)
        self.assertEqual(next_activity_state(SITTING), STANDING)
        self.assertEqual(next_activity_state(STANDING), LYING)

    def test_action_keys_use_layout_independent_numeric_codes(self) -> None:
        self.assertIn(key_code("\n"), ENTER_KEY_CODES)
        self.assertEqual(key_code("\t"), TAB_KEY_CODE)
        self.assertEqual(key_code("\x1b"), ESCAPE_KEY_CODE)
        self.assertEqual(key_code("5"), DRINK_KEY_CODE)
        self.assertEqual(STATE_KEY_CODES[key_code("1")], LYING)
        self.assertEqual(STATE_KEY_CODES[key_code("2")], SEMI_SITTING)
        self.assertEqual(STATE_KEY_CODES[key_code("3")], SITTING)
        self.assertEqual(STATE_KEY_CODES[key_code("4")], STANDING)
        self.assertNotIn(key_code("q"), STATE_KEY_CODES)
        self.assertNotIn(key_code("й"), STATE_KEY_CODES)

    def test_statistics_progress_bar_keeps_small_values_visible(self) -> None:
        self.assertEqual(progress_bar(0, 100, 8), "░░░░░░░░")
        self.assertEqual(progress_bar(25, 100, 8), "██░░░░░░")
        self.assertEqual(progress_bar(0.1, 100, 8), "█░░░░░░░")
        self.assertEqual(percentage(25, 100), 25)
        self.assertEqual(percentage(1, 0), 0)

        compact_line = statistics_summary_line(
            "◆",
            "Лежание",
            25,
            100,
            60,
            inline_bar=True,
        )
        self.assertIn("25% [", compact_line)
        self.assertIn("█", compact_line)
        self.assertIn("░", compact_line)
        self.assertLessEqual(len(compact_line), 60)

    def test_drink_recency_handles_missing_and_recorded_events(self) -> None:
        self.assertEqual(drink_recency(None, 100), "ещё не отмечено")
        self.assertEqual(drink_recency(40, 100), "00:01:00 назад")

    def test_drink_timeline_groups_two_half_hours_under_each_hour(self) -> None:
        slots = [0] * 48
        slots[0] = 1
        slots[2] = 2
        slots[47] = 1

        rows = drink_timeline_rows(tuple(slots), hours_per_row=12)

        self.assertEqual(len(rows), 4)
        self.assertTrue(rows[0].startswith("00 01 02"))
        self.assertTrue(rows[1].startswith("■□ ■□ □□"))
        self.assertTrue(rows[2].endswith("22 23"))
        self.assertTrue(rows[3].endswith("□□ □■"))

    def test_activity_timeline_uses_state_symbols_and_empty_dots(self) -> None:
        slots = [None] * 48
        slots[0] = LYING
        slots[1] = SEMI_SITTING
        slots[2] = SITTING
        slots[3] = STANDING
        slots[47] = APP_OFF

        rows = activity_timeline_rows(tuple(slots), hours_per_row=12)

        self.assertEqual(len(rows), 4)
        self.assertTrue(rows[1].startswith("◆◐ ●▲ ··"))
        self.assertTrue(rows[3].endswith("·· ·○"))

    def test_quarter_hour_timelines_align_four_cells_under_each_hour(self) -> None:
        activities = [None] * 96
        activities[0:4] = [LYING, SEMI_SITTING, SITTING, STANDING]
        drinks = [0] * 96
        drinks[0] = 1
        drinks[3] = 1

        activity_rows = activity_timeline_rows(
            tuple(activities),
            hours_per_row=12,
        )
        drink_rows = drink_timeline_rows(tuple(drinks), hours_per_row=12)

        self.assertTrue(activity_rows[0].startswith(" 00   01 "))
        self.assertTrue(activity_rows[1].startswith("◆◐●▲ ····"))
        self.assertTrue(drink_rows[1].startswith("■□□■ □□□□"))

    def test_statistics_day_heading_is_human_readable(self) -> None:
        today = date(2026, 10, 29)
        self.assertEqual(day_heading(today, today), "Сегодня")
        self.assertEqual(day_heading(today - timedelta(days=1), today), "Вчера")
        self.assertEqual(day_heading(date(2026, 10, 26), today), "Понедельник")

    def test_cat_poses_are_distinct_and_fit_a_small_terminal(self) -> None:
        poses = [CAT_ART[state] for state in (LYING, SITTING, STANDING, SEMI_SITTING)]
        self.assertEqual(len(set(poses)), 4)
        for pose in poses:
            self.assertGreaterEqual(len(pose), 8)
            self.assertLessEqual(len(pose), 9)
            self.assertLessEqual(max(map(len, pose)), 48)
        self.assertTrue(any("jgs" in line for line in IDLE_CAT))

    def test_large_cat_scenes_are_distinct_and_selected_responsively(self) -> None:
        poses = [
            LARGE_CAT_ART[state]
            for state in (LYING, SITTING, STANDING, SEMI_SITTING)
        ]
        self.assertEqual(len(set(poses)), 4)
        for pose in poses:
            self.assertGreaterEqual(len(pose), 7)
            self.assertLessEqual(len(pose), 16)
            self.assertLessEqual(max(map(len, pose)), 70)

        self.assertTrue(any("Felix Lee" in line for line in LARGE_CAT_ART[LYING]))
        self.assertTrue(any("SSt" in line for line in LARGE_CAT_ART[SITTING]))
        self.assertTrue(any("[bug]" in line for line in LARGE_CAT_ART[STANDING]))
        self.assertTrue(any("jgs" in line for line in LARGE_CAT_ART[SEMI_SITTING]))
        self.assertTrue(any("Felix Lee" in line for line in LARGE_IDLE_CAT))

        self.assertIs(cat_art_for_size(LYING, 100, LARGE_CAT_MIN_ROWS), LARGE_CAT_ART[LYING])
        self.assertIs(cat_art_for_size(LYING, 80, LARGE_CAT_MIN_ROWS), CAT_ART[LYING])
        self.assertIs(cat_art_for_size(LYING, 100, 10), CAT_ART[LYING])

    def test_cat_art_is_centered_as_one_block(self) -> None:
        art = ("    head", "body")
        self.assertEqual(centered_art_column(art, 20), 6)
        self.assertEqual(centered_art_column(art, 4), 0)

    def test_cat_art_tier_reserves_a_fixed_height(self) -> None:
        self.assertEqual(
            cat_art_block_rows(LYING, LARGE_CAT_ART[LYING]),
            LARGE_CAT_MIN_ROWS,
        )
        self.assertEqual(
            cat_art_block_rows(STANDING, LARGE_CAT_ART[STANDING]),
            LARGE_CAT_MIN_ROWS,
        )
        self.assertEqual(
            cat_art_block_rows(LYING, CAT_ART[LYING]),
            COMPACT_CAT_ROWS,
        )


if __name__ == "__main__":
    unittest.main()
