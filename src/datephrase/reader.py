"""A contract layer over Duckling (https://github.com/facebook/duckling): Duckling reads the phrase; this module
only shapes its answer the way the caller's labels ask, refuses what does not fit, asks when the day/month order is
genuinely open, and records how the answer was obtained. It parses nothing itself."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import fields
from datetime import date, datetime, time, timedelta, timezone

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

MAX_INPUT_CHARS = 512  # one phrase, not a document
DUCKLING_URL = os.environ.get("DATEPHRASE_DUCKLING_URL", "http://127.0.0.1:8000/parse")
LOCALES = {"MDY": "en_US", "DMY": "en_GB", "YMD": "en_US"}  # year-first dates read alike in both
GRAINS = ("week", "month", "quarter", "year")  # a time at one of these grains is a whole period
MONTHS_IN = {"month": 1, "quarter": 3, "year": 12}
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")  # not strftime: that follows the host's locale
# Two unrelated reference dates: a value that changes in year, month and day between them names no date of its own
PROBES = (datetime(2004, 3, 10, tzinfo=timezone.utc), datetime(2012, 8, 23, tzinfo=timezone.utc))
CONTEXT_FIELDS = {f.name for f in fields(TemporalContext)}


class EngineUnavailable(RuntimeError):
    """Duckling could not be reached: a deployment problem, never a statement about the text."""


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
    """Read one phrase holding one date (the caller's promise) through Duckling.

    kind (date | period | duration), anchor (absolute | relative) and, for a relative one, direction (past | future)
    are the caller's classification. answers holds the user's replies to earlier questions (`answer()` fills it)."""
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
    ctx = TemporalContext.from_dict(context.to_dict() | answers)
    stripped = text.strip()
    start = origin_offset + len(text) - len(text.lstrip())
    source = Source(stripped, start, start + len(stripped))
    phrase = " ".join(stripped.split()).rstrip(".,;")
    ref_date, ref_source = (ctx.reference_date, "session") if ctx.reference_date else (_today(), "system_clock")
    ref = datetime.combine(ref_date, time(), tzinfo=timezone.utc)
    dims = ["duration"] if kind == "duration" else ["time"] if kind else ["time", "duration"]
    locales = [LOCALES[ctx.date_order]] if ctx.date_order else ["en_US", "en_GB"]
    defaults, policies, read = [], {}, {}

    def provenance():
        return Provenance("duckling", {"url": DUCKLING_URL, "locales": locales, "dims": dims, "tz": "UTC"},
                          ref_date, ref_source, kind, anchor, direction, tuple(defaults), dict(policies),
                          read.get("body"), read.get("engine", {}))

    def done(status, value=None, reason=None):
        return TemporalReadResult(status, source, provenance(), request, value=value, reason=reason)

    if not phrase:
        return done("not_temporal")
    if len(stripped) > MAX_INPUT_CHARS:
        return done("not_temporal", reason="TOO_LONG")

    # Each locale's reading of the WHOLE phrase. Duckling also reads parts ("the year 2027" as "the year"): a part
    # is not the phrase, so it never counts.
    readings = {loc: whole for loc in locales if (whole := _whole(_duckling(phrase, loc, dims, ref), phrase))}
    if not readings:
        return done("not_temporal")
    values = {loc: _shape(e, kind) for loc, e in readings.items()}
    if len(values) == 2 and _key(values["en_US"]) != _key(values["en_GB"]):
        usable = {loc: v for loc, v in values.items() if v and _fits(v, direction, ref_date)}
        if len(usable) == 2:  # day/month order genuinely open: the only question asked
            options = (("MDY", _say(usable["en_US"])), ("DMY", _say(usable["en_GB"])))
            return TemporalReadResult("ask", source, provenance(), request, reason="DATE_ORDER_AMBIGUOUS",
                                      question=Question("DATE_ORDER_AMBIGUOUS", "date_order",
                                                        f"Does '{stripped}' mean {options[0][1]} or {options[1][1]}?",
                                                        "choice", options))
        if usable:
            values = usable
    locale = next(iter(values))
    entity, value = readings[locale], values[locale]
    read.update(body=entity["body"], engine={"dim": entity["dim"], "locale": locale,
                                             "type": entity["value"].get("type"), "grain": entity["value"].get("grain")})
    if value is None:  # Duckling's answer is not of the kind asked: never forced
        return done("not_temporal", reason="KIND_MISMATCH")

    if isinstance(value, PeriodValue) and value.grain == "week":
        if ctx.week_start:
            policies["week_start"] = ctx.week_start
        else:
            defaults.append("week_start:monday")
        if ctx.week_start == "sunday":  # Duckling's weeks start on Monday
            value = PeriodValue(value.start - timedelta(days=1), value.end - timedelta(days=1), "week")
    if not _fits(value, direction, ref_date):
        return done("not_temporal", reason="DIRECTION_MISMATCH")
    if anchor == "absolute" and not isinstance(value, DurationValue) and _counted_from_today(phrase, locale, dims, kind):
        return done("not_temporal", reason="ANCHOR_MISMATCH")
    return done("resolved", value)


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
    """Today's date: the reference when the session gives none. Tests replace it."""
    return date.today()


def _duckling(text: str, locale: str, dims: list[str], ref: datetime) -> list[dict]:
    body = urllib.parse.urlencode({"locale": locale, "text": text, "tz": "UTC", "dims": json.dumps(dims),
                                   "reftime": int(ref.timestamp() * 1000)}).encode()
    try:
        with urllib.request.urlopen(DUCKLING_URL, body, timeout=10) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise EngineUnavailable(f"Duckling is not reachable at {DUCKLING_URL}: {e}") from e


def _whole(entities: list[dict], phrase: str) -> dict | None:
    """Duckling's reading of the whole phrase, if it has one (not latent)."""
    whole = [e for e in entities if e["start"] == 0 and e["end"] == len(phrase) and not e.get("latent")]
    return whole[0] if whole else None


def _shape(entity: dict, kind: str | None):
    """Duckling's value in the shape the label asks for, or None when it is of another kind."""
    v = entity["value"]
    if entity["dim"] == "duration":
        return DurationValue(v["value"], v["unit"]) if kind in (None, "duration") else None
    if kind == "duration":
        return None
    if v.get("type") == "interval":
        start, end = v.get("from"), v.get("to")
        if not (start and end) or kind == "date":
            return None
        return PeriodValue(date.fromisoformat(start["value"][:10]), date.fromisoformat(end["value"][:10]), "interval")
    moment = datetime.fromisoformat(v["value"]).replace(tzinfo=None)
    grain = v["grain"]
    if grain in GRAINS:
        if kind == "date":  # Duckling says it only knows the week, month, quarter or year ("in one year"): not a day
            return None
        d = moment.date()
        end = d + timedelta(days=7) if grain == "week" else d + relativedelta(months=MONTHS_IN[grain])
        return PeriodValue(d, end, grain)
    if kind == "period":
        return None
    return DateValue(moment.date(), moment)


def _key(value):
    return None if value is None else value.to_dict()


def _fits(value, direction: str | None, ref: date) -> bool:
    """Does the value lie on the side of the reference date the direction label says? (No label: anything fits.)"""
    if direction is None or isinstance(value, DurationValue):
        return True
    if isinstance(value, DateValue):
        return value.date >= ref if direction == "future" else value.date <= ref
    return value.end > ref if direction == "future" else value.start <= ref


def _counted_from_today(phrase: str, locale: str, dims: list[str], kind: str | None) -> bool:
    """Read against two unrelated reference dates: if year, month and day all move, the phrase names no date itself
    ("tomorrow", "next quarter"), so it cannot be absolute. ("March 15" only moves its year: it stays absolute.)"""
    probes = []
    for ref in PROBES:
        entity = _whole(_duckling(phrase, locale, dims, ref), phrase)
        value = entity and _shape(entity, kind)
        if value is None:
            return False
        probes.append(value.date if isinstance(value, DateValue) else value.start)
    a, b = probes
    return a.year != b.year and a.month != b.month and a.day != b.day


def _say(value) -> str:
    def day(d):
        return f"{MONTH_NAMES[d.month - 1]} {d.day}, {d.year}"
    if isinstance(value, DateValue):
        return day(value.date)
    if isinstance(value, PeriodValue):
        return f"{day(value.start)} to {day(value.end - timedelta(days=1))}"
    return f"{value.quantity} {value.unit}"
