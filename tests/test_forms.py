"""The CLI integration's cases (2026-10-27): forms built on dateparser, rewrites, and reading a column of cells.
Labels are the ones the CLI's classifier sends; expected values are written by hand for reference 2026-10-27."""

from datetime import date

import pytest

from temporal_reader import TemporalContext, read_column, read_temporal

NOW = TemporalContext(reference_date=date(2026, 10, 27))  # a Tuesday


def show(r):
    v = r.value
    if v is None:
        return r.status
    if v.kind == "period":
        return f"{v.start}..{v.end}"
    if v.kind == "duration":
        return f"{v.quantity} {v.unit}"
    return str(v.date)


@pytest.mark.parametrize("text, kind, anchor, direction, expected", [
    # quarters
    ("Q3 2026", "period", "absolute", None, "2026-07-01..2026-10-01"),
    ("2026-Q3", "period", "absolute", None, "2026-07-01..2026-10-01"),
    ("first quarter of 2026", "period", "absolute", None, "2026-01-01..2026-04-01"),
    ("last quarter", "period", "relative", "past", "2026-07-01..2026-10-01"),
    ("this quarter", "period", "relative", None, "2026-10-01..2027-01-01"),
    ("next quarter", "period", "relative", "future", "2027-01-01..2027-04-01"),
    ("Q2", "period", "relative", "past", "2026-04-01..2026-07-01"),           # no year: the latest one, past
    ("Q2", "period", "absolute", None, "2027-04-01..2027-07-01"),            # no year: the next one
    # next / last + weekday
    ("next Monday", "date", "relative", "future", "2026-11-02"),
    ("last Friday", "date", "relative", "past", "2026-10-23"),
    ("next tue", "date", "relative", "future", "2026-11-03"),                # today is Tuesday: the next one
    # numbered weeks (ISO)
    ("week 43", "period", "relative", "past", "2026-10-19..2026-10-26"),
    ("2026-W43", "period", "absolute", None, "2026-10-19..2026-10-26"),
    ("week 43", "period", "absolute", None, "2027-10-25..2027-11-01"),       # no year, absolute: the next one
    # dates in words
    ("October twenty-sixth", "date", "relative", "past", "2026-10-26"),
    ("the first of March", "date", "relative", "past", "2026-03-01"),
    # parts of a period
    ("end of the month", "date", "relative", "future", "2026-10-31"),
    ("the beginning of next week", "date", "relative", "future", "2026-11-02"),
    ("mid-October", "date", "relative", "past", "2026-10-15"),
    ("the end of next month", "date", "relative", "future", "2026-11-30"),
    ("the first week of September", "period", "relative", "past", "2026-09-01..2026-09-08"),
    ("the last week of September", "period", "relative", "past", "2026-09-24..2026-10-01"),
    # periods up to today: today included
    ("year to date", "period", "relative", "past", "2026-01-01..2026-10-28"),
    ("month to date", "period", "relative", "past", "2026-10-01..2026-10-28"),
    ("YTD", "period", "relative", "past", "2026-01-01..2026-10-28"),
    # lengths in words
    ("a fortnight", "duration", None, None, "2 week"),
    ("half a year", "duration", None, None, "6 month"),
    ("a couple of days", "duration", None, None, "2 day"),
    ("one quarter", "duration", None, None, "1 quarter"),
    ("twenty-one days", "duration", None, None, "21 day"),
    # weekends: Saturday and Sunday
    ("last weekend", "period", "relative", "past", "2026-10-24..2026-10-26"),
    ("this weekend", "period", "relative", "future", "2026-10-31..2026-11-02"),
])
def test_cli_cases(text, kind, anchor, direction, expected):
    r = read_temporal(text, context=NOW, kind=kind, anchor=anchor, direction=direction)
    assert (r.status, show(r)) == ("resolved", expected), r.reason


@pytest.mark.parametrize("text, kind, anchor, direction", [
    ("2 business days", "duration", None, None),     # no business calendar
    ("the summer", "period", "relative", "past"),    # hemisphere-dependent
    ("Christmas", "date", "relative", None),         # holiday calendars are out of scope
    ("next weekend", "period", "relative", "future"),  # this weekend or the one after: English does not say
])
def test_decided_not_read(text, kind, anchor, direction):
    assert read_temporal(text, context=NOW, kind=kind, anchor=anchor, direction=direction).status == "not_temporal"


def test_week_to_date_uses_the_week_start():
    r = read_temporal("week to date", context=NOW, kind="period", anchor="relative", direction="past")
    assert show(r) == "2026-10-26..2026-10-28"
    assert r.provenance.policies == {"week_start": "monday"}


def test_form_and_rewrite_are_recorded():
    r = read_temporal("Q3 2026", context=NOW, kind="period", anchor="absolute")
    assert (r.provenance.form, r.provenance.text_read) == ("quarter", None)
    r = read_temporal("next Monday", context=NOW, kind="date", anchor="relative", direction="future")
    assert (r.provenance.form, r.provenance.text_read) == (None, "Monday")


def test_a_form_of_another_kind_is_never_forced():
    r = read_temporal("Q3 2026", context=NOW, kind="date", anchor="absolute")
    assert (r.status, r.reason) == ("not_temporal", "KIND_MISMATCH")


def test_a_direction_label_against_the_text_is_refused():
    # "last Friday" says past; a future label contradicts it
    r = read_temporal("last Friday", context=NOW, kind="date", anchor="relative", direction="future")
    assert (r.status, r.reason) == ("not_temporal", "DIRECTION_MISMATCH")


def test_week_53_of_a_52_week_year_is_out_of_range():
    r = read_temporal("2025-W53", context=NOW, kind="period", anchor="absolute")  # 2026 has a week 53; 2025 not
    assert (r.status, r.reason) == ("not_temporal", "OUT_OF_RANGE")


def test_number_words_are_rewritten_only_for_classified_text():
    # unclassified, "the second" stays not a date (the brief's D50); classified, number words become digits
    assert read_temporal("the second", context=NOW).status == "not_temporal"


# ---------- a column of cells ----------


def test_one_cell_with_a_day_over_12_settles_the_column():
    r = read_column(["01/10/2026", "26.10.2026", "05/11/2026"], context=NOW)
    assert (r.order, r.order_source, r.question) == ("DMY", "evidence", None)
    assert r.cells == {"01/10/2026": "2026-10-01", "26.10.2026": "2026-10-26", "05/11/2026": "2026-11-05"}
    assert r.evidence == {"MDY": [], "DMY": ["26.10.2026"]}


def test_month_first_evidence():
    r = read_column(["10/26/2026", "01/02/2026"], context=NOW)
    assert (r.order, r.cells) == ("MDY", {"10/26/2026": "2026-10-26", "01/02/2026": "2026-01-02"})


def test_no_evidence_one_question_for_the_column():
    r = read_column(["01/10/2026", "05/11/2026", "2026-10-26"], context=NOW)
    assert (r.order, r.question.reason, r.question.field) == (None, "DATE_ORDER_AMBIGUOUS", "date_order")
    assert [o[0] for o in r.question.options] == ["MDY", "DMY"]
    # cells that depend on the order wait; the others are read
    assert r.cells == {"01/10/2026": None, "05/11/2026": None, "2026-10-26": "2026-10-26"}


def test_the_answer_is_the_session_order():
    r = read_column(["01/10/2026", "05/11/2026"], context=TemporalContext(date(2026, 10, 27), date_order="DMY"))
    assert (r.order, r.order_source, r.question) == ("DMY", "session", None)
    assert r.cells == {"01/10/2026": "2026-10-01", "05/11/2026": "2026-11-05"}


def test_contradicting_cells_ask_once_and_wait():
    r = read_column(["26/10/2026", "10/26/2026", "01/02/2026"], context=NOW)
    assert (r.order, r.question.reason) == (None, "ORDER_CONFLICT")
    assert set(r.cells.values()) == {None}


def test_a_cell_against_the_chosen_order_is_not_read():
    r = read_column(["26/10/2026", "10/26/2026"], context=TemporalContext(date(2026, 10, 27), date_order="DMY"))
    assert r.cells == {"26/10/2026": "2026-10-26", "10/26/2026": None}


def test_cells_that_need_no_order_and_non_dates():
    r = read_column(["2026-10-26", "26 Oct 2026", "", "  ", "n/a", "02/02/2026"], context=NOW)
    assert (r.order, r.question) == (None, None)
    assert r.cells == {"2026-10-26": "2026-10-26", "26 Oct 2026": "2026-10-26", "n/a": None, "02/02/2026": "2026-02-02"}


def test_distinct_cells_only_and_filled_parts_reported():
    r = read_column(["26/10", "26/10", "01/02/2026"], context=NOW)
    assert (r.order, list(r.cells)) == ("DMY", ["26/10", "01/02/2026"])
    assert (r.cells["26/10"], r.filled) == ("2027-10-26", {"26/10": ("year",)})  # the next occurrence


def test_column_json():
    d = read_column(["01/10/2026"], context=NOW).to_dict()
    assert set(d) == {"order", "order_source", "question", "cells", "filled", "evidence"}
    assert d["question"]["options"][1]["value"] == "DMY"


# ---------- review findings (2026-10-27): each test failed before its fix ----------


@pytest.mark.parametrize("text, kind, anchor, direction, expected", [
    ("in a second", "date", "relative", "future", "2026-10-27"),     # "second" the unit, not the ordinal 2nd
    ("a second ago", "date", "relative", "past", "2026-10-26"),      # one second before midnight of the 27th
    ("a second", "duration", None, None, "resolved"),
    ("1 second", "duration", None, None, "resolved"),
    ("the second of March", "date", "relative", "past", "2026-03-02"),  # the ordinal still reads
    ("the second week of September", "period", "relative", "past", "2026-09-08..2026-09-15"),
])
def test_second_as_a_unit_survives_number_words(text, kind, anchor, direction, expected):
    r = read_temporal(text, context=NOW, kind=kind, anchor=anchor, direction=direction)
    assert (r.status if expected == "resolved" else show(r)) == expected, (r.status, r.reason)


@pytest.mark.parametrize("text, direction", [
    ("first of the month", "future"), ("the first", "past"), ("1st of the month", "future"),
])
def test_an_ordinal_day_dateparser_misreads_is_never_a_wrong_date(text, direction):
    # dateparser reads "1st of the month" as the 27th; a day that is not the written one is refused
    r = read_temporal(text, context=NOW, kind="date", anchor="relative", direction=direction)
    assert r.status != "resolved" or r.date.day == 1, show(r)


@pytest.mark.parametrize("text, expected", [
    ("first day of next month", "2026-11-01"), ("the last day of next month", "2026-11-30"),
])
def test_first_and_last_day_of_a_period(text, expected):
    r = read_temporal(text, context=NOW, kind="date", anchor="relative", direction="future")
    assert show(r) == expected


@pytest.mark.parametrize("column", [["12/2026", "26/10/2026"], ["12/2026", "01/02/2026"]])
def test_a_cell_the_order_cannot_change_is_read_either_way(column):
    assert read_column(column, context=NOW).cells["12/2026"] == "2026-12-01"


def test_a_part_of_a_period_reports_the_week_start_it_used():
    r = read_temporal("the beginning of next week", context=NOW, kind="date", anchor="relative", direction="future")
    assert (r.provenance.policies, "week_start:monday" in r.provenance.defaults) == ({"week_start": "monday"}, True)


@pytest.mark.parametrize("text, expected", [("Q4", "2027-10-01..2028-01-01"), ("week 43", "2027-10-25..2027-11-01")])
def test_a_year_filled_for_quarters_and_weeks_is_recorded(text, expected):
    r = read_temporal(text, context=NOW, kind="period", anchor="absolute")
    assert (show(r), r.provenance.filled) == (expected, ("year",))


def test_this_weekday_follows_the_label():
    r = read_temporal("this Monday", context=NOW, kind="date", anchor="relative", direction="past")
    assert show(r) == "2026-10-26"


def test_week_53_in_the_past_is_the_latest_year_that_has_one():
    r = read_temporal("week 53", context=NOW, kind="period", anchor="relative", direction="past")
    assert show(r) == "2020-12-28..2021-01-04"


def test_a_quarter_of_an_hour_is_not_three_months():
    r = read_temporal("one quarter of an hour", context=NOW, kind="duration")
    assert r.provenance.text_read is None or "month" not in r.provenance.text_read
