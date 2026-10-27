"""300 short texts, each holding exactly one date expression, with the expected value.

Expected values come from plain calendar arithmetic below (an oracle independent of every engine benchmarked).
Reference date: Tuesday 2026-10-27, UTC. Conventions (the library owner's decisions):
- weeks start on Monday; a period is [start, end) with end excluded;
- "next/last <weekday>" is the first such weekday strictly after / before the reference date;
- "on <weekday>" is the next one (the future);
- a date without its year is its next occurrence after the reference date;
- a numeric date that both day/month orders read differently is ambiguous: the right answer is to ask, not a value;
- a duration is compared as a length: months for month/quarter/year units, else seconds;
- "next Wednesday" said on a Tuesday is this week's Wednesday (the first after today); the Wednesday of next week is
  recorded as "alternative", a different convention, counted apart from errors. So are Duckling's weekend (from
  Friday 18:00) and this year's already-past date for a day without its year (against the next occurrence).

    python benchmarks/engines/cases.py   # writes benchmarks/engines/cases.jsonl
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

from dateutil.relativedelta import relativedelta

REF = date(2026, 10, 27)  # a Tuesday
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
         10: "ten", 12: "twelve", 14: "fourteen", 21: "twenty-one", 30: "thirty"}
SENTENCES = ["Ship the order {x}.", "The invoice is dated {x}.", "Remind me {x}.", "Show the sales from {x}.",
             "Set the delivery date to {x}.", "Move the meeting to {x}.", "The contract started {x}.",
             "List the returns from {x}.", "Book the truck for {x}.", "We restock {x}."]
FOR = ["Keep the item for {x}.", "The trial lasts {x}.", "Hold the stock for {x}.", "Rent the van for {x}.",
       "The warranty covers {x}."]
rng = random.Random(7)
cases = []


def ordinal(n):
    return f"{n}{'th' if 11 <= n <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def D(d):
    return {"kind": "date", "date": d.isoformat()}


def P(start, end):
    return {"kind": "period", "start": start.isoformat(), "end": end.isoformat()}


def U(q, unit):
    return {"kind": "duration", "quantity": q, "unit": unit}


def add(family, span, expected, kind, anchor=None, direction=None, sentences=SENTENCES):
    x = span if not span[0].isalpha() or span.split()[0] in MONTHS + ABBR + DAYS else span
    text = sentences[len(cases) % len(sentences)].replace("{x}", x)
    cases.append({"id": f"C{len(cases) + 1:03d}", "family": family, "text": text, "span": span, "kind": kind,
                  "anchor": anchor, "direction": direction, "expected": expected})


def some_date():
    return date(2025, 1, 1) + timedelta(days=rng.randrange(4 * 365))


def week_start(d):
    return d - timedelta(days=d.weekday())


def next_weekday(wd, strictly_after=REF):
    return strictly_after + timedelta(days=(wd - strictly_after.weekday() - 1) % 7 + 1)


def last_weekday(wd):
    return REF - timedelta(days=(REF.weekday() - wd - 1) % 7 + 1)


def next_occurrence(month, day):
    for year in range(REF.year, REF.year + 9):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d > REF:
            return d


# 1. ISO dates (15)
for _ in range(15):
    d = some_date()
    add("iso", d.isoformat(), D(d), "date", "absolute")
# 2. month name first (20)
for i in range(20):
    d = some_date()
    span = [f"{MONTHS[d.month-1]} {d.day}, {d.year}", f"{ABBR[d.month-1]} {d.day}, {d.year}",
            f"{MONTHS[d.month-1]} {ordinal(d.day)}, {d.year}", f"{ABBR[d.month-1]}. {d.day} {d.year}"][i % 4]
    add("named_mdy", span, D(d), "date", "absolute")
# 3. day first (20)
for i in range(20):
    d = some_date()
    span = [f"{d.day} {MONTHS[d.month-1]} {d.year}", f"the {ordinal(d.day)} of {MONTHS[d.month-1]} {d.year}",
            f"{d.day} {ABBR[d.month-1]} {d.year}", f"{ordinal(d.day)} {MONTHS[d.month-1]}, {d.year}"][i % 4]
    add("named_dmy", span, D(d), "date", "absolute")
# 4. numeric, one possible order (20): the day is over 12
for i in range(20):
    d = some_date()
    while d.day <= 12:
        d = some_date()
    span = [f"{d.month:02d}/{d.day:02d}/{d.year}", f"{d.day:02d}/{d.month:02d}/{d.year}",
            f"{d.day}.{d.month}.{d.year}", f"{d.month}/{d.day}/{d.year}"][i % 4]
    add("numeric_unambiguous", span, D(d), "date", "absolute")
# 5. numeric, both orders possible (10): the right answer is to ask
for _ in range(10):
    m, dd = rng.sample(range(1, 13), 2)
    y = rng.choice([2026, 2027])
    add("numeric_ambiguous", f"{m:02d}/{dd:02d}/{y}",
        {"kind": "ambiguous", "options": [date(y, m, dd).isoformat(), date(y, dd, m).isoformat()]}, "date", "absolute")
# 6. month and year (15)
for i in range(15):
    y, m = rng.choice([2025, 2026, 2027]), rng.randrange(1, 13)
    span = [f"{MONTHS[m-1]} {y}", f"{ABBR[m-1]} {y}", f"{MONTHS[m-1]} of {y}"][i % 3]
    add("month_year", span, P(date(y, m, 1), date(y, m, 1) + relativedelta(months=1)), "period", "absolute")
# 7. years (5)
for y in (2025, 2026, 2027, 2028, 2030):
    add("year", f"the year {y}", P(date(y, 1, 1), date(y + 1, 1, 1)), "period", "absolute")
# 8. quarters (15)
for i, (q, y) in enumerate([(1, 2027), (3, 2026), (4, 2025), (2, 2027), (1, 2026), (4, 2026), (2, 2026), (3, 2027)]):
    span = [f"Q{q} {y}", f"the {['first', 'second', 'third', 'fourth'][q-1]} quarter of {y}"][i % 2]
    add("quarter", span, P(date(y, 3 * q - 2, 1), date(y, 3 * q - 2, 1) + relativedelta(months=3)), "period", "absolute")
qs = date(2026, 10, 1)
for rel, off in (("last", -3), ("this", 0), ("next", 3), ("last", -3), ("next", 3), ("this", 0), ("next", 3)):
    s = qs + relativedelta(months=off)
    add("quarter", f"{rel} quarter", P(s, s + relativedelta(months=3)), "period", "relative",
        "past" if rel == "last" else "future")
# 9. ISO weeks (5)
for y, w in ((2026, 45), (2026, 50), (2027, 2), (2026, 30), (2027, 10)):
    s = date.fromisocalendar(y, w, 1)
    add("iso_week", [f"week {w} of {y}", f"{y}-W{w:02d}"][w % 2], P(s, s + timedelta(days=7)), "period", "absolute")
# 10. day words (10)
for i, (span, off) in enumerate([("today", 0), ("tomorrow", 1), ("yesterday", -1), ("the day after tomorrow", 2),
                                  ("the day before yesterday", -2)] * 2):
    add("relative_day", span, D(REF + timedelta(days=off)), "date", "relative",
        "past" if off < 0 else "future")
# 11. offsets (35)
units = {"day": lambda n: relativedelta(days=n), "week": lambda n: relativedelta(weeks=n),
         "month": lambda n: relativedelta(months=n), "year": lambda n: relativedelta(years=n)}
for i in range(35):
    unit = ["day", "week", "month", "year"][i % 4]
    n = rng.choice([1, 2, 3, 5, 10, 12] if unit != "year" else [1, 2, 3])
    count = (WORDS[n] if i % 3 == 0 else str(n)) + f" {unit}{'s' if n > 1 else ''}"
    shape = i % 3
    span = [f"in {count}", f"{count} ago", f"{count} from now"][shape]
    sign = -1 if shape == 1 else 1
    add("offset", span, D(REF + sign * units[unit](n)), "date", "relative", "past" if sign < 0 else "future")
# 12. weekdays (25)
def next_this_week(wd):  # "next Wednesday" said on a Tuesday: tomorrow's week or the week after? Both are English.
    return next_weekday(wd) - REF < timedelta(days=7) and next_weekday(wd) < week_start(REF) + timedelta(days=7)


for wd in range(7):
    add("weekday", f"next {DAYS[wd]}", D(next_weekday(wd)), "date", "relative", "future")
    if next_this_week(wd):
        cases[-1]["alternative"] = D(next_weekday(wd) + timedelta(days=7))
for wd in range(7):
    add("weekday", f"last {DAYS[wd]}", D(last_weekday(wd)), "date", "relative", "past")
for wd in range(7):
    add("weekday", f"on {DAYS[wd]}", D(next_weekday(wd)), "date", "relative", "future")
for span, wd, nxt in (("next Fri", 4, True), ("last Wed", 2, False), ("next Thurs", 3, True), ("on Sat", 5, True)):
    add("weekday", span, D(next_weekday(wd) if nxt else last_weekday(wd)), "date", "relative",
        "future" if nxt else "past")
    if span.startswith("next") and next_this_week(wd):
        cases[-1]["alternative"] = D(next_weekday(wd) + timedelta(days=7))
# 13. calendar periods (20)
ws = week_start(REF)
periods = {
    "last week": P(ws - timedelta(days=7), ws), "this week": P(ws, ws + timedelta(days=7)),
    "next week": P(ws + timedelta(days=7), ws + timedelta(days=14)),
    "last month": P(date(2026, 9, 1), date(2026, 10, 1)), "this month": P(date(2026, 10, 1), date(2026, 11, 1)),
    "next month": P(date(2026, 11, 1), date(2026, 12, 1)), "last year": P(date(2025, 1, 1), date(2026, 1, 1)),
    "this year": P(date(2026, 1, 1), date(2027, 1, 1)), "next year": P(date(2027, 1, 1), date(2028, 1, 1)),
    "last weekend": P(date(2026, 10, 24), date(2026, 10, 26)),
}
for i in range(20):
    span = list(periods)[i % len(periods)]
    add("calendar_period", span, periods[span], "period", "relative",
        "past" if span.startswith("last") else "future")
    if span == "last weekend":  # Duckling's weekend starts Friday 18:00: a convention, not an error
        cases[-1]["alternative"] = P(date(2026, 10, 23), date(2026, 10, 26))
# 14. parts of a period (15)
for span, d in (("the end of the month", date(2026, 10, 31)), ("the beginning of next week", date(2026, 11, 2)),
                ("mid-November", date(2026, 11, 15)), ("the first day of next month", date(2026, 11, 1)),
                ("the last day of this year", date(2026, 12, 31)), ("the end of next month", date(2026, 11, 30)),
                ("the start of next year", date(2027, 1, 1)), ("the end of the year", date(2026, 12, 31)),
                ("the first day of next year", date(2027, 1, 1)), ("the last day of next month", date(2026, 11, 30)),
                ("the beginning of next month", date(2026, 11, 1)), ("the end of this week", date(2026, 11, 1)),
                ("the start of the month", date(2026, 10, 1)), ("mid-December", date(2026, 12, 15)),
                ("the end of last month", date(2026, 9, 30))):
    add("part_of_period", span, D(d), "date", "relative", "past" if "last month" in span or span == "the start of the month" else "future")
# 15. day and month without a year (15): the next occurrence
for i in range(15):
    m, dd = rng.randrange(1, 13), rng.randrange(1, 29)
    span = [f"{MONTHS[m-1]} {dd}", f"{MONTHS[m-1]} {ordinal(dd)}", f"{dd} {MONTHS[m-1]}"][i % 3]
    add("missing_year", span, D(next_occurrence(m, dd)), "date", "absolute")
    if date(REF.year, m, dd) < REF:  # this year's, already past: Duckling's convention, against the next occurrence
        cases[-1]["alternative"] = D(date(REF.year, m, dd))
# 16. durations (30)
durations = [("3 days", 3, "day"), ("2 weeks", 2, "week"), ("6 months", 6, "month"), ("1 year", 1, "year"),
             ("90 minutes", 90, "minute"), ("2 hours", 2, "hour"), ("ten days", 10, "day"), ("three weeks", 3, "week"),
             ("48 hours", 48, "hour"), ("a week", 1, "week"), ("an hour", 1, "hour"), ("a fortnight", 2, "week"),
             ("half a year", 6, "month"), ("12 months", 12, "month"), ("two years", 2, "year"), ("30 days", 30, "day"),
             ("five minutes", 5, "minute"), ("a month", 1, "month"), ("4 weeks", 4, "week"), ("one day", 1, "day"),
             ("18 months", 18, "month"), ("seven days", 7, "day"), ("3 years", 3, "year"), ("45 minutes", 45, "minute"),
             ("a year", 1, "year"), ("two months", 2, "month"), ("8 hours", 8, "hour"), ("six weeks", 6, "week"),
             ("a day", 1, "day"), ("twenty-one days", 21, "day")]
for span, q, unit in durations:
    add("duration", span, U(q, unit), "duration", sentences=FOR)
# 17. abbreviations (15)
for span, exp, kind, anchor, direction in (
        ("3 hrs", U(3, "hour"), "duration", None, None), ("2 wks", U(2, "week"), "duration", None, None),
        ("5 mins", U(5, "minute"), "duration", None, None), ("3 mos", U(3, "month"), "duration", None, None),
        ("2 yrs", U(2, "year"), "duration", None, None), ("next Tue", D(next_weekday(1)), "date", "relative", "future"),
        ("last Mon", D(last_weekday(0)), "date", "relative", "past"), ("Sept 5, 2026", D(date(2026, 9, 5)), "date", "absolute", None),
        ("Dec 1st 2026", D(date(2026, 12, 1)), "date", "absolute", None), ("Jan 10 2027", D(date(2027, 1, 10)), "date", "absolute", None),
        ("tmrw", D(REF + timedelta(days=1)), "date", "relative", "future"),
        ("2 yrs ago", D(REF - relativedelta(years=2)), "date", "relative", "past"),
        ("in 3 wks", D(REF + timedelta(weeks=3)), "date", "relative", "future"),
        ("Feb. 14, 2027", D(date(2027, 2, 14)), "date", "absolute", None),
        ("4 days ago", D(REF - timedelta(days=4)), "date", "relative", "past")):
    add("abbreviation", span, exp, kind, anchor, direction, sentences=FOR if kind == "duration" else SENTENCES)
# 18. with a time of day (10): the date is what is compared
for span, d, anchor, direction in (
        ("tomorrow at 5pm", REF + timedelta(days=1), "relative", "future"),
        ("November 3, 2026 at 14:30", date(2026, 11, 3), "absolute", None),
        ("next Monday at 9am", next_weekday(0), "relative", "future"), ("today at noon", REF, "relative", None),
        ("yesterday at 8:15", REF - timedelta(days=1), "relative", "past"),
        ("December 24, 2026 at midnight", date(2026, 12, 24), "absolute", None),
        ("2026-11-20 10:00", date(2026, 11, 20), "absolute", None),
        ("Friday at 6pm", next_weekday(4), "relative", "future"),
        ("the 15th of December 2026 at noon", date(2026, 12, 15), "absolute", None),
        ("in two days at 3pm", REF + timedelta(days=2), "relative", "future")):
    add("with_time", span, D(d), "date", anchor, direction)

assert len(cases) == 300, len(cases)
assert len({c["id"] for c in cases}) == 300
for c in cases:
    assert c["span"] in c["text"], c
Path(__file__).with_name("cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases))
print(f"{len(cases)} cases")
for fam in dict.fromkeys(c["family"] for c in cases):
    print(f"  {fam:22} {sum(c['family'] == fam for c in cases)}")
