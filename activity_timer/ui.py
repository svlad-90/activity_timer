"""Curses user interface for the activity timer."""

from __future__ import annotations

import curses
import locale
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from .storage import (
    ACTIVITY_STATES,
    APP_OFF,
    LYING,
    SEMI_SITTING,
    SITTING,
    STANDING,
    ActivityStore,
    InstanceAlreadyRunningError,
)


STATE_LABELS = {
    LYING: "ЛЕЖАНИЕ",
    SITTING: "СИДЕНИЕ",
    STANDING: "СТОЯНИЕ",
    SEMI_SITTING: "ПОЛУСИДЯ",
    APP_OFF: "ПРИЛОЖЕНИЕ НЕ ВКЛЮЧЕНО",
    None: "ВЫБЕРИТЕ СОСТОЯНИЕ",
}

STATE_KEY_CODES = {
    ord("1"): LYING,
    ord("2"): SEMI_SITTING,
    ord("3"): SITTING,
    ord("4"): STANDING,
}

TAB_KEY_CODE = 9
DRINK_KEY_CODE = ord("5")
ENTER_KEY_CODES = frozenset((10, 13, curses.KEY_ENTER))
ESCAPE_KEY_CODE = 27
SUSPEND_GAP_THRESHOLD_SECONDS = 5.0

WEEKDAY_LABELS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)

STATISTICS_ITEMS = (
    (LYING, "Лежание", "◆", 5),
    (SEMI_SITTING, "Полусидя", "◐", 2),
    (SITTING, "Сидение", "●", 6),
    (STANDING, "Стояние", "▲", 7),
    (APP_OFF, "Не включено", "○", 8),
)
TIMELINE_SYMBOLS = {
    state: symbol for state, _, symbol, _ in STATISTICS_ITEMS
}
TIMELINE_COLOR_PAIRS = {
    state: color_pair for state, _, _, color_pair in STATISTICS_ITEMS
}

IDLE_CAT = (
    "       .       .",
    "       \\`-\"'\"-'/",
    "        } 6 6 {",
    "       =.  Y  ,=",
    "     (\"\"-'***`-\"\")",
    r"      `-/     \-'",
    "  jgs (  )-(  )==='",
    "        \"\"   \"\"  отдыхаю...",
)

CAT_ART = {
    LYING: (
        "         /\\___/\\",
        "        /       \\",
        "       (  ;   ;  )",
        "     ==(    n    )==",
        "      .-\\_______/-.",
        "     /             \\",
        "    (  '~~~~~~~~~~~~)",
        "     `-------------'  грустно...",
    ),
    SITTING: (
        "        /\\___/\\",
        "       /       \\",
        "      (  o   o  )",
        "    ==(    w    )==   поиграем?",
        "       \\_______/",
        "       /       \\",
        "      /  |   |  \\    .-.",
        "     (   |   |   )  /   '",
        "      \\_(_)_(_)_/__/",
    ),
    STANDING: (
        "     .-.          /\\___/\\",
        "    (   )        /       \\",
        "     `-'        (  ^   ^  )",
        "       \\      ==(    o    )==",
        "        \\     (m)\\_______/(m)",
        "         \\       \\       /",
        "                  |     |",
        "                  | | | |",
        "                  (__|_|__)~~~'  играем!",
    ),
    SEMI_SITTING: (
        "        /\\___/\\",
        "       /       \\",
        "      (  o   o  )___",
        "    ==(    w    )   `-.",
        "       \\_______/       \\",
        "       /  |           __/",
        "      /___|________.-'",
        "        (_|_)  полусидя",
    ),
    APP_OFF: IDLE_CAT,
    None: IDLE_CAT,
}

COMPACT_CAT_ROWS = max(len(art) for art in CAT_ART.values())

LARGE_IDLE_CAT = (
    "       _.---.._             _.---...__",
    "    .-'   /\\   \\          .'  /\\     /",
    "    `.   (  )   \\        /   (  )   /",
    "      `.  \\/   .'\\      /`.   \\/  .'",
    "        ``---''   )    (   ``---''",
    "                .';.--.;`.",
    "              .' /_...._\\ `.",
    "            .'   `.a  a.'   `.",
    "           (        \\/        )",
    "            `.___..-'`-..___.'",
    "               \\          /",
    "                `-.____.-'  Felix Lee",
    "                       отдыхаю...",
)

# Hand-drawn ASCII art with the artists' signatures preserved. Sources and
# attribution are documented in README.md.
LARGE_CAT_ART = {
    LYING: (
        "              __..--''``---....___   _..._    __",
        "    /// //_.-'    .-/\";  `        ``<._  ``.''_ `. / // /",
        "   ///_.-' _..--.'_    \\                    `( ) ) // //",
        "   / (_..-' // (< _     ;_..__               ; `' / ///",
        "    / // // //  `-._,_)' // / ``--...____..-' /// / //",
        "                         Felix Lee",
        "                       грустно лежу...",
    ),
    SITTING: (
        "                  __ _..-.",
        "              _.-' _`.,'/",
        "          _,-'  ,-' _,`/_",
        "          `-.)(  -'.  <)",
        "            ,'\\> __,`'/ `",
        "      ___     /`.    (",
        "     / _ \\       )\\   \\",
        "     `' )/   _..' /    :",
        "      ,'/ ,''    (     |",
        "     / / /     .\\ \\    ;",
        "    / / (       `\\ \\  /\\",
        "    \\ `._)       )\\ \\/_/",
        r"     `.___._____/_)\_,)_) SSt",
        "                  ну поиграем?",
    ),
    STANDING: (
        "           _",
        "           \\`*-.",
        "            )  _`-.",
        "           .  : `. .",
        "           : _   '  \\",
        "           ; *` _.   `*-._",
        "           `-.-'          `-.",
        "             ;       `       `.",
        "             :.       .        \\",
        "             . \\  .   :   .-'   .",
        "             '  `+.;  ;  '      :",
        "             :  '  |    ;       ;-.",
        "             ; '   : :`-:     _.`* ;",
        "    [bug] .*' /  .*' ; .*`- +'  `*'",
        "          `*-*   `*-*  `*-*'",
        "                     гуляем!",
    ),
    SEMI_SITTING: (
        "     _",
        "    ( \\",
        "     \\ \\",
        "     / /                |\\\\",
        "    / /     .-`````-.   / ^`-.",
        "    \\ \\    /         \\_/  {|} `o",
        "     \\ \\  /   .---.   \\\\ _  ,--'",
        "      \\ \\/   /     \\,  \\( `^^^",
        "       \\   \\/\\      (\\  )",
        "        \\   ) \\     ) \\ \\",
        "    jgs  ) /__ \\__  ) (\\ \\___",
        "        (___)))__))(__))(__)))",
        "                 полусидя",
    ),
    APP_OFF: LARGE_IDLE_CAT,
    None: LARGE_IDLE_CAT,
}

LARGE_CAT_MIN_WIDTH = 90
LARGE_CAT_MIN_ROWS = max(len(art) for art in LARGE_CAT_ART.values())

CAT_COLOR_PAIRS = {
    LYING: 5,
    SITTING: 6,
    STANDING: 7,
    SEMI_SITTING: 2,
    APP_OFF: 8,
    None: 4,
}

STATE_PANEL_COLOR_PAIRS = {
    LYING: 5,
    SEMI_SITTING: 2,
    SITTING: 6,
    STANDING: 7,
}
_INITIALIZED_COLOR_PAIRS: set[int] = set()


def default_database_path() -> Path:
    configured_home = os.environ.get("XDG_DATA_HOME")
    data_home = (
        Path(configured_home)
        if configured_home
        else Path.home() / ".local" / "share"
    )
    return data_home / "activity-timer" / "activity.sqlite3"


def format_duration(seconds: float) -> str:
    whole_seconds = max(0, int(seconds))
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"


def _add_text(
    window: curses.window,
    row: int,
    column: int,
    text: str,
    style: int = 0,
) -> None:
    height, width = window.getmaxyx()
    if row < 0 or row >= height or column >= width:
        return
    clipped = text[: max(0, width - column - 1)]
    try:
        window.addstr(row, max(0, column), clipped, style)
    except curses.error:
        pass


def _add_centered(
    window: curses.window,
    row: int,
    text: str,
    style: int = 0,
) -> None:
    _, width = window.getmaxyx()
    _add_text(window, row, max(0, (width - len(text)) // 2), text, style)


def next_activity_state(current_state: Optional[str]) -> str:
    """Return the next state in the Enter-key carousel."""
    if current_state not in ACTIVITY_STATES:
        return ACTIVITY_STATES[0]
    current_index = ACTIVITY_STATES.index(current_state)
    return ACTIVITY_STATES[(current_index + 1) % len(ACTIVITY_STATES)]


def key_code(key: object) -> Optional[int]:
    """Normalize curses input to a layout-independent numeric code."""
    if isinstance(key, int):
        return key
    if isinstance(key, str) and len(key) == 1:
        return ord(key)
    return None


def suspend_gap_detected(
    wall_elapsed: float,
    monotonic_elapsed: float,
) -> bool:
    """Detect a long event-loop pause or a forward wall-clock jump."""
    return (
        monotonic_elapsed >= SUSPEND_GAP_THRESHOLD_SECONDS
        or wall_elapsed - monotonic_elapsed >= SUSPEND_GAP_THRESHOLD_SECONDS
    )


def progress_bar(value: float, total: float, width: int) -> str:
    """Render a proportional bar, keeping small non-zero values visible."""
    if width <= 0:
        return ""
    if total <= 0 or value <= 0:
        filled = 0
    else:
        filled = max(1, min(width, round(value / total * width)))
    return "█" * filled + "░" * (width - filled)


def drink_recency(last_drink_at: Optional[float], now: float) -> str:
    """Format how long ago the latest drinking event happened."""
    if last_drink_at is None:
        return "ещё не отмечено"
    return f"{format_duration(max(0.0, now - last_drink_at))} назад"


def drink_timeline_rows(
    slots: tuple[int, ...],
    hours_per_row: int,
) -> tuple[str, ...]:
    """Format hour labels and aligned drink-event cells."""
    if len(slots) not in (48, 96):
        raise ValueError("drink timeline requires 48 or 96 time slots")
    if hours_per_row < 1:
        raise ValueError("hours_per_row must be positive")

    slots_per_hour = len(slots) // 24
    rows = []
    for first_hour in range(0, 24, hours_per_row):
        last_hour = min(24, first_hour + hours_per_row)
        labels = []
        cells = []
        for hour in range(first_hour, last_hour):
            labels.append(f"{hour:02d}".center(slots_per_hour))
            cells.append(
                "".join(
                    "■" if slots[hour * slots_per_hour + slot] else "□"
                    for slot in range(slots_per_hour)
                )
            )
        rows.extend((" ".join(labels), " ".join(cells)))
    return tuple(rows)


def activity_timeline_rows(
    slots: tuple[Optional[str], ...],
    hours_per_row: int,
) -> tuple[str, ...]:
    """Format hour labels and dominant-state symbols for time slots."""
    if len(slots) not in (48, 96):
        raise ValueError("activity timeline requires 48 or 96 time slots")
    if hours_per_row < 1:
        raise ValueError("hours_per_row must be positive")

    slots_per_hour = len(slots) // 24
    rows = []
    for first_hour in range(0, 24, hours_per_row):
        last_hour = min(24, first_hour + hours_per_row)
        labels = []
        cells = []
        for hour in range(first_hour, last_hour):
            labels.append(f"{hour:02d}".center(slots_per_hour))
            cells.append(
                "".join(
                    TIMELINE_SYMBOLS.get(
                        slots[hour * slots_per_hour + slot],
                        "·",
                    )
                    for slot in range(slots_per_hour)
                )
            )
        rows.extend((" ".join(labels), " ".join(cells)))
    return tuple(rows)


def activity_zone_status(
    active: bool,
    value: float,
    total: float,
    width: int,
) -> str:
    """Format today's duration and bar for a horizontal state panel."""
    marker = "● " if active else "  "
    bar_width = max(0, width - len(marker) - 9)
    return f"{marker}{format_duration(value)} {progress_bar(value, total, bar_width)}"


def compact_activity_zone_text(
    key: str,
    label: str,
    active: bool,
    value: float,
    total: float,
    width: int,
) -> str:
    """Fit a state label, today's duration, and bar on one narrow panel row."""
    marker = "●" if active else " "
    duration = format_duration(value)
    base = f"{marker}{key} {label} {duration} "
    if len(base) > width:
        base = f"{marker}{key} {label} {duration[:5]} "
    bar_width = max(0, width - len(base))
    return (base + progress_bar(value, total, bar_width))[:width]


def percentage(value: float, total: float) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, round(value / total * 100)))


def statistics_summary_line(
    symbol: str,
    label: str,
    value: float,
    total: float,
    width: int,
    inline_bar: bool,
) -> str:
    """Format one statistics row with an optional compact inline bar."""
    share = percentage(value, total)
    text = f"{symbol}  {label:<12} {format_duration(value)}  {share:>3}%"
    if not inline_bar:
        return text
    bar_width = max(4, min(24, width - len(text) - 3))
    return f"{text} [{progress_bar(value, total, bar_width)}]"


def day_heading(day: date, today: date) -> str:
    if day == today:
        return "Сегодня"
    if day == today - timedelta(days=1):
        return "Вчера"
    return WEEKDAY_LABELS[day.weekday()]


def cat_art_for_size(
    state: Optional[str],
    width: int,
    available_rows: int,
) -> tuple[str, ...]:
    """Choose a large scene only when every state can fit without clipping."""
    large_art = LARGE_CAT_ART.get(state, LARGE_IDLE_CAT)
    if (
        width >= LARGE_CAT_MIN_WIDTH
        and available_rows >= LARGE_CAT_MIN_ROWS
        and max(map(len, large_art), default=0) <= width - 2
    ):
        return large_art
    return CAT_ART.get(state, IDLE_CAT)


def centered_art_column(art: tuple[str, ...], width: int) -> int:
    """Center an ASCII-art block while preserving its internal indentation."""
    block_width = max(map(len, art), default=0)
    return max(0, (width - block_width) // 2)


def cat_art_block_rows(
    state: Optional[str],
    art: tuple[str, ...],
) -> int:
    """Return the fixed region height for the selected responsive art tier."""
    large_art = LARGE_CAT_ART.get(state, LARGE_IDLE_CAT)
    return LARGE_CAT_MIN_ROWS if art is large_art else COMPACT_CAT_ROWS


def _zone_style(state: str, active: bool) -> int:
    color_pair = STATE_PANEL_COLOR_PAIRS[state]
    if not curses.has_colors() or color_pair not in _INITIALIZED_COLOR_PAIRS:
        return curses.A_REVERSE | curses.A_BOLD if active else curses.A_BOLD
    style = curses.color_pair(color_pair) | curses.A_BOLD
    return style | curses.A_REVERSE if active else style


def _status_style(state: Optional[str]) -> int:
    if not curses.has_colors():
        return curses.A_BOLD
    if state in ACTIVITY_STATES:
        return curses.color_pair(1) | curses.A_BOLD
    return curses.color_pair(3) | curses.A_BOLD


def _statistics_style(color_pair: int) -> int:
    if not curses.has_colors() or color_pair not in _INITIALIZED_COLOR_PAIRS:
        return curses.A_BOLD
    return curses.color_pair(color_pair) | curses.A_BOLD


def _draw_activity_timeline_row(
    window: curses.window,
    row: int,
    slots: tuple[Optional[str], ...],
    first_hour: int,
    hour_count: int,
) -> None:
    """Draw one centered activity row while preserving each state's color."""
    _, width = window.getmaxyx()
    slots_per_hour = len(slots) // 24
    line_width = hour_count * (slots_per_hour + 1) - 1
    column = max(0, (width - line_width) // 2)
    for hour in range(first_hour, first_hour + hour_count):
        for slot in range(slots_per_hour):
            state = slots[hour * slots_per_hour + slot]
            symbol = TIMELINE_SYMBOLS.get(state, "·")
            style = (
                curses.A_DIM
                if state is None
                else _statistics_style(TIMELINE_COLOR_PAIRS[state])
            )
            _add_text(window, row, column, symbol, style)
            column += 1
        if hour < first_hour + hour_count - 1:
            column += 1


def _draw_cat(
    window: curses.window,
    start_row: int,
    state: Optional[str],
    last_row: int,
) -> int:
    """Draw the cat pose for the current state without touching the footer."""
    _, width = window.getmaxyx()
    art = cat_art_for_size(state, width, max(0, last_row - start_row))
    block_rows = cat_art_block_rows(state, art)
    block_end_row = min(last_row, start_row + block_rows)
    visible_rows = max(0, block_end_row - start_row)
    style = _statistics_style(CAT_COLOR_PAIRS.get(state, 4))
    column = centered_art_column(art, width)
    row = start_row + max(0, (visible_rows - len(art)) // 2)
    for line in art:
        if row >= block_end_row:
            break
        _add_text(window, row, column, line, style)
        row += 1
    return block_end_row


def _draw_state_zones(
    window: curses.window,
    start_row: int,
    current_state: Optional[str],
    totals: dict[str, object],
    recorded_total: float,
) -> int:
    """Draw responsive state panels and return the first free row."""
    _, width = window.getmaxyx()
    state_keys = {LYING: "1", SEMI_SITTING: "2", SITTING: "3", STANDING: "4"}
    short_labels = {LYING: "Л", SEMI_SITTING: "ПС", SITTING: "С", STANDING: "СТ"}

    if width >= 68:
        gap = 2
        box_width = min(26, max(15, (width - 10) // 4))
        total_width = box_width * 4 + gap * 3
        start_column = max(0, (width - total_width) // 2)
        for index, state in enumerate(ACTIVITY_STATES):
            column = start_column + index * (box_width + gap)
            active = state == current_state
            style = _zone_style(state, active)
            inner_width = box_width - 2
            label = f"[{state_keys[state]}] {STATE_LABELS[state]}"
            status = activity_zone_status(
                active,
                float(totals[state]),
                recorded_total,
                inner_width,
            )
            _add_text(window, start_row, column, "┌" + "─" * inner_width + "┐", style)
            _add_text(
                window,
                start_row + 1,
                column,
                "│" + label.center(inner_width) + "│",
                style,
            )
            _add_text(
                window,
                start_row + 2,
                column,
                "│" + status.center(inner_width) + "│",
                style,
            )
            _add_text(window, start_row + 3, column, "└" + "─" * inner_width + "┘", style)
        return start_row + 5

    if width >= 36:
        gap = 2
        box_width = max(16, min(30, (width - gap - 2) // 2))
        total_width = box_width * 2 + gap
        start_column = max(0, (width - total_width) // 2)
        for index, state in enumerate(ACTIVITY_STATES):
            row = start_row + (index // 2) * 3
            column = start_column + (index % 2) * (box_width + gap)
            active = state == current_state
            style = _zone_style(state, active)
            inner_width = box_width - 2
            label = compact_activity_zone_text(
                state_keys[state],
                short_labels[state],
                active,
                float(totals[state]),
                recorded_total,
                inner_width,
            )
            _add_text(window, row, column, "┌" + "─" * inner_width + "┐", style)
            _add_text(
                window,
                row + 1,
                column,
                "│" + label.center(inner_width) + "│",
                style,
            )
            _add_text(window, row + 2, column, "└" + "─" * inner_width + "┘", style)
        return start_row + 7

    row = start_row
    line_width = max(1, width - 2)
    for state in ACTIVITY_STATES:
        active = state == current_state
        label = compact_activity_zone_text(
            state_keys[state],
            short_labels[state],
            active,
            float(totals[state]),
            recorded_total,
            line_width,
        )
        _add_centered(window, row, label, _zone_style(state, active))
        row += 1
    return row


def _draw_main(window: curses.window, store: ActivityStore, now: float) -> None:
    window.erase()
    height, width = window.getmaxyx()
    state = store.current_state
    footer_row = max(0, height - 3)
    today = store.daily_totals(days=1, now=now)[0]["totals"]
    assert isinstance(today, dict)
    recorded_today = sum(float(today[item]) for item, _, _, _ in STATISTICS_ITEMS)

    _add_centered(
        window,
        1,
        "ТАЙМЕР АКТИВНОСТИ  •  СЕГОДНЯ",
        curses.A_BOLD,
    )
    content_row = _draw_state_zones(
        window,
        3,
        state,
        today,
        recorded_today,
    )

    if state not in ACTIVITY_STATES:
        _add_centered(window, content_row, STATE_LABELS[state], _status_style(state))
        _add_centered(window, content_row + 1, "Enter — начать с «Лежания»")

    cat_gap = 1 if width < 68 and state in ACTIVITY_STATES else 3
    cat_start_row = content_row + cat_gap
    _draw_cat(window, cat_start_row, state, footer_row)

    drink_count = store.drink_count(datetime.fromtimestamp(now).date())
    drink_status = (
        f"[5] Попил   Сегодня: {drink_count}   "
        f"Последний раз: {drink_recency(store.last_drink_at(), now)}"
    )
    _add_centered(window, footer_row, drink_status, _statistics_style(2))
    _add_centered(
        window,
        footer_row + 1,
        "[Enter] Следующее состояние   [1/2/3/4] Прямой выбор",
    )
    _add_centered(window, footer_row + 2, "[Tab] Статистика   [Esc] Выход")
    window.refresh()


def _draw_statistics(
    window: curses.window,
    store: ActivityStore,
    now: float,
    day_offset: int,
) -> None:
    window.erase()
    height, width = window.getmaxyx()
    today = datetime.fromtimestamp(now).date()
    selected_date = today - timedelta(days=day_offset)
    day_data = store.daily_totals(days=1, end_date=selected_date, now=now)[0]
    totals = day_data["totals"]
    assert isinstance(totals, dict)
    recorded_time = sum(float(totals[state]) for state, _, _, _ in STATISTICS_ITEMS)

    detailed = width >= 56 and height >= 36
    title_row = 1 if detailed else 0
    date_row = 3 if detailed else 1
    total_row = 4 if detailed else 2
    start_row = 6 if detailed else 4

    _add_centered(
        window,
        title_row,
        "✦  СТАТИСТИКА ПО ДНЯМ  ✦",
        curses.A_BOLD,
    )
    _add_centered(
        window,
        date_row,
        f"{day_heading(selected_date, today)}  •  {selected_date:%d.%m.%Y}",
        curses.A_BOLD,
    )
    _add_centered(
        window,
        total_row,
        f"Учтено времени: {format_duration(recorded_time)}",
        curses.A_DIM,
    )

    bar_width = min(42, max(12, width - 32))
    for index, (state, label, symbol, color_pair) in enumerate(STATISTICS_ITEMS):
        value = float(totals[state])
        style = _statistics_style(color_pair)
        row = start_row + index * (3 if detailed else 1)
        _add_centered(
            window,
            row,
            statistics_summary_line(
                symbol,
                label,
                value,
                recorded_time,
                width,
                inline_bar=not detailed,
            ),
            style,
        )
        if detailed:
            _add_centered(
                window,
                row + 1,
                "[" + progress_bar(value, recorded_time, bar_width) + "]",
                style,
            )

    row_stride = 3 if detailed else 1
    summary_end_row = start_row + len(STATISTICS_ITEMS) * row_stride - 1
    activity_header_row = summary_end_row + 2
    if activity_header_row < height - 2:
        _add_centered(
            window,
            activity_header_row,
            "◈  СОСТОЯНИЯ  •  клетка 15 мин",
            curses.A_BOLD,
        )
    hours_per_row = 12 if width >= 61 else 6
    drink_rows = drink_timeline_rows(
        store.drink_quarter_hour_slots(selected_date),
        hours_per_row=hours_per_row,
    )
    activity_slots = store.activity_quarter_hour_slots(selected_date, now=now)
    activity_rows = activity_timeline_rows(activity_slots, hours_per_row)
    timeline_row = activity_header_row + 1
    for group_index in range(len(activity_rows) // 2):
        if timeline_row + 1 >= height - 2:
            break
        _add_centered(
            window,
            timeline_row,
            activity_rows[group_index * 2],
            curses.A_DIM,
        )
        first_hour = group_index * hours_per_row
        hour_count = min(hours_per_row, 24 - first_hour)
        _draw_activity_timeline_row(
            window, timeline_row + 1, activity_slots, first_hour, hour_count
        )
        timeline_row += 2

    event_header_row = timeline_row + 1
    if event_header_row < height - 2:
        _add_centered(
            window,
            event_header_row,
            (
                f"≈  СОБЫТИЯ: ВОДА  •  Попил: "
                f"{store.drink_count(selected_date)}  •  ■ отметка"
            ),
            _statistics_style(2),
        )
    timeline_row = event_header_row + 1
    for group_index in range(len(drink_rows) // 2):
        if timeline_row + 1 >= height - 2:
            break
        _add_centered(
            window,
            timeline_row,
            drink_rows[group_index * 2],
            curses.A_DIM,
        )
        _add_centered(
            window,
            timeline_row + 1,
            drink_rows[group_index * 2 + 1],
            _statistics_style(2),
        )
        timeline_row += 2

    note_row = timeline_row + 1
    if recorded_time <= 0 and note_row < height - 2:
        _add_centered(
            window,
            note_row,
            "За этот день пока нет записей активности",
            curses.A_DIM,
        )

    footer_row = max(0, height - 2)
    _add_centered(
        window,
        footer_row,
        "[←/↓] Раньше   [→/↑] Позже   [Tab/Esc] Назад",
    )
    window.refresh()


def _initialize_terminal(window: curses.window) -> None:
    """Configure optional terminal capabilities with safe fallbacks."""
    _INITIALIZED_COLOR_PAIRS.clear()
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    window.timeout(250)
    window.keypad(True)
    if not curses.has_colors():
        return
    try:
        curses.start_color()
    except curses.error:
        return
    background = curses.COLOR_BLACK
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        pass
    pair_specs = (
        (1, curses.COLOR_BLACK, curses.COLOR_GREEN),
        (2, curses.COLOR_CYAN, background),
        (3, curses.COLOR_YELLOW, background),
        (4, curses.COLOR_WHITE, background),
        (5, curses.COLOR_BLUE, background),
        (6, curses.COLOR_YELLOW, background),
        (7, curses.COLOR_GREEN, background),
        (8, curses.COLOR_MAGENTA, background),
    )
    color_pair_limit = getattr(curses, "COLOR_PAIRS", 0)
    for pair_number, foreground, pair_background in pair_specs:
        if pair_number >= color_pair_limit:
            continue
        try:
            curses.init_pair(pair_number, foreground, pair_background)
        except curses.error:
            continue
        _INITIALIZED_COLOR_PAIRS.add(pair_number)


def run_interface(window: curses.window, store: ActivityStore) -> None:
    _initialize_terminal(window)

    show_statistics = False
    statistics_offset = 0
    previous_wall_time = time.time()
    previous_monotonic_time = time.monotonic()
    last_heartbeat_monotonic = previous_monotonic_time
    while True:
        now = time.time()
        monotonic_now = time.monotonic()
        if suspend_gap_detected(
            now - previous_wall_time,
            monotonic_now - previous_monotonic_time,
        ):
            store.record_inactive_gap(now)
            last_heartbeat_monotonic = monotonic_now
        previous_wall_time = now
        previous_monotonic_time = monotonic_now

        if monotonic_now - last_heartbeat_monotonic >= 1.0:
            store.heartbeat(now)
            last_heartbeat_monotonic = monotonic_now

        if show_statistics:
            _draw_statistics(window, store, now, statistics_offset)
        else:
            _draw_main(window, store, now)

        try:
            key = window.get_wch()
        except curses.error:
            continue
        code = key_code(key)
        if code is None:
            continue

        if code in (curses.KEY_DOWN, curses.KEY_LEFT) and show_statistics:
            statistics_offset += 1
            continue
        if code in (curses.KEY_UP, curses.KEY_RIGHT) and show_statistics:
            statistics_offset = max(0, statistics_offset - 1)
            continue

        if code in ENTER_KEY_CODES:
            store.switch_state(next_activity_state(store.current_state))
            show_statistics = False
            continue

        if code == TAB_KEY_CODE:
            show_statistics = not show_statistics
            if show_statistics:
                statistics_offset = 0
            continue

        if code == DRINK_KEY_CODE:
            store.record_drink(now)
            show_statistics = False
            continue

        if code == ESCAPE_KEY_CODE:
            if show_statistics:
                show_statistics = False
            else:
                return
            continue

        state = STATE_KEY_CODES.get(code)
        if state is not None:
            store.switch_state(state)
            show_statistics = False


def main() -> None:
    locale.setlocale(locale.LC_ALL, "")
    try:
        store = ActivityStore(default_database_path())
    except InstanceAlreadyRunningError:
        print(
            "Таймер активности уже запущен в другом окне.",
            file=sys.stderr,
        )
        return
    try:
        curses.wrapper(run_interface, store)
    finally:
        store.stop()
        store.close()
