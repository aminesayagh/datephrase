"""Thin wrapper over dateparser: the backend reads the text; the caller's classification (kind, anchor, direction)
decides what shape the answer takes. Gaps are filled by named rules and recorded; only a choice with no evidence
either way (the day/month order of 01/02/2026) is asked."""

import re
from calendar import monthrange
from dataclasses import fields
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from importlib.metadata import version

from dateparser.date import DateDataParser
from dateutil.relativedelta import relativedelta

from .models import (
    ANCHORS,
    DIRECTIONS,
    KINDS,
    DateValue,
    DurationValue,
    PeriodValue,
    Provenance,
    Question,
    Request,
    Source,
    TemporalConfigError,
    TemporalContext,
    TemporalReadResult,
)

MAX_INPUT_CHARS = 512  # ponytail: guards dateparser's regexes against huge input; raise if real spans need it
BACKEND = f"dateparser {version('dateparser')}"
# The parts an absolute expression of each kind needs. Missing ones are filled by rule, see _fill.
REQUIRED_PARTS = {"date": ["year", "month", "day"], "period": ["year"]}
PARTS = ("year", "month", "day")
# Which parts did the text give? Parse it against two unrelated (leap-year) bases: a part that changes with the base
# was filled in by dateparser, not written. (dateparser's own REQUIRE_PARTS can misread: "March 15" -> March 2015.)
PROBE_BASES = (datetime(2004, 3, 10), datetime(2012, 8, 23))
# dateparser reads "3 months" as a date that long ago. A duration is measured back from two fixed dates, never the
# session's: a true quantity is the same from both; "two months and five days" is not (66 days, then 64).
DURATION_BASES = (datetime(2000, 1, 15), datetime(2001, 3, 20))
# Proposed defaults (2026-10-27) for settings the session leaves unset; applied ones are listed in provenance.defaults.
DEFAULTS = {"week_start": "monday", "month_overflow": "clamp"}  # ISO 8601 weeks; dateparser's own month arithmetic
WEEK_START_DAY = {"monday": 0, "sunday": 6}
YEAR_FIRST = re.compile(r"\d{4}[-/.]")  # 2026-02-01: its shape fixes the order, no date_order applies
NUMERIC_PAIR = re.compile(r"\d{1,2}\s*[-/.]\s*\d{1,2}")  # 01/02, 26.10.2026: where day/month order matters
CONTEXT_FIELDS = {f.name for f in fields(TemporalContext)}
SEARCH_LIMIT = 12 * 8  # months scanned for a next occurrence: covers February 29 (next leap year)


def read_temporal(
    text: str,
    *,
    context: TemporalContext | None = None,
    kind: str | None = None,
    anchor: str | None = None,
    direction: str | None = None,
    answers: dict | None = None,
    origin_offset: int = 0,
) -> TemporalReadResult:
    """Read one candidate expression (not a whole prompt) with dateparser, English only.

    kind (date | period | duration), anchor (absolute | relative) and, for a relative one, direction
    (past | future; future when not sent) come from the upstream classifier.
    answers holds the user's replies to earlier questions; `answer()` fills it for you."""
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    if context is None:
        context = TemporalContext()
    if not isinstance(context, TemporalContext):
        raise TemporalConfigError("context must be a TemporalContext (use TemporalContext.from_dict for JSON)")
    if kind not in (None, *KINDS):
        raise TemporalConfigError(f"kind must be one of {KINDS} or None, got {kind!r}")
    if anchor not in (None, *ANCHORS):
        raise TemporalConfigError(f"anchor must be one of {ANCHORS} or None, got {anchor!r}")
    if direction not in (None, *DIRECTIONS):
        raise TemporalConfigError(f"direction must be one of {DIRECTIONS} or None, got {direction!r}")
    if direction and anchor != "relative":
        raise TemporalConfigError("direction is a label of relative expressions: send it with anchor='relative'")
    if type(origin_offset) is not int or origin_offset < 0:
        raise TemporalConfigError("origin_offset must be a nonnegative int")
    answers = dict(answers or {})
    if unknown := answers.keys() - CONTEXT_FIELDS:
        raise TemporalConfigError(f"unknown answer fields: {sorted(unknown)}")

    request = Request(text, context, kind, anchor, answers, origin_offset, direction)
    ctx = TemporalContext.from_dict(context.to_dict() | answers)  # validates the replies like any setting
    stripped = text.strip()
    start = origin_offset + len(text) - len(text.lstrip())
    source = Source(stripped, start, start + len(stripped))
    ref, ref_source = (ctx.reference_date, "session") if ctx.reference_date else (_today(), "system_clock")
    labelled, defaults, policies = direction, [], {}
    if anchor == "relative" and direction is None:  # decided 2026-10-27: a relative date without a label is future
        direction = "future"
        defaults.append("direction:future")

    settings = {"PREFER_LOCALE_DATE_ORDER": False}
    if YEAR_FIRST.match(stripped):
        settings["DATE_ORDER"] = "YMD"
    elif ctx.date_order:
        settings["DATE_ORDER"] = ctx.date_order
    if direction:
        settings["PREFER_DATES_FROM"] = direction
    probing = anchor == "absolute" and kind in REQUIRED_PARTS
    if kind == "duration":
        settings["RELATIVE_BASE"] = DURATION_BASES[0]
    elif probing:
        settings["RELATIVE_BASE"] = PROBE_BASES[0]
    else:
        settings["RELATIVE_BASE"] = datetime.combine(ref, time())
    uses_ref = kind != "duration" and not probing
    filled = []

    def provenance():
        return Provenance(BACKEND, _jsonable(settings), ref if uses_ref else None, ref_source if uses_ref else None,
                          kind, anchor, labelled, tuple(filled), tuple(defaults), dict(policies))

    def done(status, value=None, reason=None, data=None):
        return TemporalReadResult(status, source, provenance(), request, value=value, reason=reason,
                                  period=data and data.period, locale=data and data.locale)

    def setting(name):
        if value := getattr(ctx, name):
            policies[name] = value
            return value
        defaults.append(f"{name}:{DEFAULTS[name]}")
        policies[name] = DEFAULTS[name]
        return DEFAULTS[name]

    # Day/month order: keep only the readings whose day and month come from the text. "03/15" read day first makes
    # dateparser take 15 as a year and invent the month, so it is dropped. Two real, different readings are asked.
    if "DATE_ORDER" not in settings and NUMERIC_PAIR.search(stripped):
        real = [o for o in ("MDY", "DMY") if _written(stripped, settings | {"DATE_ORDER": o}, ("month", "day"))]
        readings = {o: _parse(stripped, settings | {"DATE_ORDER": o}) for o in real}
        if len({r.date_obj.date() for r in readings.values()}) > 1:
            options = [(o, _label(text, context, kind, anchor, labelled, answers, origin_offset, o)) for o in real]
            question = f"Does '{stripped}' mean {options[0][1]} or {options[1][1]}?"
            return TemporalReadResult("ask", source, provenance(), request, reason="DATE_ORDER_AMBIGUOUS",
                                      question=Question("DATE_ORDER_AMBIGUOUS", "date_order", question,
                                                        "choice", tuple(options)))
        if len(real) == 1:
            settings["DATE_ORDER"] = real[0]

    data = _parse(stripped, settings)
    if data is None:
        return done("not_temporal")
    moment = data.date_obj
    try:
        if probing and (missing := _missing(stripped, settings, REQUIRED_PARTS[kind])):
            # decided 2026-10-27: a missing day is the 1st; a missing year or month is the next occurrence after
            # the reference date (March 15 on 2026-10-27 is 2027-03-15)
            known = {p: getattr(moment, p) for p in PARTS if p not in missing}
            if kind == "period":
                known.setdefault("day", 1)
            moment = datetime.combine(_fill(known, ref), moment.time())
            filled += missing
            uses_ref = any(p != "day" for p in missing)

        d = moment.date()
        if anchor == "relative" and kind == "date" and data.period in ("month", "year") and d.day < ref.day \
                and d.day == monthrange(d.year, d.month)[1]:  # dateparser moved the day back: the month is too short
            if setting("month_overflow") == "reject":
                return done("not_temporal", reason="MONTH_OVERFLOW_REJECTED", data=data)
        if kind == "period":
            value = _whole_period(d, data.period, setting("week_start") if data.period == "week" else None)
        elif kind == "duration":
            value, reason = _duration(stripped, settings, data.period)
            if value is None:
                return done("not_temporal", reason=reason, data=data)
        else:
            value = DateValue(d, moment)
    except (ValueError, OverflowError):  # beyond year 1..9999
        return done("not_temporal", reason="OUT_OF_RANGE", data=data)
    if value is None:  # the classification and dateparser's precision disagree: never forced
        return done("not_temporal", reason="KIND_MISMATCH", data=data)
    if labelled and kind == "date" and (d < ref if labelled == "future" else d > ref):
        return done("not_temporal", reason="DIRECTION_MISMATCH", data=data)  # the label contradicts the text
    return done("resolved", value, data=data)


def answer(asked: TemporalReadResult | dict, value) -> TemporalReadResult:
    """Read the asked text again with the user's reply. `asked` is the ask result, or its to_dict() form."""
    if isinstance(asked, TemporalReadResult):
        asked = asked.to_dict()
    if asked.get("status") != "ask":
        raise TemporalConfigError("only an 'ask' result can be answered")
    q, req = asked["question"], asked["request"]
    if value not in {o["value"] for o in q["options"]}:
        raise TemporalConfigError(f"answer must be one of {[o['value'] for o in q['options']]}, got {value!r}")
    return read_temporal(req["text"], context=TemporalContext.from_dict(req["context"]), kind=req["kind"],
                         anchor=req["anchor"], direction=req["direction"], answers=req["answers"] | {q["field"]: value},
                         origin_offset=req["origin_offset"])


def _today() -> date:
    """Today's date: the reference when the session gives none (decided 2026-10-27). Tests replace it."""
    return date.today()


def _parse(text, settings):
    if not text or len(text) > MAX_INPUT_CHARS:
        return None
    data = DateDataParser(languages=["en"], settings=settings).get_date_data(text)
    return data if data.date_obj else None


def _probe(text, settings):
    return [_parse(text, settings | {"RELATIVE_BASE": base}) for base in PROBE_BASES]


def _missing(text, settings, parts) -> list[str]:
    a, b = _probe(text, settings)
    return [p for p in parts if a and b and getattr(a.date_obj, p) != getattr(b.date_obj, p)]


def _written(text, settings, parts) -> bool:
    """Did the text itself give these parts? (Readable, and the same under both probe bases.)"""
    a, b = _probe(text, settings)
    return bool(a and b) and all(getattr(a.date_obj, p) == getattr(b.date_obj, p) for p in parts)


def _fill(known: dict, ref: date) -> date:
    """The date with the known parts; a missing day is the 1st, a missing year or month the next occurrence after ref."""
    day = known.get("day", 1)
    if "year" in known and "month" in known:
        return date(known["year"], known["month"], day)
    first = ref.year * 12 + ref.month - 1
    for n in range(SEARCH_LIMIT + 1):
        y, m0 = divmod(first + n, 12)
        if known.get("year", y) != y or known.get("month", m0 + 1) != m0 + 1 or day > monthrange(y, m0 + 1)[1]:
            continue
        if (candidate := date(y, m0 + 1, day)) > ref:
            return candidate
    raise ValueError(f"no occurrence of {known} after {ref}")


def _label(text, context, kind, anchor, direction, answers, origin_offset, order) -> str:
    """How one day/month reading reads to the user, after every other rule (next occurrence...) is applied."""
    r = read_temporal(text, context=context, kind=kind, anchor=anchor, direction=direction,
                      answers=answers | {"date_order": order}, origin_offset=origin_offset)
    if isinstance(r.value, DateValue):
        return _say(r.value.date)
    if isinstance(r.value, PeriodValue):
        return f"{_say(r.value.start)} to {_say(r.value.end - timedelta(days=1))}"
    return f"{'month' if order == 'MDY' else 'day'} first"


def _say(d: date, day: bool = True) -> str:
    """A date as the user reads it: "February 1, 2026" (not strftime: that follows the host locale)."""
    month = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December")[d.month - 1]
    return f"{month} {d.day}, {d.year}" if day else f"{month} {d.year}"


def _whole_period(d: date, precision: str | None, week_start: str | None) -> PeriodValue | None:
    """The whole calendar period dateparser's answer lies in, at the precision dateparser reported."""
    if precision == "year":
        return PeriodValue(date(d.year, 1, 1), date(d.year + 1, 1, 1), "year")
    if precision == "month":
        start = d.replace(day=1)
        return PeriodValue(start, start + relativedelta(months=1), "month")
    if precision == "week":
        start = d - timedelta(days=(d.weekday() - WEEK_START_DAY[week_start]) % 7)
        return PeriodValue(start, start + timedelta(days=7), "week")
    return None


def _duration(text, settings, precision) -> tuple[DurationValue | None, str | None]:
    """The quantity dateparser's reading spans, in the unit it reported; the same from both bases or nothing."""
    quantities = []
    for base in DURATION_BASES:
        data = _parse(text, settings | {"RELATIVE_BASE": base})
        quantities.append(_measure(base, data.date_obj, precision) if data else None)
    if None in quantities:
        return None, "KIND_MISMATCH"
    if quantities[0] != quantities[1]:
        return None, "MIXED_UNITS"  # e.g. months and days together: no single unit holds it exactly
    q = quantities[0]
    return DurationValue(int(q) if q.denominator == 1 else float(q), precision), None


def _measure(base: datetime, moment: datetime, precision: str | None) -> Fraction | None:
    a, b = sorted((moment, base))
    micro = (b - a) // timedelta(microseconds=1)
    if precision == "day":
        return Fraction(micro, 86_400_000_000)  # exact: 2.5 days stays 2.5
    if precision == "week":
        return Fraction(micro, 7 * 86_400_000_000)
    rd = relativedelta(b, a)
    if (rd.days, rd.hours, rd.minutes, rd.seconds, rd.microseconds) != (0, 0, 0, 0, 0):
        return None
    months = rd.years * 12 + rd.months
    if precision == "month":
        return Fraction(months)
    if precision == "year" and months % 12 == 0:
        return Fraction(months // 12)
    return None


def _jsonable(settings: dict) -> dict:
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in settings.items()}
