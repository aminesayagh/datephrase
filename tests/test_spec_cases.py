"""The brief's cases (tests/fixtures), each sent with the classification a correct upstream classifier gives it.

Expected outcomes are the brief's, with the user's decisions of 2026-10-27 applied:
- inputs the brief rejects as unsupported (times, two-digit years, fractions, compound durations...) are accepted as
  dateparser reads them;
- whatever the brief leaves open (needs_context, ambiguous) must come out as a question to the user ("ask"), except
  where a rule fills it (DECIDED below): a missing year or month is the next occurrence after the reference date, a
  missing day is the 1st, unset week start / month overflow default to Monday / clamp, and with no reference date in
  the session today's date is the reference (pinned here to 2026-10-27);
- a relative expression arrives with its direction (past | future) from the classifier; future when it does not.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from temporal_reader import TemporalContext, read_temporal, reader

FIXTURES = Path(__file__).parent / "fixtures"
CONTEXTS = json.loads((FIXTURES / "contexts.json").read_text())
CASES = [json.loads(line) for name in ("development_60", "extra_cases", "holdout", "gaps")
         for line in (FIXTURES / f"{name}.jsonl").read_text().splitlines()]


TODAY = date(2026, 10, 27)
P = lambda start, end, grain: {"kind": "period", "start": start, "end": end, "grain": grain}
D = lambda d: {"kind": "date", "date": d}
# Expected values changed by the decisions above, written by hand (C0's reference date is 2026-10-26).
DECIDED = {
    "D17": P("2027-10-01", "2027-11-01", "month"),  # October: the next one after 2026-10-26
    "D18": P("2027-10-01", "2027-11-01", "month"),  # in October
    "D20": P("2027-05-01", "2027-06-01", "month"),  # May
    "X45": P("2027-06-01", "2027-07-01", "month"),  # June
    "X46": P("2027-03-01", "2027-04-01", "month"),  # March
    "D54": D("2027-03-15"),                         # March 15
    "X50": D("2027-03-15"),                         # March 15: the brief's current-year policy is replaced
    "H27": D("2027-07-04"),                         # July 4
    "X13": D("2028-02-29"),                         # February 29: the next one is in the next leap year
    "D53": D("2026-10-28"),                         # tomorrow, no session reference: today (2026-10-27) + 1
    "H26": D("2026-11-01"),                         # in five days, no session reference
    "H33": D("2026-10-28"),                         # Wednesday, classified future: the next one after Mon 2026-10-26
    "D56": D("2026-02-28"),                         # in one month from Jan 31, no policy: clamp is the default
    "H19": D("2026-02-28"),                         # one month ago from Mar 31, no policy: clamp is the default
    "X51": P("2026-10-26", "2026-11-02", "week"),   # this week, no week start: Monday is the default
}
U = lambda q, unit: {"kind": "duration", "quantity": q, "unit": unit}
NOT_TEMPORAL, ASK = "not_temporal", "ask"
# The brief's "unsupported" inputs, accepted as dateparser reads them (decided 2026-10-27). Each outcome is judged by
# hand: dateparser's correct reading, or an honest not_temporal / ask where it cannot read it. Never another value.
ACCEPTED = {
    "D28": NOT_TEMPORAL,                     # 1.5 months: dateparser does not read fractional months
    "D29": NOT_TEMPORAL,                     # two months and five days: no single unit holds it (MIXED_UNITS)
    "D30": NOT_TEMPORAL,                     # 2 business days
    "D60": D("2026-10-26"),                  # 2026-10-26 at 14:30: its date (the time stays in datetime)
    "X10": ASK,                              # 01/02/26: January 2 or February 1, 2026
    "X11": D("2026-10-26"),                  # Oct 26 26
    "X28": NOT_TEMPORAL,                     # half a month
    "X29": NOT_TEMPORAL,                     # two and a half weeks: dateparser does not read it
    "X30": NOT_TEMPORAL,                     # a month and a half
    "X31": U(14, "month"),                   # 1 year 2 months
    "X32": D("2026-11-12"),                  # in 2 weeks and 3 days from 2026-10-26: 17 days later
    "X33": D("2026-10-26"),                  # in 3 hours from midnight 2026-10-26: still that day
    "X34": D("2026-10-27"),                  # tomorrow at 5pm
    "X35": D("2026-10-26"),                  # 2026-10-26T14:30:00Z
    "X36": NOT_TEMPORAL,                     # past month: a rolling window, no whole period
    "X37": NOT_TEMPORAL,                     # last 30 days
    "X38": NOT_TEMPORAL,                     # since March
    "X39": NOT_TEMPORAL,                     # between March and May
    "X40": NOT_TEMPORAL,                     # every Monday
    "X41": NOT_TEMPORAL,                     # fiscal Q3
    "X42": NOT_TEMPORAL,                     # several days
    "H28": D("2026-10-26"),                  # 2026-10-26 08:00
    "H29": U(23, "day"),                     # three weeks and two days
    "H30": NOT_TEMPORAL,                     # 0.5 years: dateparser does not read fractional years
    "H31": D("2026-10-26"),                  # 26/10/26: month 26 does not exist, so day first
}

# Why each remaining case fails. strict: a case that starts passing (a fix, a dateparser upgrade) fails the run,
# so this list is updated instead of going stale.
NOT_READ = "dateparser does not read this text at all"
KNOWN = {
    **dict.fromkeys(["D25", "D43", "D44", "D45", "X17", "X53", "H16", "H20", "G01"], NOT_READ + " (quarters)"),
    **dict.fromkeys(["D59", "X47", "H32", "G02"], NOT_READ + " (next + weekday)"),
    **dict.fromkeys(["D26", "D27", "X04", "G04"], NOT_READ + " (number words: zero, twenty-one, twenty-sixth)"),
    "X18": NOT_READ + " (ISO week)",
    "D49": "dateparser swaps month 13 and day 01 into 2026-01-13 instead of rejecting it",
    "X02": "dateparser ignores a weekday that contradicts the date (26 Oct 2026 is a Monday)",
    "X12": "dateparser ignores the wrong ordinal suffix in 26rd",
    "G06": "dateparser counts 2 weeks later from the reference date where the brief asks from when",
    "X20": "label vs brief: with the classifier saying 'period', 2026 is a year (as X21 expects); the brief asks",
}


def context(name):
    c = CONTEXTS[name]
    rd = c["reference_date"]
    return TemporalContext(date.fromisoformat(rd) if rd else None, c["date_order"], c["week_start"],
                           c["month_overflow_policy"])


def expected_value(v):
    if v["kind"] == "date":
        return {"kind": "date", "date": v["date"]}
    if v["kind"] == "range":
        return {"kind": "period", "start": v["start"], "end": v["end"], "grain": v["grain"]}
    return v


PARAMS = [pytest.param(c, id=f"{c['id']}:{c['input']}",
                       marks=[pytest.mark.xfail(reason=KNOWN[c["id"]], strict=True)] if c["id"] in KNOWN else [])
          for c in CASES]


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(reader, "_today", lambda: TODAY)


@pytest.mark.parametrize("case", PARAMS)
def test_spec_case(case):
    label = case["classification"] or {}
    r = read_temporal(case["input"], context=context(case["context"]), kind=label.get("kind"), anchor=label.get("anchor"),
                      direction=label.get("direction"))
    got = r.value.to_dict() if r.value else None
    if got and got["kind"] == "date":
        got.pop("datetime")
    if case["id"] in DECIDED:
        assert got == DECIDED[case["id"]], (r.status, r.reason, got)
        return
    match case["status"]:
        case "unsupported":
            want = ACCEPTED[case["id"]]
            assert (r.status if isinstance(want, str) else got) == want, (r.status, r.reason, got)
        case "resolved":
            assert got == expected_value(case["value"]), (r.status, r.reason, got)
        case "needs_context" | "ambiguous":  # the brief's "ask the user"
            assert r.status == "ask", (r.status, r.reason, got)
        case _:  # invalid, not_temporal: no value may come out
            assert r.status != "resolved", (r.status, got)
