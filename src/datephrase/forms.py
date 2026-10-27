"""Forms dateparser does not read, built on dates it does: quarters, numbered weeks, a period up to today, weekends,
and a part of a period ("the end of the month", "the first week of September").

`read` returns (value, form name, parts filled by rule, counted from today?), or None when the text is not one of
these forms. A form counted from today ("next quarter", "this weekend") cannot be read as absolute. A part of a period asks the
caller back for the period itself (`read_period`), so dateparser still reads "next week" or "October"."""

import re
from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from .models import DateValue, PeriodValue

QUARTER = re.compile(r"(?:the )?(?:q([1-4])|([1-4])(?:st|nd|rd|th) quarter)(?:,? (?:of )?(\d{4}))?")
QUARTER_YEAR_FIRST = re.compile(r"(\d{4})[- ]?q([1-4])")
QUARTER_RELATIVE = re.compile(r"(last|previous|this|current|next) quarter")
WEEK_NUMBER = re.compile(r"(?:the )?(?:week|wk\.?) ?(\d{1,2})(?:,? (?:of )?(\d{4}))?|(\d{4})-?w(\d{1,2})")
TO_DATE = re.compile(r"(year|quarter|month|week)[ -]to[ -]date|([yqmw])td")
WEEKEND = re.compile(r"(?:(last|previous|this) )?weekend|the weekend")
DAY_OF = re.compile(r"(?:the )?(\d{1,3}(?:st|nd|rd|th)|first|last)(?: day)? of,? (.+)")  # "the 15th of last month"
PART = re.compile(r"(?:the )?(end|beginning|start|middle|mid)(?:-| of | )(.+)")
NTH_WEEK = re.compile(r"(?:the )?(1st|2nd|3rd|4th|5th|last) week of (.+)")
# "the end of the month": the month meant is the current one
CURRENT = {f"{article}{unit}": f"this {unit}" for unit in ("week", "month", "quarter", "year") for article in ("", "the ")}
UNITS_OF = {"y": "year", "q": "quarter", "m": "month", "w": "week"}
WEEK_START_DAY = {"monday": 0, "sunday": 6}


class PeriodNotRead(Exception):
    """A form was recognised, but the period inside it was not read: the whole phrase is refused, for that reason."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def read(text, *, ref: date, direction: str | None, week_start, read_period):
    """`week_start()` returns the session's week start (recording its default); `read_period(text)` reads a period, or
    raises PeriodNotRead: a recognised form is final, never handed on to another rule."""
    t = text.lower()
    if m := QUARTER.fullmatch(t):
        return _quarter(int(m[1] or m[2]), int(m[3]) if m[3] else None, ref, direction), "quarter", _year_filled(m[3]), False
    if m := QUARTER_YEAR_FIRST.fullmatch(t):
        return _quarter(int(m[2]), int(m[1]), ref, direction), "quarter", (), False
    if m := QUARTER_RELATIVE.fullmatch(t):
        offset = {"last": -1, "previous": -1, "this": 0, "current": 0, "next": 1}[m[1]]
        start = _quarter_start(ref) + relativedelta(months=3 * offset)
        return PeriodValue(start, start + relativedelta(months=3), "quarter"), "quarter", (), True
    if m := WEEK_NUMBER.fullmatch(t):
        week, year = (int(m[1]), m[2]) if m[1] else (int(m[4]), m[3])
        return _iso_week(week, int(year) if year else None, ref, direction), "week_number", _year_filled(year), False
    if m := TO_DATE.fullmatch(t):
        unit = m[1] or UNITS_OF[m[2]]
        start = _start_of(unit, ref, week_start)
        return PeriodValue(start, ref + timedelta(days=1), f"{unit}_to_date"), "to_date", (), True  # today included
    if m := WEEKEND.fullmatch(t):
        return _weekend(m[1] or ("last" if direction == "past" else "this"), ref), "weekend", (), True
    if m := NTH_WEEK.fullmatch(t):
        p = read_period(CURRENT.get(m[2], m[2]))
        start = p.end - timedelta(days=7) if m[1] == "last" else p.start + timedelta(days=7 * (int(m[1][0]) - 1))
        if start < p.start or start >= p.end:
            raise ValueError(f"{m[1]} week of a {p.grain} does not exist")
        return PeriodValue(start, min(start + timedelta(days=7), p.end), "week"), "part_of_period", (), False
    if m := DAY_OF.fullmatch(t):  # "first day of next month", "the last day of the year"
        p = read_period(CURRENT.get(m[2], m[2]))
        nth = 1 if m[1] == "first" else None if m[1] == "last" else int(m[1][:-2])
        d = p.end - timedelta(days=1) if nth is None else p.start + timedelta(days=nth - 1)
        if not p.start <= d < p.end:
            raise ValueError(f"day {nth} of a {p.grain} does not exist")
        return DateValue(d, _midnight(d)), "part_of_period", (), False
    if m := PART.fullmatch(t):
        p = read_period(CURRENT.get(m[2], m[2]))
        if m[1] == "end":
            return DateValue(p.end - timedelta(days=1), _midnight(p.end - timedelta(days=1))), "part_of_period", (), False
        if m[1] in ("beginning", "start"):
            return DateValue(p.start, _midnight(p.start)), "part_of_period", (), False
        middle = p.start.replace(day=15) if p.grain == "month" else p.start + timedelta(days=(p.end - p.start).days // 2)
        return DateValue(middle, _midnight(middle)), "part_of_period", (), False
    return None


def _midnight(d: date) -> datetime:
    return datetime.combine(d, time())


def _quarter_start(d: date) -> date:
    return date(d.year, (d.month - 1) // 3 * 3 + 1, 1)


def _quarter(q: int, year: int | None, ref: date, direction: str | None) -> PeriodValue:
    """Quarter q of `year`; without a year, the most recent one when the label says past, else the next one."""
    def start_in(y):
        return date(y, 3 * q - 2, 1)
    if year is None:
        year = _year_by_direction(start_in, ref, direction)
    start = start_in(year)
    return PeriodValue(start, start + relativedelta(months=3), "quarter")


def _iso_week(week: int, year: int | None, ref: date, direction: str | None) -> PeriodValue:
    """ISO week (Monday start, as numbered weeks always are); a year without it raises ValueError."""
    def start_in(y):
        return date.fromisocalendar(y, week, 1)
    if year is None:
        year = _year_by_direction(start_in, ref, direction)
    start = start_in(year)
    return PeriodValue(start, start + timedelta(days=7), "week")


def _year_filled(year) -> tuple[str, ...]:
    return () if year else ("year",)


def _year_by_direction(start_in, ref: date, direction: str | None) -> int:
    """Past: the latest year whose period has started by ref. Otherwise the next occurrence: it starts after ref.
    Years without the period (week 53) are skipped: the latest past week 53 before 2026-10-27 is 2020's."""
    past = direction == "past"
    for year in range(ref.year, ref.year - 8, -1) if past else range(ref.year, ref.year + 8):
        try:
            start = start_in(year)
        except ValueError:
            continue
        if (start <= ref) if past else (start > ref):
            return year
    raise ValueError("no such period within 7 years of the reference date")


def _start_of(unit: str, ref: date, week_start) -> date:
    if unit == "year":
        return date(ref.year, 1, 1)
    if unit == "quarter":
        return _quarter_start(ref)
    if unit == "month":
        return ref.replace(day=1)
    return ref - timedelta(days=(ref.weekday() - WEEK_START_DAY[week_start()]) % 7)


def _weekend(which: str, ref: date) -> PeriodValue:
    """Saturday and Sunday. "this weekend" is the current one on a weekend day, else the coming one; "last weekend"
    the one before it."""
    saturday = ref - timedelta(days=(ref.weekday() - 5) % 7)  # the latest Saturday, ref included
    if ref.weekday() < 5:
        saturday += timedelta(days=7)  # a weekday: this weekend is the coming one
    if which in ("last", "previous"):
        saturday -= timedelta(days=7)
    return PeriodValue(saturday, saturday + timedelta(days=2), "weekend")
