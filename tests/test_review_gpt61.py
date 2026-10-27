"""Findings of the GPT-6.1-Sol review (2026-10-27). Each test failed before its fix. Reference: Tuesday 2026-10-27."""

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


def test_nested_forms_are_bounded_by_the_length_limit():  # 1: was a RecursionError
    r = read_temporal("last week of " * 350 + "October", context=NOW, kind="period", anchor="absolute")
    assert r.status == "not_temporal"


def test_a_decimal_count_of_fortnights_keeps_its_value():  # 2: "1.5 fortnights" was 1.1 weeks
    assert show(read_temporal("1.5 fortnights", kind="duration")) == "3 week"


def test_a_month_period_ignores_the_probe_day():  # 3: on the 5th, "October" was this October
    r = read_temporal("October", context=TemporalContext(reference_date=date(2026, 10, 5)), kind="period",
                      anchor="absolute")
    assert show(r) == "2027-10-01..2027-11-01"


@pytest.mark.parametrize("text, kind", [("tomorrow", "date"), ("Wednesday", "date"), ("this month", "period")])
def test_a_relative_phrase_labelled_absolute_is_refused(text, kind):  # 4: "tomorrow" was 2026-11-01
    r = read_temporal(text, context=NOW, kind=kind, anchor="absolute")
    assert (r.status, r.reason) == ("not_temporal", "ANCHOR_MISMATCH")


def test_a_decimal_length_is_not_a_day_month_pair():  # 5: "1.5 years" asked the day/month order
    r = read_temporal("1.5 years", context=NOW, kind="date", anchor="relative")
    assert r.status == "not_temporal"


@pytest.mark.parametrize("text", ["tomorrow", "this month", "today"])
def test_a_date_is_not_a_duration(text):  # 6: "tomorrow" was 1 day, "this month" 0 months
    r = read_temporal(text, context=NOW, kind="duration")
    assert (r.status, r.reason) == ("not_temporal", "KIND_MISMATCH")


@pytest.mark.parametrize("text, expected", [
    ("1 second", "1 second"), ("3 hours", "3 hour"), ("90 minutes", "90 minute"), ("a week", "1 week"),
])
def test_time_units_are_kept(text, expected):  # 7: "1 second" was 1.157e-05 day
    assert show(read_temporal(text, kind="duration")) == expected


@pytest.mark.parametrize("text, expected", [
    ("October twenty-second 2026", "2026-10-22"), ("the twenty second of October 2026", "2026-10-22"),
])
def test_compound_ordinals_ending_in_second(text, expected):  # 10: was not_temporal
    assert show(read_temporal(text, context=NOW, kind="date", anchor="absolute")) == expected


def test_a_part_of_a_period_reports_the_year_filled_inside():  # 12: filled was ()
    r = read_temporal("beginning of October", context=NOW, kind="date", anchor="absolute")
    assert (show(r), r.provenance.filled) == ("2027-10-01", ("year",))