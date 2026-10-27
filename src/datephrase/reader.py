"""Thin wrapper over dateparser: the backend reads the text; the caller's classification (kind, anchor, direction)
decides what shape the answer takes. Gaps are filled by named rules and recorded; only a choice with no evidence
either way (the day/month order of 01/02/2026) is asked."""

import re
from calendar import month_abbr, month_name, monthrange
from dataclasses import fields
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from importlib.metadata import version

from dateparser.date import DateDataParser
from dateutil.relativedelta import relativedelta
from text_to_num import alpha2digit

from . import forms
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
NUMERIC_PAIR = re.compile(  # 01/02, 26.10.2026, "01 02 2026", "1,2,2026": day/month order matters; not "1.5 years"
    r"(?<!\d)\d{1,2}(?:\s*[-/.,]\s*|\s+)\d{1,2}(?!\d)(?!\s*(?:second|minute|hour|day|week|fortnight|month|quarter|year)s?\b)",
    re.IGNORECASE)
CONTEXT_FIELDS = {f.name for f in fields(TemporalContext)}
SEARCH_LIMIT = 12 * 8  # months scanned for a next occurrence: covers February 29 (next leap year)
WEEKDAY = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues|tue|wed|thurs|thur|thu|fri|sat|sun"
# "next Monday" is not read by dateparser; "Monday" is, and "next" / "last" say which one
RELATIVE_WEEKDAY = re.compile(rf"(next|coming|this|last|previous) ((?:{WEEKDAY})\.?)", re.IGNORECASE)
WEEKDAY_DIRECTION = {"next": "future", "coming": "future", "this": None, "last": "past", "previous": "past"}
# Lengths dateparser does not read, said in the units it does. A quarter is 3 months (converted back, see _QUARTERS).
UNIT_WORD = r"(?:second|minute|hour|day|week|fortnight|month|quarter|year)s?\b"
COUNT = r"(?<![\d.])(\d+(?:\.\d+)?)"  # a whole count: never the "5" of "1.5"
LENGTH_WORDS = (
    (re.compile(r"\bhalf (?:a |an )?year\b", re.IGNORECASE), lambda m: "6 months"),
    (re.compile(r"\b(?:a )?couple(?: of)?\b", re.IGNORECASE), lambda m: "2"),
    (re.compile(rf"\b(?:a|an) (?={UNIT_WORD})", re.IGNORECASE), lambda m: "1 "),  # "a week" -> "1 week"
    (re.compile(rf"{COUNT} fortnights?\b", re.IGNORECASE), lambda m: f"{_times(m[1], 2)} weeks"),
    (re.compile(rf"{COUNT} quarters?\b(?![\s-]+of\b)", re.IGNORECASE), lambda m: f"{_times(m[1], 3)} months"),  # not "of an hour"
)
# A duration keeps the unit it was written in, down to seconds (dateparser reports them as part of a day)
TIME_UNITS = {"hour": 3_600_000_000, "minute": 60_000_000, "second": 1_000_000}
# dateparser reads "1.5 days" right, but takes "1.5 years" for January 5: fractional months and years are not read
FRACTIONAL_MONTHS = re.compile(r"\d*\.\d+ (?:month|quarter|year)s?\b", re.IGNORECASE)  # after unit spelling
# Units as people write them, attached or not ("90 mins", "1.50hours", "1.5-year", ".5 yr"), spelled out in full
UNIT_SPELLINGS = {
    "second": ("seconds", "second", "secs", "sec", "s"), "minute": ("minutes", "minute", "mins", "min", "m"),
    "hour": ("hours", "hour", "hrs", "hr", "h"), "day": ("days", "day", "d"),
    "week": ("weeks", "week", "wks", "wk", "w"), "fortnight": ("fortnights", "fortnight"),
    "month": ("months", "month", "mos", "mo"), "quarter": ("quarters", "quarter", "qtrs", "qtr"),
    "year": ("years", "year", "yrs", "yr", "y"),  # one letter as dateparser reads it: "90m" is minutes, "5y" years
}
UNIT_OF = {spelling: unit for unit, spellings in UNIT_SPELLINGS.items() for spelling in spellings}
NUMBER_AND_UNIT = re.compile(
    rf"(?<![\w.])(\d*\.\d+|\d+)\s*-?\s*({'|'.join(sorted(UNIT_OF, key=len, reverse=True))})\b", re.IGNORECASE)
TIME_UNIT = re.compile(r"\b(hour|minute|second)s?\b", re.IGNORECASE)
# "second" is a unit of time as well as an ordinal: text2num would turn "in a second" into "in a 2nd". It is read
# once the other numbers are digits: "seconds" is the unit; "second" is the unit right after a count ("a second",
# "2 second", "twenty two second" -> "22 second"), the ordinal otherwise ("the second", "second May"). After a
# round ten in a date ("October twenty second") it ends the ordinal: 22nd.
SECOND = re.compile(r"(?<!-)\bseconds?\b", re.IGNORECASE)  # "twenty-second" is left to text2num: 22nd
COUNTED = re.compile(r"(?:\b(?:a|an|per|each|every)|(?<![\w.])(\d*\.?\d+))\s*$", re.IGNORECASE)
MONTH_WORD = re.compile(rf"\b(?:{'|'.join(m.lower() for m in month_name[1:] + month_abbr[1:])})\b", re.IGNORECASE)
# One written day ("the 1st", "October 26th"): dateparser misreads some ("1st of the month" -> the 27th)
# not "the 2nd day from now", a count; but "the 2nd day of the month" is a day
ORDINAL_DAY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)\b(?!\s*{UNIT_WORD}(?!\s+of\b))", re.IGNORECASE)
# a duration is a run of "<number> <unit>": "3 days", "1 year 2 months", "3 weeks and 2 days", "2 hours ago"
DURATION_SHAPE = re.compile(
    r"(?:for )?(?:\d*\.?\d+ (?:second|minute|hour|day|week|month|year)s?(?:,? and |, | )?)+(?: ago)?", re.IGNORECASE)
_QUARTERS = re.compile(r"\bquarters?\b(?![\s-]+of\b)", re.IGNORECASE)


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
    normalized = " ".join(stripped.split())  # "a  week", "a\tweek"
    phrase, text_direction, quarters, too_big = "", None, False, False
    if len(stripped) <= MAX_INPUT_CHARS:  # measured on the text as given, before any rewrite or form
        try:
            phrase, text_direction, quarters = _rewrite(normalized, kind) if kind else (normalized, None, False)
        except (ValueError, OverflowError):  # a number too big to rewrite or to count in any unit
            too_big = True
    quarters = quarters and kind == "duration"  # said in quarters ("one quarter", "2 qtrs"): the answer stays so
    labelled, defaults, policies = direction, [], {}
    if text_direction:  # "last Friday": the text says which one ("this Friday" does not: the label decides)
        direction = text_direction
    elif anchor == "relative" and direction is None:  # decided 2026-10-27: a relative date without a label is future
        direction = "future"
        defaults.append("direction:future")

    settings = {"PREFER_LOCALE_DATE_ORDER": False}
    if YEAR_FIRST.match(phrase):
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
    filled, form = [], None

    def provenance():
        return Provenance(BACKEND, _jsonable(settings), ref if uses_ref else None, ref_source if uses_ref else None,
                          kind, anchor, labelled, tuple(filled), tuple(defaults), dict(policies),
                          phrase if phrase != stripped else None, form)

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

    if len(stripped) > MAX_INPUT_CHARS:  # before the forms too: they read nested periods ("last week of" * n)
        return done("not_temporal", reason="TOO_LONG")
    if too_big:
        return done("not_temporal", reason="OUT_OF_RANGE")
    if FRACTIONAL_MONTHS.search(phrase):
        return done("not_temporal", reason="FRACTION_NOT_SUPPORTED")

    # Quarters, numbered weeks, "year to date", weekends, "the end of the month": built on dateparser (forms.py)
    if kind != "duration":
        def read_period(t):
            r = read_temporal(t, context=ctx, kind="period", anchor=anchor, direction=labelled)
            if r.status != "resolved":
                return None
            policies.update(r.provenance.policies)  # what the period used is what this answer used
            defaults.extend(d for d in r.provenance.defaults if d not in defaults)
            filled.extend(p for p in r.provenance.filled if p not in filled)  # "beginning of October": its year
            return r.value
        try:
            found = forms.read(phrase, ref=ref, direction=direction, week_start=lambda: setting("week_start"),
                               read_period=read_period)
        except (ValueError, OverflowError):  # week 53 of a 52-week year, beyond year 9999
            return done("not_temporal", reason="OUT_OF_RANGE")
        if found:
            value, form, form_filled, form_relative = found
            if anchor == "absolute" and form_relative:  # "next quarter", "this weekend": counted from today
                return done("not_temporal", reason="ANCHOR_MISMATCH")
            filled += form_filled
            uses_ref = True
            if kind and value.kind != kind:
                return done("not_temporal", reason="KIND_MISMATCH")
            if labelled and value.kind == "date" and (value.date < ref if labelled == "future" else value.date > ref):
                return done("not_temporal", reason="DIRECTION_MISMATCH")
            return done("resolved", value)

    # A day alone ("the 1st", "the 15th of the month"): that day at its next occurrence (its latest one when past).
    # dateparser cannot be trusted with it: it reads "the 1st" ... "the 12th" as January ... December.
    if kind != "duration" and (day := _lone_day(phrase)):
        if kind == "period":
            return done("not_temporal", reason="KIND_MISMATCH")  # a day is not a period
        try:
            d = _fill({"day": day}, ref, past=direction == "past")
        except ValueError:  # no month has that day ("the 32nd")
            return done("not_temporal", reason="OUT_OF_RANGE")
        filled += ["year", "month"]
        uses_ref = True
        return done("resolved", DateValue(d, datetime.combine(d, time())))

    # Day/month order: keep only the readings whose day and month come from the text. "03/15" read day first makes
    # dateparser take 15 as a year and invent the month, so it is dropped. Two real, different readings are asked.
    if "DATE_ORDER" not in settings and kind != "duration" and NUMERIC_PAIR.search(phrase):
        real = [o for o in ("MDY", "DMY") if _written(phrase, settings | {"DATE_ORDER": o}, ("month", "day"))]
        readings = {o: _parse(phrase, settings | {"DATE_ORDER": o}) for o in real}
        if len({r.date_obj.date() for r in readings.values()}) > 1:
            options = [(o, _label(text, context, kind, anchor, labelled, answers, origin_offset, o)) for o in real]
            question = f"Does '{stripped}' mean {options[0][1]} or {options[1][1]}?"
            return TemporalReadResult("ask", source, provenance(), request, reason="DATE_ORDER_AMBIGUOUS",
                                      question=Question("DATE_ORDER_AMBIGUOUS", "date_order", question,
                                                        "choice", tuple(options)))
        if len(real) == 1:
            settings["DATE_ORDER"] = real[0]

    data = _parse(phrase, settings)
    if data is None:
        return done("not_temporal")
    moment = data.date_obj
    try:
        a, b = _probe(phrase, settings) if probing else (None, None)
        varying = [p for p in PARTS if a and b and getattr(a.date_obj, p) != getattr(b.date_obj, p)]
        if len(varying) == len(PARTS):  # "tomorrow" labelled absolute: the text gave no part of a date itself
            return done("not_temporal", reason="ANCHOR_MISMATCH", data=data)
        if missing := [p for p in REQUIRED_PARTS.get(kind, ()) if p in varying]:
            # decided 2026-10-27: a missing day is the 1st; a missing year or month is the next occurrence after
            # the reference date (March 15 on 2026-10-27 is 2027-03-15)
            known = {p: getattr(moment, p) for p in PARTS if p not in missing}
            if kind == "period":
                known["day"] = 1  # a period starts on the 1st: never the probe's day, which would move the cutoff
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
            if not DURATION_SHAPE.fullmatch(phrase):  # "tomorrow", "yesterday at 12:00": a date, not a length
                return done("not_temporal", reason="KIND_MISMATCH", data=data)
            value, reason = _duration(phrase, settings, data.period)
            if value is None:
                return done("not_temporal", reason=reason, data=data)
            if quarters and value.unit == "month" and value.quantity % 3 == 0:  # said in quarters: kept in quarters
                q = Fraction(value.quantity) / 3
                value = DurationValue(int(q) if q.denominator == 1 else float(q), "quarter")
        else:
            value = DateValue(d, moment)
            days = ORDINAL_DAY.findall(phrase)
            if len(days) == 1 and int(days[0]) != d.day:  # not the day the text wrote: never a wrong date
                return done("not_temporal", reason="DAY_MISREAD", data=data)
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


def _rewrite(text: str, kind: str | None) -> tuple[str, str | None, bool]:
    """The text in words dateparser reads, the direction it states (if any), and whether it counts in quarters.
    Numbers in words become digits (text2num: "October twenty-sixth" -> "October 26th")."""
    seconds = []
    def hold(m):
        seconds.append(m[0])
        return f" SECONDHELD{len(seconds) - 1} "
    text = " ".join(alpha2digit(SECOND.sub(hold, text), "en", threshold=0).split())
    for i, word in enumerate(seconds):  # now every count is a digit: decide what each "second" is
        held = f"SECONDHELD{i}"
        before = text[:text.index(held)]
        counted = COUNTED.search(before)
        if word.lower() == "seconds" or (counted and not (kind != "duration" and counted[1] and _round_ten(counted[1]))):
            text = text.replace(held, word, 1)  # the unit: "a second", "2 seconds"
        elif counted and counted[1]:  # "October 20 second" -> "October 22nd": a round ten, then the ordinal
            n = int(counted[1]) + 2
            text = text[:counted.start(1)] + f"{n}nd" + text[counted.end(1):].replace(f" {held}", "", 1)
        else:
            text = text.replace(held, "2nd", 1)  # the ordinal: "the second", "second May"
    text = NUMBER_AND_UNIT.sub(_spell_unit, text)
    quarters = bool(_QUARTERS.search(text))  # "1 quarter", "2 quarters" (spelled out), not "a quarter of an hour"
    for pattern, said in LENGTH_WORDS:
        text = pattern.sub(said, text)
    if m := RELATIVE_WEEKDAY.fullmatch(text):
        return m[2], WEEKDAY_DIRECTION[m[1].lower()], quarters
    return text, None, quarters


def _round_ten(number: str) -> bool:
    return number.isdigit() and int(number) in range(20, 100, 10)


def _today() -> date:
    """Today's date: the reference when the session gives none (decided 2026-10-27). Tests replace it."""
    return date.today()


def _parse(text, settings):
    if not text or len(text) > MAX_INPUT_CHARS:
        return None
    try:
        data = DateDataParser(languages=["en"], settings=settings).get_date_data(text)
    except (ValueError, OverflowError):  # a number too big for any calendar
        return None
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


def _fill(known: dict, ref: date, past: bool = False) -> date:
    """The date with the known parts; a missing day is the 1st, a missing year or month the next occurrence after ref
    (with past=True, the latest one on or before ref)."""
    day = known.get("day", 1)
    if "year" in known and "month" in known:
        return date(known["year"], known["month"], day)
    first = ref.year * 12 + ref.month - 1
    for n in range(0, -SEARCH_LIMIT - 1, -1) if past else range(SEARCH_LIMIT + 1):
        y, m0 = divmod(first + n, 12)
        if known.get("year", y) != y or known.get("month", m0 + 1) != m0 + 1 or day > monthrange(y, m0 + 1)[1]:
            continue
        candidate = date(y, m0 + 1, day)
        if (candidate <= ref) if past else (candidate > ref):
            return candidate
    raise ValueError(f"no occurrence of {known} after {ref}")


def _spell_unit(m) -> str:
    number = "0" + m[1] if m[1].startswith(".") else m[1]
    unit = UNIT_OF[m[2].lower()]
    return f"{number} {unit if Fraction(number) == 1 else unit + 's'}"


def _lone_day(phrase: str) -> int | None:
    """The day of a phrase whose only number is one ordinal day and that names no month: "the 1st", "the 15th"."""
    days = ORDINAL_DAY.findall(phrase)
    if len(days) == 1 and len(re.findall(r"\d+", phrase)) == 1 and not MONTH_WORD.search(phrase):
        return int(days[0])
    return None


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
        if data and data.date_obj > base:  # "tomorrow at 1:00" is a moment ahead, not a length ("3 days" reads back)
            return None, "KIND_MISMATCH"
        quantities.append(_measure(base, data.date_obj, precision) if data else None)
    if None in quantities:
        return None, "KIND_MISMATCH"
    if quantities[0] != quantities[1]:
        return None, "MIXED_UNITS"  # e.g. months and days together: no single unit holds it exactly
    q, unit = quantities[0], precision
    if precision == "day" and (written := TIME_UNIT.findall(text)):  # "90 minutes": kept in minutes, not days
        unit = min((u.lower() for u in written), key=TIME_UNITS.get)
        q = q * Fraction(86_400_000_000, TIME_UNITS[unit])
    return DurationValue(int(q) if q.denominator == 1 else float(q), unit), None


def _times(count: str, n: int) -> str:
    q = Fraction(count) * n
    return str(int(q)) if q.denominator == 1 else str(float(q))


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
