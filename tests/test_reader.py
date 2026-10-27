import json
from datetime import date, datetime

import pytest

from datephrase import TemporalConfigError, TemporalContext, answer, read_temporal, reader

REF = TemporalContext(reference_date=date(2026, 10, 26))


# What dateparser 1.4.3 answers with a fixed reference date and no classification. These pin the backend: a version
# bump that changes an answer fails here first, so the change is seen before it reaches the CLI.
@pytest.mark.parametrize("text, expected, period", [
    ("2026-10-26", "2026-10-26", "day"),
    ("October 26, 2026", "2026-10-26", "day"),
    ("26 Oct 2026", "2026-10-26", "day"),
    ("October 26th 2026", "2026-10-26", "day"),
    ("26/10/2026", "2026-10-26", "day"),        # month 26 does not exist: one reading only
    ("October", "2026-10-26", "month"),         # unclassified: the reference date fills the gaps
    ("tomorrow", "2026-10-27", "day"),
    ("in two weeks", "2026-11-09", "week"),
    ("two weeks ago", "2026-10-12", "week"),
    ("last month", "2026-09-26", "month"),
    ("3 months", "2026-07-26", "month"),        # unclassified: a duration is read as that long ago
    ("2026-10-26 at 14:30", "2026-10-26", "day"),
])
def test_backend_answers(text, expected, period):
    r = read_temporal(text, context=REF)
    assert (r.status, r.date.isoformat(), r.period) == ("resolved", expected, period)


@pytest.mark.parametrize("text", ["cotton", "2026-02-29", "next Monday", "", "   "])
def test_not_temporal(text):
    r = read_temporal(text, context=REF)
    assert (r.status, r.value, r.question, r.to_dict()["details"]) == ("not_temporal", None, None, None)


def test_time_of_day_is_kept_in_datetime():
    assert read_temporal("2026-10-26 at 14:30", context=REF).value.datetime == datetime(2026, 10, 26, 14, 30)


def test_year_first_dates_ignore_date_order():
    for order in (None, "MDY", "DMY"):
        assert read_temporal("2026-02-01", context=TemporalContext(date_order=order)).date == date(2026, 2, 1)


def test_session_date_order_answers_without_asking():
    assert read_temporal("01/02/2026", context=TemporalContext(date_order="DMY")).date == date(2026, 2, 1)
    assert read_temporal("01/02/2026", context=TemporalContext(date_order="MDY")).date == date(2026, 1, 2)
    assert read_temporal("02/02/2026").date == date(2026, 2, 2)  # both orders agree: nothing to ask


def test_reference_date_is_used_and_recorded():
    other = TemporalContext(reference_date=date(2020, 6, 15))
    assert [read_temporal("tomorrow", context=c).date for c in (REF, other, REF)] == [
        date(2026, 10, 27), date(2020, 6, 16), date(2026, 10, 27)]
    d = read_temporal("tomorrow", context=REF).to_dict()["provenance"]
    assert d == {"backend": "dateparser 1.4.3", "reference_date": "2026-10-26", "reference_source": "session",
                 "settings": {"PREFER_LOCALE_DATE_ORDER": False, "RELATIVE_BASE": "2026-10-26T00:00:00"},
                 "classification": {"kind": None, "anchor": None, "direction": None}, "filled": [], "defaults": [],
                 "policies": {}, "text_read": None, "form": None}


def test_output_is_json_with_metadata():
    d = json.loads(json.dumps(read_temporal("2026-10-26").to_dict()))
    assert d["value"] == {"kind": "date", "date": "2026-10-26", "datetime": "2026-10-26T00:00:00"}
    assert d["details"] == {"weekday": "monday", "iso_week": "2026-W44", "quarter": 4, "day_of_year": 299}
    assert (d["period"], d["locale"], d["question"]) == ("day", "en", None)
    assert "request" not in d  # only an ask carries what is needed to answer it


def test_offsets():
    text = "  October 26, 2026 "
    r = read_temporal(text, origin_offset=100)
    assert (r.source.text, r.source.start, r.source.end) == ("October 26, 2026", 102, 118)


def test_oversized_input_is_not_parsed():
    assert read_temporal("2026-10-26" + "x" * 600).status == "not_temporal"


# ---------- the caller's classification ----------


WEEK = TemporalContext(reference_date=date(2026, 10, 26), week_start="monday")


@pytest.mark.parametrize("text, start, end, grain", [
    ("last month", "2026-09-01", "2026-10-01", "month"),
    ("this week", "2026-10-26", "2026-11-02", "week"),
    ("next year", "2027-01-01", "2028-01-01", "year"),
])
def test_period_is_the_whole_period(text, start, end, grain):
    v = read_temporal(text, context=WEEK, kind="period", anchor="relative").value
    assert (v.start.isoformat(), v.end.isoformat(), v.grain) == (start, end, grain)


@pytest.mark.parametrize("text, quantity, unit", [
    ("30 days", 30, "day"), ("two weeks", 2, "week"), ("3 months", 3, "month"), ("one year", 1, "year"),
])
def test_duration_keeps_its_unit(text, quantity, unit):
    v = read_temporal(text, context=REF, kind="duration").value
    assert (v.quantity, v.unit) == (quantity, unit)


def test_duration_does_not_depend_on_the_session():
    late = TemporalContext(reference_date=date(2026, 1, 31))
    assert read_temporal("one month", context=late, kind="duration").value.quantity == 1


def test_absolute_full_date_is_not_asked():
    assert read_temporal("26 October 2026", kind="date", anchor="absolute").date == date(2026, 10, 26)
    assert read_temporal("October 2026", kind="period", anchor="absolute").value.grain == "month"


def test_absolute_unreadable_is_not_temporal():
    assert read_temporal("cotton", context=REF, kind="date", anchor="absolute").status == "not_temporal"


def test_label_and_backend_disagree_is_never_forced():
    r = read_temporal("2026-10-26", context=REF, kind="period")  # dateparser found a single day
    assert (r.status, r.reason, r.value) == ("not_temporal", "KIND_MISMATCH", None)


def test_classification_is_recorded():
    p = read_temporal("last month", context=REF, kind="period", anchor="relative", direction="past").to_dict()["provenance"]
    assert p["classification"] == {"kind": "period", "anchor": "relative", "direction": "past"}


# ---------- asking: only when nothing points either way ----------


def ask_then_answer(text, reply, context=None, **labels):
    """The client's round trip: the question travels as JSON and comes back with the user's reply."""
    r = read_temporal(text, context=context, **labels)
    assert (r.status, r.value) == ("ask", None), r
    return r, answer(json.loads(json.dumps(r.to_dict())), reply)


NOW = TemporalContext(reference_date=date(2026, 10, 27))


@pytest.mark.parametrize("text, labels, reply, expected", [
    ("01/02/2026", {"kind": "date", "anchor": "absolute"}, "DMY", date(2026, 2, 1)),
    ("01/02/2026", {}, "MDY", date(2026, 1, 2)),
    ("01/02", {"kind": "date", "anchor": "absolute"}, "DMY", date(2027, 2, 1)),  # with the next-occurrence year
])
def test_day_month_order_is_asked_and_answered(text, labels, reply, expected):
    asked, r = ask_then_answer(text, reply, NOW, **labels)
    assert (asked.question.reason, asked.question.field) == ("DATE_ORDER_AMBIGUOUS", "date_order")
    assert (r.status, r.date) == ("resolved", expected)


def test_choice_questions_offer_what_can_be_sent_back():
    q = read_temporal("01/02/2026").to_dict()["question"]
    assert q["options"] == [{"value": "MDY", "label": "January 2, 2026"}, {"value": "DMY", "label": "February 1, 2026"}]
    assert q["text"] == "Does '01/02/2026' mean January 2, 2026 or February 1, 2026?"


def test_order_question_shows_the_next_occurrence():
    r = read_temporal("01/02", context=NOW, kind="date", anchor="absolute")
    assert r.question.text == "Does '01/02' mean January 2, 2027 or February 1, 2027?"


@pytest.mark.parametrize("text", ["03/15", "15/03"])
def test_an_invented_reading_is_never_offered(text):
    # day first, "03/15" would make dateparser read 15 as a year and invent the month (October 3, 2115)
    r = read_temporal(text, context=NOW, kind="date", anchor="absolute")
    assert (r.status, r.date) == ("resolved", date(2027, 3, 15))


def test_one_valid_order_is_not_asked():
    assert read_temporal("26/10/2026").date == date(2026, 10, 26)


def test_answers_accumulate_across_questions():
    # an earlier setting travels with the question and is kept when it is answered
    r = read_temporal("01/02/2026", context=NOW, answers={"week_start": "sunday"})
    r = answer(r.to_dict(), "DMY")
    assert r.request.answers == {"week_start": "sunday", "date_order": "DMY"}
    assert r.date == date(2026, 2, 1)


def test_ask_keeps_offsets_through_the_answer():
    _, r = ask_then_answer("  01/02/2026", "DMY", origin_offset=10)
    assert (r.source.start, r.source.end) == (12, 22)


def test_bad_answers_are_refused():
    asked = read_temporal("01/02/2026")
    for bad in ("YDM", 1, True, None, "nonsense"):
        with pytest.raises(TemporalConfigError):
            answer(asked, bad)  # only an offered option goes back
    with pytest.raises(TemporalConfigError):
        answer(read_temporal("2026-10-26"), "DMY")  # nothing was asked
    with pytest.raises(TemporalConfigError):
        read_temporal("01/02/2026", answers={"date_order": "nonsense"})  # replies are validated like settings


# ---------- missing parts: filled by rule, never asked (decided 2026-10-27) ----------


@pytest.mark.parametrize("text, kind, expected, filled", [
    ("March 15", "date", {"kind": "date", "date": "2027-03-15"}, ["year"]),
    ("October 28", "date", {"kind": "date", "date": "2026-10-28"}, ["year"]),      # still ahead this year
    ("October 27", "date", {"kind": "date", "date": "2027-10-27"}, ["year"]),      # today itself is not "next"
    ("February 29", "date", {"kind": "date", "date": "2028-02-29"}, ["year"]),     # the next leap year
    ("15", "date", {"kind": "date", "date": "2026-11-15"}, ["year", "month"]),     # the next 15th
    ("November", "period", {"kind": "period", "start": "2026-11-01", "end": "2026-12-01", "grain": "month"}, ["year"]),
    ("October", "period", {"kind": "period", "start": "2027-10-01", "end": "2027-11-01", "grain": "month"}, ["year"]),
])
def test_missing_year_or_month_is_the_next_occurrence(text, kind, expected, filled):
    r = read_temporal(text, context=NOW, kind=kind, anchor="absolute")
    got = r.value.to_dict()
    got.pop("datetime", None)
    assert (r.status, got) == ("resolved", expected)
    assert (list(r.provenance.filled), r.provenance.reference_date) == (filled, date(2026, 10, 27))


@pytest.mark.parametrize("text, expected", [("February 2026", date(2026, 2, 1)), ("October 2026", date(2026, 10, 1))])
def test_missing_day_is_the_first(text, expected):
    r = read_temporal(text, context=NOW, kind="date", anchor="absolute")
    assert (r.status, r.date, r.provenance.filled) == ("resolved", expected, ("day",))
    assert r.provenance.reference_date is None  # the 1st does not depend on today


def test_no_session_reference_uses_today(monkeypatch):
    monkeypatch.setattr(reader, "_today", lambda: date(2026, 10, 27))
    r = read_temporal("tomorrow", kind="date", anchor="relative")
    assert (r.date, r.provenance.reference_source) == (date(2026, 10, 28), "system_clock")
    assert read_temporal("March 15", kind="date", anchor="absolute").date == date(2027, 3, 15)


def test_session_reference_wins_over_today(monkeypatch):
    monkeypatch.setattr(reader, "_today", lambda: date(1999, 1, 1))
    r = read_temporal("tomorrow", context=NOW, kind="date", anchor="relative")
    assert (r.date, r.provenance.reference_source) == (date(2026, 10, 28), "session")


# ---------- defaults for unset settings, recorded in provenance ----------


def test_week_start_defaults_to_monday():
    r = read_temporal("this week", context=NOW, kind="period", anchor="relative")
    assert (r.value.start, r.value.end) == (date(2026, 10, 26), date(2026, 11, 2))
    assert ("week_start:monday" in r.provenance.defaults, r.provenance.policies) == (True, {"week_start": "monday"})


def test_session_week_start_is_recorded_as_a_policy_not_a_default():
    r = read_temporal("this week", context=TemporalContext(date(2026, 10, 27), week_start="sunday"),
                      kind="period", anchor="relative")
    assert (r.value.start, r.provenance.policies) == (date(2026, 10, 25), {"week_start": "sunday"})
    assert not any(d.startswith("week_start") for d in r.provenance.defaults)


def test_month_overflow_defaults_to_clamp():
    r = read_temporal("in one month", context=TemporalContext(date(2026, 1, 31)), kind="date", anchor="relative")
    assert (r.date, r.provenance.policies) == (date(2026, 2, 28), {"month_overflow": "clamp"})
    assert "month_overflow:clamp" in r.provenance.defaults


def test_reject_overflow_leaves_it_unresolved():
    r = read_temporal("in one month", context=TemporalContext(date(2026, 1, 31), month_overflow="reject"),
                      kind="date", anchor="relative")
    assert (r.status, r.reason, r.value) == ("not_temporal", "MONTH_OVERFLOW_REJECTED", None)


def test_no_overflow_no_policy_recorded():
    r = read_temporal("in two months", context=TemporalContext(date(2026, 1, 31)), kind="date", anchor="relative")
    assert (r.date, r.provenance.policies) == (date(2026, 3, 31), {})


# ---------- durations: exact, or nothing ----------


@pytest.mark.parametrize("text, quantity, unit", [
    ("2.5 days", 2.5, "day"), ("1.5 weeks", 1.5, "week"), ("1 year 2 months", 14, "month"),
    ("three weeks and two days", 23, "day"),
])
def test_duration_quantity_is_exact(text, quantity, unit):
    v = read_temporal(text, kind="duration").value
    assert (v.quantity, v.unit) == (quantity, unit)


def test_months_and_days_together_have_no_single_unit():
    r = read_temporal("two months and five days", kind="duration")
    assert (r.status, r.reason) == ("not_temporal", "MIXED_UNITS")


# ---------- calendar bounds ----------


@pytest.mark.parametrize("text, labels, context", [
    ("9999", {"kind": "period", "anchor": "absolute"}, None),
    ("next year", {"kind": "period", "anchor": "relative"}, TemporalContext(reference_date=date(9999, 6, 1))),
    ("tomorrow", {"kind": "date", "anchor": "relative"}, TemporalContext(reference_date=date(9999, 12, 31))),
])
def test_beyond_year_9999_is_out_of_range_not_a_crash(text, labels, context):
    r = read_temporal(text, context=context, **labels)
    assert (r.status, r.value) == ("not_temporal", None)


# ---------- direction: past or future, labelled by the classifier for relative expressions ----------


@pytest.mark.parametrize("direction, expected", [("past", date(2026, 10, 21)), ("future", date(2026, 10, 28))])
def test_direction_label_answers_past_or_future(direction, expected):
    r = read_temporal("Wednesday", context=NOW, kind="date", anchor="relative", direction=direction)
    assert (r.status, r.date) == ("resolved", expected)
    assert r.provenance.direction == direction


def test_without_direction_the_default_is_future():
    r = read_temporal("Wednesday", context=NOW, kind="date", anchor="relative")
    assert (r.status, r.date) == ("resolved", date(2026, 10, 28))
    p = r.to_dict()["provenance"]
    assert (p["classification"]["direction"], p["defaults"]) == (None, ["direction:future"])  # applied, not received


@pytest.mark.parametrize("text, expected", [("two weeks ago", date(2026, 10, 13)), ("yesterday", date(2026, 10, 26))])
def test_default_direction_never_overrides_the_text(text, expected):
    r = read_temporal(text, context=NOW, kind="date", anchor="relative")
    assert (r.status, r.date) == ("resolved", expected)


def test_default_direction_only_for_relative():
    assert read_temporal("26 October 2026", kind="date", anchor="absolute").provenance.defaults == ()


@pytest.mark.parametrize("text, direction", [("tomorrow", "past"), ("two weeks ago", "future")])
def test_direction_contradicting_the_text_is_never_forced(text, direction):
    r = read_temporal(text, context=NOW, kind="date", anchor="relative", direction=direction)
    assert (r.status, r.reason, r.value) == ("not_temporal", "DIRECTION_MISMATCH", None)


def test_direction_needs_no_opinion_on_today():
    assert read_temporal("today", context=NOW, kind="date", anchor="relative", direction="past").date == date(2026, 10, 27)


def test_config_errors():
    with pytest.raises(TemporalConfigError):
        TemporalContext(reference_date=datetime(2026, 10, 26))
    with pytest.raises(TemporalConfigError):
        TemporalContext(date_order="DM")
    with pytest.raises(TemporalConfigError):
        TemporalContext.from_dict({"reference_date": "26/10/2026"})
    with pytest.raises(TemporalConfigError):
        read_temporal("today", context={"reference_date": "2026-10-26"})
    with pytest.raises(TemporalConfigError):
        read_temporal("today", kind="range")
    with pytest.raises(TemporalConfigError):
        read_temporal("today", anchor="past")
    with pytest.raises(TemporalConfigError):
        read_temporal("today", answers={"colour": "red"})
    with pytest.raises(TemporalConfigError):
        read_temporal("today", anchor="relative", direction="soon")
    with pytest.raises(TemporalConfigError):
        read_temporal("26 October 2026", anchor="absolute", direction="past")  # direction belongs to relative ones
