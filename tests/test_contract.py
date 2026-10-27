"""The contract layer alone, against a fake Duckling: shapes, refusals, the one question, provenance. Duckling's own
reading is not tested here (see test_live.py); these tests pin what datephrase adds on top of it."""

import json
from datetime import date, datetime

import pytest

from datephrase import (DateValue, DurationValue, EngineUnavailable, PeriodValue, TemporalConfigError,
                        TemporalContext, answer, read_temporal, reader)

NOW = TemporalContext(reference_date=date(2026, 10, 27))  # a Tuesday


def time_entity(text, value, grain="day", start=0, end=None):
    return {"body": text[start:end or len(text)], "start": start, "end": end or len(text), "dim": "time",
            "latent": False, "value": {"type": "value", "value": value, "grain": grain}}


def interval(text, start, end):
    return {"body": text, "start": 0, "end": len(text), "dim": "time", "latent": False,
            "value": {"type": "interval", "from": {"value": start}, "to": {"value": end}}}


def duration(text, quantity, unit):
    return {"body": text, "start": 0, "end": len(text), "dim": "duration", "latent": False,
            "value": {"type": "value", "value": quantity, "unit": unit}}


@pytest.fixture
def engine(monkeypatch):
    """A fake Duckling: answers[(text, locale)] -> entities, or answers[text] for every locale. The probes for
    absolute phrases use reference dates in 2004 and 2012: probe[text] -> {year: entities}."""
    answers, probe, calls = {}, {}, []

    def fake(text, locale, dims, ref):
        calls.append((text, locale, tuple(dims), ref.date()))
        if ref.year in (2004, 2012) and text in probe:
            return probe[text].get(ref.year, [])
        return answers.get((text, locale), answers.get(text, []))
    monkeypatch.setattr(reader, "_duckling", fake)
    monkeypatch.setattr(reader, "_today", lambda: date(2026, 10, 27))
    return answers, probe, calls


def read(text, **labels):
    return read_temporal(text, context=NOW, **labels)


# ---------- shapes ----------

def test_a_day_is_a_date(engine):
    engine[0]["next Monday"] = [time_entity("next Monday", "2026-11-02T00:00:00.000+00:00")]
    r = read("next Monday", kind="date", anchor="relative")
    assert (r.status, r.value) == ("resolved", DateValue(date(2026, 11, 2), datetime(2026, 11, 2)))


@pytest.mark.parametrize("grain, start, end", [("week", "2026-10-26", "2026-11-02"), ("month", "2026-09-01", "2026-10-01"),
                                               ("quarter", "2026-07-01", "2026-10-01"), ("year", "2025-01-01", "2026-01-01")])
def test_a_coarse_grain_is_a_whole_period(engine, grain, start, end):
    engine[0]["x"] = [time_entity("x", f"{start}T00:00:00.000+00:00", grain)]
    v = read("x", kind="period").value
    assert (v.start.isoformat(), v.end.isoformat(), v.grain) == (start, end, grain)


def test_an_interval_is_a_period_from_start_to_end(engine):
    engine[0]["last weekend"] = [interval("last weekend", "2026-10-23T18:00:00.000+00:00", "2026-10-26T00:00:00.000+00:00")]
    assert read("last weekend", kind="period").value == PeriodValue(date(2026, 10, 23), date(2026, 10, 26), "interval")


def test_a_duration_keeps_its_unit(engine):
    engine[0]["90 minutes"] = [duration("90 minutes", 90, "minute")]
    assert read("90 minutes", kind="duration").value == DurationValue(90, "minute")


def test_without_labels_the_answer_takes_duckling_s_shape(engine):
    engine[0]["last month"] = [time_entity("last month", "2026-09-01T00:00:00.000+00:00", "month")]
    engine[0]["3 days"] = [duration("3 days", 3, "day")]
    assert isinstance(read("last month").value, PeriodValue)
    assert isinstance(read("3 days").value, DurationValue)


# ---------- refusals, never a forced value ----------

@pytest.mark.parametrize("entity, kind", [
    (time_entity("x", "2026-10-28T00:00:00.000+00:00", "day"), "period"),     # a day is not a period
    (time_entity("x", "2026-09-01T00:00:00.000+00:00", "month"), "duration"),
    (duration("x", 3, "day"), "date"),
    (interval("x", "2026-10-23T18:00:00.000+00:00", "2026-10-26T00:00:00.000+00:00"), "date"),
])
def test_an_answer_of_another_kind_is_refused(engine, entity, kind):
    engine[0]["x"] = [entity]
    r = read("x", kind=kind)
    assert (r.status, r.reason, r.value) == ("not_temporal", "KIND_MISMATCH", None)


@pytest.mark.parametrize("grain", ["week", "month", "quarter", "year"])
def test_a_date_asked_but_only_a_coarser_grain_known_is_refused(engine, grain):
    # Duckling answers "in one year" with 2027-10-01 at month grain: it does not know the day
    engine[0]["in one year"] = [time_entity("in one year", "2027-10-01T00:00:00.000+00:00", grain)]
    r = read("in one year", kind="date", anchor="relative")
    assert (r.status, r.reason) == ("not_temporal", "KIND_MISMATCH")


def test_a_reading_of_part_of_the_phrase_is_not_the_phrase(engine):
    # Duckling reads "the year 2027" as "the year" (= 2026): a part never counts
    engine[0]["the year 2027"] = [time_entity("the year 2027", "2026-01-01T00:00:00.000+00:00", "year", 0, 8)]
    assert read("the year 2027", kind="period").status == "not_temporal"


def test_a_latent_reading_does_not_count(engine):
    engine[0]["5"] = [dict(time_entity("5", "2026-11-05T00:00:00.000+00:00"), latent=True)]
    assert read("5").status == "not_temporal"


@pytest.mark.parametrize("value, direction", [("2026-10-28T00:00:00.000+00:00", "past"),
                                              ("2026-10-20T00:00:00.000+00:00", "future")])
def test_a_direction_label_against_the_reading_is_refused(engine, value, direction):
    engine[0]["x"] = [time_entity("x", value)]
    r = read("x", kind="date", anchor="relative", direction=direction)
    assert (r.status, r.reason) == ("not_temporal", "DIRECTION_MISMATCH")


def test_today_fits_either_direction(engine):
    engine[0]["today"] = [time_entity("today", "2026-10-27T00:00:00.000+00:00")]
    assert read("today", kind="date", anchor="relative", direction="past").status == "resolved"


def test_a_phrase_counted_from_today_is_not_absolute(engine):
    answers, probe, _ = engine
    answers["tomorrow"] = [time_entity("tomorrow", "2026-10-28T00:00:00.000+00:00")]
    probe["tomorrow"] = {2004: [time_entity("tomorrow", "2004-03-11T00:00:00.000+00:00")],
                         2012: [time_entity("tomorrow", "2012-08-24T00:00:00.000+00:00")]}
    r = read("tomorrow", kind="date", anchor="absolute")
    assert (r.status, r.reason) == ("not_temporal", "ANCHOR_MISMATCH")


def test_a_date_that_only_lacks_its_year_stays_absolute(engine):
    answers, probe, _ = engine
    answers["March 15"] = [time_entity("March 15", "2027-03-15T00:00:00.000+00:00")]
    probe["March 15"] = {2004: [time_entity("March 15", "2004-03-15T00:00:00.000+00:00")],
                         2012: [time_entity("March 15", "2013-03-15T00:00:00.000+00:00")]}
    assert read("March 15", kind="date", anchor="absolute").value.date == date(2027, 3, 15)


# ---------- the one question: day/month order ----------

def test_locales_reading_differently_ask_once(engine):
    answers = engine[0]
    answers[("03/04/2026", "en_US")] = [time_entity("03/04/2026", "2026-03-04T00:00:00.000+00:00")]
    answers[("03/04/2026", "en_GB")] = [time_entity("03/04/2026", "2026-04-03T00:00:00.000+00:00")]
    r = read("03/04/2026", kind="date", anchor="absolute")
    assert (r.status, r.question.field) == ("ask", "date_order")
    assert r.to_dict()["question"]["options"] == [{"value": "MDY", "label": "March 4, 2026"},
                                                  {"value": "DMY", "label": "April 3, 2026"}]
    assert answer(json.loads(json.dumps(r.to_dict())), "DMY").value.date == date(2026, 4, 3)


def test_one_locale_reading_the_whole_phrase_decides(engine):
    answers = engine[0]
    answers[("26/10/2026", "en_US")] = [time_entity("26/10/2026", "2026-10-01T00:00:00.000+00:00", "day", 3, 10)]
    answers[("26/10/2026", "en_GB")] = [time_entity("26/10/2026", "2026-10-26T00:00:00.000+00:00")]
    r = read("26/10/2026", kind="date", anchor="absolute")
    assert (r.status, r.value.date, r.provenance.engine["locale"]) == ("resolved", date(2026, 10, 26), "en_GB")


def test_the_session_order_asks_one_locale_only(engine):
    answers, _, calls = engine
    answers[("03/04/2026", "en_GB")] = [time_entity("03/04/2026", "2026-04-03T00:00:00.000+00:00")]
    r = read_temporal("03/04/2026", context=TemporalContext(date(2026, 10, 27), date_order="DMY"))
    assert r.value.date == date(2026, 4, 3)
    assert {c[1] for c in calls} == {"en_GB"}


def test_no_question_when_only_one_answer_fits_the_labels(engine):
    answers = engine[0]
    answers[("03/04/2026", "en_US")] = [time_entity("03/04/2026", "2026-03-04T00:00:00.000+00:00")]
    answers[("03/04/2026", "en_GB")] = [time_entity("03/04/2026", "2026-04-03T00:00:00.000+00:00")]
    r = read("03/04/2026", kind="date", anchor="relative", direction="past")  # both are past: still asked
    assert r.status == "ask"
    answers[("03/04/2027", "en_US")] = [time_entity("03/04/2027", "2027-03-04T00:00:00.000+00:00")]
    answers[("03/04/2027", "en_GB")] = [time_entity("03/04/2027", "2027-04-03T00:00:00.000+00:00")]
    r = read("03/04/2027", kind="date", anchor="relative", direction="past")  # neither is past: refused
    assert r.status == "not_temporal"
    answers[("10/11/2026", "en_US")] = [time_entity("10/11/2026", "2026-10-11T00:00:00.000+00:00")]
    answers[("10/11/2026", "en_GB")] = [time_entity("10/11/2026", "2026-11-10T00:00:00.000+00:00")]
    r = read("10/11/2026", kind="date", anchor="relative", direction="future")  # only November 10 is ahead
    assert (r.status, r.value.date) == ("resolved", date(2026, 11, 10))


# ---------- settings, provenance, errors ----------

def test_week_start_default_and_sunday(engine):
    engine[0]["this week"] = [time_entity("this week", "2026-10-26T00:00:00.000+00:00", "week")]
    r = read("this week", kind="period")
    assert (r.value.start, r.provenance.defaults) == (date(2026, 10, 26), ("week_start:monday",))
    r = read_temporal("this week", context=TemporalContext(date(2026, 10, 27), week_start="sunday"), kind="period")
    assert (r.value.start, r.value.end, r.provenance.policies) == (date(2026, 10, 25), date(2026, 11, 1),
                                                                   {"week_start": "sunday"})


def test_provenance_says_what_duckling_read(engine):
    engine[0]["last month"] = [time_entity("last month", "2026-09-01T00:00:00.000+00:00", "month")]
    p = read("last month", kind="period", anchor="relative").to_dict()["provenance"]
    assert (p["backend"], p["text_read"], p["engine"]["grain"], p["reference_date"], p["reference_source"]) == (
        "duckling", "last month", "month", "2026-10-27", "session")


def test_no_session_reference_uses_today(engine):
    engine[0]["tomorrow"] = [time_entity("tomorrow", "2026-10-28T00:00:00.000+00:00")]
    r = read_temporal("tomorrow", kind="date")
    assert (r.value.date, r.provenance.reference_source) == (date(2026, 10, 28), "system_clock")


def test_whitespace_and_final_punctuation_are_not_part_of_the_phrase(engine):
    _, _, calls = engine
    read("  next\tMonday. ")
    assert calls[0][0] == "next Monday"


def test_offsets_are_the_caller_s_text(engine):
    r = read_temporal("  next Monday ", context=NOW, origin_offset=10)
    assert (r.source.text, r.source.start, r.source.end) == ("next Monday", 12, 23)


@pytest.mark.parametrize("text", ["", "   ", "x" * 513])
def test_empty_and_too_long(engine, text):
    assert read(text).status == "not_temporal"


def test_duckling_down_is_an_error_not_a_status(monkeypatch):
    monkeypatch.setattr(reader, "DUCKLING_URL", "http://127.0.0.1:9/parse")
    with pytest.raises(EngineUnavailable):
        read_temporal("tomorrow", context=NOW)


def test_config_errors():
    with pytest.raises(TemporalConfigError):
        TemporalContext(reference_date=datetime(2026, 10, 26))
    with pytest.raises(TemporalConfigError):
        TemporalContext(date_order="DM")
    with pytest.raises(TemporalConfigError):
        read_temporal("today", kind="range")
    with pytest.raises(TemporalConfigError):
        read_temporal("26 October 2026", anchor="absolute", direction="past")
    with pytest.raises(TemporalConfigError):
        read_temporal("today", answers={"colour": "red"})


def test_bad_answers_are_refused(engine):
    answers = engine[0]
    answers[("03/04/2026", "en_US")] = [time_entity("03/04/2026", "2026-03-04T00:00:00.000+00:00")]
    answers[("03/04/2026", "en_GB")] = [time_entity("03/04/2026", "2026-04-03T00:00:00.000+00:00")]
    asked = read("03/04/2026")
    for bad in ("YDM", 1, None):
        with pytest.raises(TemporalConfigError):
            answer(asked, bad)
