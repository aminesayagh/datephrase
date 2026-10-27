"""The GPT-6.1-Sol audit of the v0.2.0 fixes (2026-10-27). Each test covers the whole class of input a fix is about,
not only the phrase the audit reported. Reference: Tuesday 2026-10-27."""

from datetime import date

import pytest

from datephrase import TemporalContext, read_temporal

NOW = TemporalContext(reference_date=date(2026, 10, 27))


def show(r):
    v = r.value
    if v is None:
        return r.status
    if v.kind == "period":
        return f"{v.start}..{v.end}"
    if v.kind == "duration":
        return f"{v.quantity} {v.unit}"
    return str(v.date)


# The length limit is measured on the text as given, before any rewrite or form, and nothing crashes.
LABELS = [{"kind": "duration"}, {"kind": "date", "anchor": "relative"}, {"kind": "date", "anchor": "absolute"},
          {"kind": "period", "anchor": "relative"}, {}]


@pytest.mark.parametrize("text", [
    "9" * 5000 + " fortnights", "9" * 5000 + " days", "x" * 513, "first week of " * 40 + "October",
    "last week of " * 350 + "October",
])
@pytest.mark.parametrize("labels", LABELS)
def test_too_long_is_refused_before_any_rewrite(text, labels):
    r = read_temporal(text, context=NOW, **labels)
    assert (r.status, r.reason) == ("not_temporal", "TOO_LONG")


@pytest.mark.parametrize("text", [
    "9" * 500 + " fortnights", "9" * 400 + " days", "9" * 300 + " years ago", "9" * 400 + ".5 qtrs",
    "9" * 400 + ".25 fortnights", "9" * 450 + " weeks",
])
@pytest.mark.parametrize("labels", LABELS)
def test_numbers_too_big_for_a_calendar_are_not_a_crash(text, labels):
    assert read_temporal(text, context=NOW, **labels).status == "not_temporal"


# A duration is a length read back from a base: a moment ahead of it is a date, whatever digits it holds.
@pytest.mark.parametrize("text", [
    "tomorrow at 1:00", "next week at 9am", "today", "in 3 days", "this month", "yesterday at 12:00",
    "today at 0:00", "3 days and", "the 3 days", "May 2026", "2026-10-26",
])
def test_a_moment_is_not_a_length(text):
    r = read_temporal(text, context=NOW, kind="duration")
    assert (r.status, r.reason) == ("not_temporal", "KIND_MISMATCH")


@pytest.mark.parametrize("text, expected", [
    ("3 days", "3 day"), ("0 days", "0 day"), ("3 days ago", "3 day"), ("1 year 2 months", "14 month"),
    ("three weeks and two days", "23 day"), ("2 hours ago", "2 hour"), ("1 year, 2 months", "14 month"),
])
def test_lengths_still_read(text, expected):
    assert show(read_temporal(text, context=NOW, kind="duration")) == expected


# Fractional months, quarters and years are refused in every spelling dateparser would misread.
@pytest.mark.parametrize("text", [
    ".5 years", ".5 months", "0.5 years", "2.25 quarters", "1.5 yr", "1.5-year", "1.5 mos", "1.5yrs", "3.5 qtrs",
    ".5y", "1.5y", "2.5 Years", "1.5\tmonths",
])
@pytest.mark.parametrize("labels", [{"kind": "duration"}, {"kind": "date", "anchor": "relative"}])
def test_fractional_months_and_years_in_any_spelling(text, labels):
    phrase = text if labels["kind"] == "duration" else f"in {text}"
    r = read_temporal(phrase, context=NOW, **labels)
    assert (r.status, r.reason) == ("not_temporal", "FRACTION_NOT_SUPPORTED")


def test_fractional_days_and_weeks_still_read():
    assert [show(read_temporal(t, kind="duration")) for t in ("1.5 days", ".5 days", "1.5 weeks")] == [
        "1.5 day", "0.5 day", "1.5 week"]


@pytest.mark.parametrize("text", ["01 02 2026", "1,2,2026", "01.02.2026", "1 2 2026"])
def test_a_real_order_ambiguity_is_asked_whatever_the_separator(text):
    assert read_temporal(text, context=NOW).question.reason == "DATE_ORDER_AMBIGUOUS"


# Every form counted from today is refused under an absolute label; forms that name their period are not.
@pytest.mark.parametrize("text", ["next quarter", "this quarter", "last quarter", "last weekend", "this weekend",
                                  "year to date", "month to date", "YTD"])
def test_forms_counted_from_today_are_not_absolute(text):
    r = read_temporal(text, context=NOW, kind="period", anchor="absolute")
    assert (r.status, r.reason) == ("not_temporal", "ANCHOR_MISMATCH")


@pytest.mark.parametrize("text, expected", [
    ("Q3 2026", "2026-07-01..2026-10-01"), ("2026-W43", "2026-10-19..2026-10-26"), ("Q4", "2027-10-01..2028-01-01"),
])
def test_forms_that_name_their_period_stay_absolute(text, expected):
    assert show(read_temporal(text, context=NOW, kind="period", anchor="absolute")) == expected


# A lone ordinal day is that day at its next occurrence (its latest when past), on any day of any month.
def expected_day(n, ref, past):
    """Independent of the library: walk the calendar from ref, one day at a time."""
    from datetime import timedelta
    step = timedelta(days=-1 if past else 1)
    d = ref if past else ref + step
    while d.day != n:
        d += step
    return d


def ordinal(n):
    return f"{n}{'th' if 11 <= n <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


@pytest.mark.parametrize("n", range(1, 32))
@pytest.mark.parametrize("ref", [date(2026, 10, 27), date(2027, 2, 28), date(2028, 2, 29), date(2026, 12, 31),
                                 date(2026, 1, 1), date(2026, 4, 30)])
@pytest.mark.parametrize("labels, past", [({"kind": "date", "anchor": "absolute"}, False),
                                          ({"kind": "date", "anchor": "relative"}, False),
                                          ({"kind": "date", "anchor": "relative", "direction": "past"}, True),
                                          ({}, False)])
def test_a_lone_ordinal_is_a_day_never_a_month(n, ref, labels, past):
    r = read_temporal(f"the {ordinal(n)}", context=TemporalContext(reference_date=ref), **labels)
    assert r.value.date == expected_day(n, ref, past)


@pytest.mark.parametrize("text, expected", [("the first of 2027", "2027-01-01"),
                                            ("the first of March 2027", "2027-03-01")])
def test_an_ordinal_with_its_year_or_month_is_not_alone(text, expected):
    assert show(read_temporal(text, context=NOW, kind="date", anchor="absolute")) == expected


def test_a_lone_day_is_not_a_period():
    r = read_temporal("the first", context=NOW, kind="period", anchor="absolute")
    assert (r.status, r.reason) == ("not_temporal", "KIND_MISMATCH")


def test_a_lone_ordinal_in_the_past_is_the_latest_one():
    r = read_temporal("the first", context=NOW, kind="date", anchor="relative", direction="past")
    assert (show(r), r.provenance.filled) == ("2026-10-01", ("year", "month"))


# An ordinal followed by a unit counts ("the second day from now"), unless it is the day of a period.
@pytest.mark.parametrize("text, expected", [
    ("the second day from now", "2026-10-29"), ("the third day from now", "2026-10-30"),
    ("the second day of the month", "2026-10-02"), ("the fifth day of next month", "2026-11-05"),
    ("the tenth day of October 2026", "2026-10-10"), ("the last day of next month", "2026-11-30"),
])
def test_an_ordinal_count_is_not_a_misread_day(text, expected):
    assert show(read_temporal(text, context=NOW, kind="date", anchor="relative")) == expected


# "second": the unit only right after a count; anywhere else an ordinal, whatever comes around it.
@pytest.mark.parametrize("text", [
    "second May 2026", "May, second 2026", "May second, 2026", "the second of May 2026", "2nd May 2026",
    "Second May 2026",
])
def test_second_as_an_ordinal(text):
    assert show(read_temporal(text, context=NOW, kind="date", anchor="absolute")) == "2026-05-02"


@pytest.mark.parametrize("tens", ["twenty", "thirty"])
@pytest.mark.parametrize("joint", [" ", "-"])
def test_a_compound_ordinal_ending_in_second(tens, joint):
    day = {"twenty": 22, "thirty": 32}[tens]
    r = read_temporal(f"October {tens}{joint}second 2026", context=NOW, kind="date", anchor="absolute")
    assert show(r) == (f"2026-10-{day}" if day <= 31 else "not_temporal")


def test_a_count_of_seconds_in_a_date_is_not_a_day():
    r = read_temporal("two second", context=NOW, kind="date", anchor="absolute")
    assert r.status == "not_temporal"


@pytest.mark.parametrize("text, expected", [
    ("one second", "1 second"), ("1 second", "1 second"), ("a second", "1 second"), ("5 seconds", "5 second"),
    ("two second", "2 second"), ("twenty two second", "22 second"), ("twenty two seconds", "22 second"),
    ("thirty seconds", "30 second"),
])
def test_second_as_a_unit(text, expected):
    assert show(read_temporal(text, kind="duration")) == expected


# Spacing never changes the reading.
@pytest.mark.parametrize("text, expected", [
    ("a  week", "1 week"), ("a\tweek", "1 week"), ("an   hour", "1 hour"), ("a\nday", "1 day"), (" a week ", "1 week"),
    ("two   weeks", "2 week"),
])
def test_spacing_does_not_matter(text, expected):
    assert show(read_temporal(text, kind="duration")) == expected


# Units in the spellings people use, attached or not, keep their unit.
@pytest.mark.parametrize("text, expected", [
    ("90 mins", "90 minute"), ("1.50hours", "1.5 hour"), ("2 hrs", "2 hour"), ("30 sec", "30 second"),
    ("45 secs", "45 second"), ("1 min", "1 minute"), ("3 wks", "3 week"), ("2 yrs", "2 year"), ("6 mos", "6 month"),
    ("1 qtr", "1 quarter"), ("2 qtrs", "2 quarter"), ("90minutes", "90 minute"), ("2-week", "2 week"),
    ("90m", "90 minute"), ("1.50h", "1.5 hour"), ("3d", "3 day"), ("2w", "2 week"), ("30s", "30 second"),
    ("5y", "5 year"), ("one qtr", "1 quarter"), ("two qtrs", "2 quarter"), ("two quarters", "2 quarter"),
])
def test_unit_spellings(text, expected):
    assert show(read_temporal(text, kind="duration")) == expected


@pytest.mark.parametrize("text", ["one quarter  of an hour", "a quarter of an hour", "one quarter-of-an-hour",
                                  "a quarter-of-an-hour"])
def test_a_quarter_of_an_hour_is_never_a_calendar_quarter(text):
    r = read_temporal(text, kind="duration")
    assert r.value is None or r.value.unit not in ("month", "quarter")


# ---------- final audit (2026-10-27): items 1-8, each on its whole class ----------


@pytest.mark.parametrize("n", [1, 2, 9, 12, 13, 15, 28])
@pytest.mark.parametrize("period, start", [("last month", date(2026, 9, 1)), ("next month", date(2026, 11, 1)),
                                           ("next year", date(2027, 1, 1)), ("this month", date(2026, 10, 1))])
def test_nth_of_a_period_is_that_day_of_it(n, period, start):  # 1: the qualifier was dropped
    r = read_temporal(f"the {ordinal(n)} of {period}", context=NOW, kind="date", anchor="relative")
    assert r.value.date == start.replace(day=n)


@pytest.mark.parametrize("text", ["the 2nd banana", "the 15th apple", "the 3rd attempt", "1st place"])
def test_an_ordinal_with_other_words_is_not_a_lone_day(text):  # 1: any text with one ordinal became a date
    assert read_temporal(text, context=NOW, kind="date", anchor="relative").status == "not_temporal"


@pytest.mark.parametrize("tens", [20, 30, 40, 50])
@pytest.mark.parametrize("direction, sign", [("past", -1), ("future", 1)])
def test_a_round_count_of_seconds_is_a_count(tens, direction, sign):  # 2: "20 second ago" became the 22nd
    from datetime import datetime, timedelta
    phrase = f"{tens} second ago" if direction == "past" else f"in {tens} second"
    r = read_temporal(phrase, context=NOW, kind="date", anchor="relative", direction=direction)
    assert r.value.datetime == datetime(2026, 10, 27) + sign * timedelta(seconds=tens)


@pytest.mark.parametrize("text", ["the second day of next year", "the 15th of last month", "the first week of next month",
                                  "the end of next week", "mid next month"])
def test_a_recognised_form_whose_period_is_refused_is_refused(text):  # 3: fell through to the lone-day rule
    r = read_temporal(text, context=NOW, kind="date" if "week of" not in text else "period", anchor="absolute")
    assert (r.status, r.reason) == ("not_temporal", "ANCHOR_MISMATCH")


@pytest.mark.parametrize("clock, hour", [("noon", 12), ("5pm", 17), ("9:30", 9), ("midnight", 0)])
def test_a_lone_day_keeps_its_time(clock, hour):  # 4: "the 15th at noon" lost its time
    r = read_temporal(f"the 15th at {clock}", context=NOW, kind="date", anchor="absolute")
    assert (r.value.date, r.value.datetime.hour) == (date(2026, 11, 15), hour)


@pytest.mark.parametrize("text", ["3 days, 2 hours", "3 days,2 hours", "3 days & 2 hours", "3 days and 2 hours",
                                  "3 days 2 hours", "3 days, 2 hours."])
def test_duration_pieces_with_any_separator(text):  # 5: valid durations were refused
    assert show(read_temporal(text, kind="duration")) == "74 hour"


@pytest.mark.parametrize("text, expected", [("1h30m", "90 minute"), ("2h15m", "135 minute"), ("1d12h", "36 hour"),
                                            ("3 days.", "3 day")])
def test_glued_unit_pairs(text, expected):  # 6: "1h30m" was refused
    assert show(read_temporal(text, kind="duration")) == expected


@pytest.mark.parametrize("text", ["01 02 2026", "01/02/2026", "1,2,2026"])
@pytest.mark.parametrize("labels, reason", [({"kind": "period", "anchor": "absolute"}, "KIND_MISMATCH"),
                                            ({"kind": "duration"}, None)])
def test_no_question_when_no_answer_would_be_used(text, labels, reason):  # 7: asked although both answers fail
    r = read_temporal(text, context=NOW, **labels)
    assert r.status == "not_temporal" and (reason is None or r.reason == reason)


def test_one_usable_answer_decides_without_asking():  # 7: past label, only one reading lies in the past
    r = read_temporal("01/02/2027", context=NOW, kind="date", anchor="relative", direction="past")
    assert r.status != "ask"


@pytest.mark.parametrize("n, expected", [(100, date(2027, 4, 10)), (32, date(2027, 2, 1)), (365, date(2027, 12, 31))])
def test_any_nth_day_of_a_year(n, expected):  # 8: only two-digit days were read
    r = read_temporal(f"the {ordinal(n)} day of 2027", context=NOW, kind="date", anchor="relative")
    assert r.value.date == expected


def test_a_day_beyond_its_period_is_out_of_range():
    r = read_temporal("the 366th day of 2027", context=NOW, kind="date", anchor="relative")
    assert (r.status, r.reason) == ("not_temporal", "OUT_OF_RANGE")


@pytest.mark.parametrize("text", ["10:10 26 Oct 2026", "26 Oct 2026 10:10", "10:10 2026-10-26"])
def test_a_time_is_never_a_day_month_pair(text):  # the colon rule
    r = read_temporal(text, context=NOW, kind="date", anchor="absolute")
    assert (r.status, r.value.date) == ("resolved", date(2026, 10, 26))
