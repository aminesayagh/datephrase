# datephrase

Read one English date phrase (`"last quarter"`, `"next Monday"`, `"26/10/2026"`, `"a fortnight"`) into a typed, JSON-ready value: a **date**, a whole **period**, or a **duration**. Each value records how it was read.

It is a thin layer over two maintained libraries:

- [dateparser](https://github.com/scrapinghub/dateparser) 1.4.3 (BSD-3-Clause) reads the text.
- [text2num](https://github.com/allo-media/text2num) 3.1.0 (MIT) turns number words into digits.

On top of them it adds four things:

- **The shape you asked for.** A period is the whole period (`last month` gives September 1 to October 1), not one day inside it. A duration keeps its unit: `3 months` stays 3 months, never 90 days.
- **Forms dateparser does not read.** Quarters, ISO week numbers, "year to date", weekends, and parts of a period ("the end of the month", "the first week of September").
- **No silent guesses.** Every gap is filled by a named rule and recorded. The one real ambiguity, the day/month order of `01/02/2026`, comes back as a question for your user.
- **Whole columns.** `read_column` reads a spreadsheet column of dates with a single day/month order for the whole column, decided by evidence.

English only. Gregorian calendar. Python 3.13 or later.

## Install

```bash
pip install "git+https://github.com/aminesayagh/datephrase.git"
# or
uv add "git+https://github.com/aminesayagh/datephrase.git"
```

## Quick start

```python
from datetime import date
from datephrase import TemporalContext, answer, read_temporal

ctx = TemporalContext(reference_date=date(2026, 10, 27))   # "today" for relative phrases

read_temporal("October 26th, 2026").value.date                                     # 2026-10-26
read_temporal("last quarter", context=ctx, kind="period", anchor="relative").value
# a PeriodValue: start 2026-07-01, end 2026-10-01 (excluded), grain "quarter"
read_temporal("next Monday", context=ctx, kind="date", anchor="relative").value.date  # 2026-11-02
read_temporal("a fortnight", kind="duration").value                                # a DurationValue: 2 "week"

r = read_temporal("01/02/2026")
r.status          # "ask"
r.question.text   # "Does '01/02/2026' mean January 2, 2026 or February 1, 2026?"
answer(r, "DMY").value.date                                                        # 2026-02-01
```

From the shell:

```bash
python -m datephrase "last quarter" --kind period --anchor relative --context '{"reference_date": "2026-10-27"}'
```

## How it works

You give the reader **one phrase**, not a whole sentence. Cut the phrase out first (with your own extractor or an NER model). Leave comparison words such as "before", "since" or "in the last" outside it: they belong to your query, not to the date.

Optionally, you also say what the phrase is. These labels usually come from a classifier upstream:

| Label | Values | Meaning |
|---|---|---|
| `kind` | `date`, `period`, `duration` | One day, a whole span (a month, a quarter…), or a length of time |
| `anchor` | `absolute`, `relative` | Written out in full (`October 2026`), or counted from today (`last month`) |
| `direction` | `past`, `future` | For relative phrases only: which way it points when the text does not say (`Wednesday`) |

Without labels you get dateparser's single date as it is. With labels you get the shape you asked for. If dateparser's reading contradicts the labels, the result is refused (`not_temporal`), never forced.

## Results

`read_temporal` returns a `TemporalReadResult` with one of three statuses:

| `status` | What you get |
|---|---|
| `resolved` | `value`: a `DateValue`, `PeriodValue` or `DurationValue` |
| `ask` | `question`: send it to your user, then call `answer(result, reply)` |
| `not_temporal` | No value. `reason` says why when there is one (see below) |

| Value | Fields |
|---|---|
| `DateValue` | `date`, `datetime` (a time of day, when written, is kept here) |
| `PeriodValue` | `start`, `end` (**end excluded**: filter with `>= start AND < end`), `grain` (`week`, `month`, `quarter`, `year`, `weekend`, `year_to_date`…) |
| `DurationValue` | `quantity` (int, or float for `2.5 days`), `unit` (`second`, `minute`, `hour`, `day`, `week`, `month`, `quarter`, `year`) |

`result.to_dict()` is JSON-ready:

```json
{
  "status": "resolved",
  "value": {"kind": "period", "start": "2026-07-01", "end": "2026-10-01", "grain": "quarter"},
  "question": null,
  "reason": null,
  "source": {"text": "last quarter", "start": 0, "end": 12},
  "details": {"days": 92},
  "provenance": {
    "backend": "dateparser 1.4.3",
    "reference_date": "2026-10-27",
    "reference_source": "session",
    "classification": {"kind": "period", "anchor": "relative", "direction": null},
    "form": "quarter",
    "text_read": null,
    "filled": [],
    "defaults": ["direction:future"],
    "policies": {},
    "settings": {"...": "the exact settings sent to dateparser"}
  }
}
```

- `source` keeps the phrase's offsets. Pass `origin_offset=` to place them in a longer text.
- `details` adds calendar facts: weekday, ISO week, quarter and day of year for a date; the number of days for a period.
- `provenance` records everything that shaped the answer: the backend version, where "today" came from, the labels received, the form used, the rewritten text dateparser actually read, the parts filled by rule, and the defaults and policies applied.

`not_temporal` reasons:

| Reason | Meaning |
|---|---|
| `KIND_MISMATCH` | The `kind` label and dateparser's reading disagree: `period` but a single day was found, or `duration` for a moment such as `tomorrow` or `tomorrow at 1:00` (a length is read back from a date, never ahead of it) |
| `ANCHOR_MISMATCH` | `absolute`, but the phrase is counted from today: it names no part of a date itself (`tomorrow`, `Wednesday`, `this month`), or it is a form like `next quarter`, `this weekend` or `year to date` |
| `DIRECTION_MISMATCH` | The `direction` label contradicts the text (`tomorrow` labelled `past`) |
| `DAY_MISREAD` | dateparser returned another day than the one written (it reads `1st of the month` as the 27th) |
| `MIXED_UNITS` | `two months and five days`: no single unit holds it exactly |
| `FRACTION_NOT_SUPPORTED` | A fractional count of months, quarters or years in any spelling (`1.5 months`, `.5 yr`, `1.5-year`): dateparser would read "1.5" as January 5 |
| `MONTH_OVERFLOW_REJECTED` | You set `month_overflow="reject"`, and Jan 31 + 1 month has no Feb 31 |
| `OUT_OF_RANGE` | Before year 1 or after year 9999, or a week 53 that does not exist |
| `TOO_LONG` | More than 512 characters, measured on the text as given: a phrase, not a document |
| none | The text is not a date this reader understands |

## Supported phrases

All examples are read with today being Tuesday, 2026-10-27.

### Dates

| Written | Examples | Value |
|---|---|---|
| ISO, year first | `2026-10-26`, `2026/10/26`, `2026.10.26` | 2026-10-26 (the order is fixed by the shape) |
| Numeric day/month | `26/10/2026`, `10/26/2026`, `26.10.2026`, `26-10-2026` | 2026-10-26 (only one order is a real date) |
| Ambiguous numeric | `01/02/2026` | **asks**: January 2 or February 1 |
| Month name | `26 October 2026`, `October 26th, 2026`, `26 Oct 2026`, `Sept. 5, 2026`, `the 26th of October 2026` | the date |
| With a weekday | `Mon, 26 Oct 2026` | 2026-10-26 |
| Two-digit year | `Oct 26 '26`, `26/10/26` | 2026-10-26 |
| With a time | `2026-10-26 at 14:30`, `2026-10-26T14:30:00Z` | 2026-10-26, with the time in `datetime` |
| Numbers in words | `October twenty-sixth`, `the first of March` | 2026-10-26, 2026-03-01 |
| Day words | `today`, `yesterday`, `tomorrow`, `the day after tomorrow` | counted from today |
| Offsets | `in two weeks`, `3 days ago`, `3 weeks from now`, `in one quarter` | 2026-11-10, 2026-10-24, 2026-11-17, 2027-01-27 |
| Weekdays | `Wednesday`, `next Monday`, `last Friday`, `next tue` | 2026-10-28 (future), 2026-11-02, 2026-10-23, 2026-11-03 |
| Part of a period | `end of the month`, `the beginning of next week`, `mid-October`, `first day of next month`, `the last day of next month` | 2026-10-31, 2026-11-02, 2026-10-15, 2026-11-01, 2026-11-30 |

### Periods

`end` is excluded.

| Written | Examples | Value |
|---|---|---|
| Calendar periods | `last month`, `this week`, `next year` | 2026-09-01..2026-10-01, 2026-10-26..2026-11-02, 2027-01-01..2028-01-01 |
| Month, year | `October 2026`, `2026-10`, `in 2026` | the whole month or year |
| Quarters | `Q3 2026`, `2026-Q3`, `first quarter of 2026`, `last quarter`, `this quarter` | 2026-07-01..2026-10-01, … |
| ISO weeks | `2026-W43`, `week 43` | 2026-10-19..2026-10-26 |
| Up to today | `year to date`, `month to date`, `week to date`, `YTD` | 2026-01-01..2026-10-28 (today included) |
| Weekends | `last weekend`, `this weekend` | 2026-10-24..2026-10-26, 2026-10-31..2026-11-02 (Saturday and Sunday) |
| Week of a month | `the first week of September`, `the last week of September` | 2026-09-01..2026-09-08, 2026-09-24..2026-10-01 |

### Durations

The unit is kept.

| Written | Examples | Value |
|---|---|---|
| Number and unit | `30 days`, `two weeks`, `3 months`, `one year`, `1 year 2 months` | 30 day, 2 week, 3 month, 1 year, 14 month |
| Fractions | `2.5 days`, `1.5 weeks`, `1.5 fortnights` | exact: 2.5 day, 1.5 week, 3 week |
| Times | `1 second`, `90 minutes`, `3 hours` | kept in that unit: 1 second, 90 minute, 3 hour |
| Unit spellings | `90 mins`, `2 hrs`, `30 secs`, `3 wks`, `6 mos`, `2 yrs`, `1 qtr`, `1.50hours`, `2-week` | 90 minute, 2 hour, 30 second, 3 week, 6 month, 2 year, 1 quarter, 1.5 hour, 2 week |
| In words | `twenty-one days`, `a week`, `a fortnight`, `a couple of days`, `half a year`, `one quarter` | 21 day, 1 week, 2 week, 2 day, 6 month, 1 quarter |

## Rules instead of guesses

When the text and the session leave something open, a fixed rule fills it. Every applied rule is listed in `provenance`:

| Open | Rule | Recorded in |
|---|---|---|
| Missing year or month (`March 15`, absolute) | The **next occurrence** after today: 2027-03-15. Today itself is not "next": `October 27` gives 2027-10-27, and `October` gives October 2027. | `filled` |
| Missing day (`February 2026` as a date) | The 1st: 2026-02-01 | `filled` |
| A day alone (`the first`, `the 15th`) | That day at its next occurrence: 2026-11-01, 2026-11-15 (its latest one when the direction is past). dateparser alone reads `the 1st` to `the 12th` as months. | `filled` |
| No `reference_date` in the context | Today's date from the system clock. Pass it explicitly to make results reproducible. | `reference_source: "system_clock"` |
| No `direction` on a relative phrase | `future` (`Wednesday` gives the next one). The text wins when it says: `two weeks ago` stays in the past. | `defaults` |
| No `week_start` | Monday (ISO 8601) | `defaults`, `policies` |
| No `month_overflow` | `clamp`: Jan 31 + 1 month = Feb 28 | `defaults`, `policies` |
| Day/month order, when both readings are real dates and differ | **Asked**, never guessed | `question` |

A reading is real only when its day and month come from the text. `03/15` read day-first would invent a month, so it resolves to March 15 without a question.

### Session context

```python
TemporalContext(
    reference_date=date(2026, 10, 27),  # today for relative phrases; system date when None
    date_order=None,                    # "MDY", "DMY" or "YMD" for numeric dates; None = ask on real ambiguity
    week_start=None,                    # "monday" or "sunday"; None = monday
    month_overflow=None,                # "clamp" or "reject"; None = clamp
)
```

Invalid settings raise `TemporalConfigError`. So does an answer that is not one of the offered options.

## Asking the user

The only question is the day/month order. The reader keeps no state between calls: an `ask` result's JSON carries a `request` with everything needed to resume. You can send it to a client and back:

```python
r = read_temporal("01/02/2026")
payload = r.to_dict()          # send payload["question"] to the user: text + options
# payload["question"]["options"] == [{"value": "MDY", "label": "January 2, 2026"},
#                                    {"value": "DMY", "label": "February 1, 2026"}]
answer(payload, "DMY").value.date   # 2026-02-01
```

To stop asking for a whole session, set `TemporalContext(date_order="DMY")`.

## Reading a column

`read_column(values, context=None)` reads a column of cells, such as a spreadsheet's date column, with **one** day/month order for the whole column:

```python
from datephrase import read_column

r = read_column(["01/10/2026", "26.10.2026", "05/11/2026"])
r.order, r.order_source   # ("DMY", "evidence"): "26.10.2026" only reads day first
r.cells                   # {"01/10/2026": "2026-10-01", "26.10.2026": "2026-10-26", "05/11/2026": "2026-11-05"}
```

- **The order** comes from `context.date_order`, or else from evidence: one cell that only one order reads (a day over 12) settles the whole column.
- **No evidence:** with no deciding cell and at least one ambiguous cell, you get **one** question for the column (`DATE_ORDER_AMBIGUOUS`), never one per cell. Answer it by calling `read_column` again with `TemporalContext(date_order=<chosen value>)`.
- **Contradicting cells** (`26/10/2026` beside `10/26/2026`) give one `ORDER_CONFLICT` question.
- **Order-free cells** (`2026-10-26`, `26 Oct 2026`, `12/2026`) are always read.
- **`None` cells:** a cell against the chosen order, or one that is not a date.
- **Keys and missing parts:** cells are keyed by their stripped text, and each distinct cell is read once. A cell missing its year gets the next occurrence, listed in `r.filled`.

## Not supported

Read as `not_temporal` by design:

- Business days and holidays (`2 business days`, `Christmas`): no business or holiday calendar.
- Seasons (`the summer`): they depend on the hemisphere.
- `next weekend`: English does not say whether it means this weekend or the one after.
- Rolling windows and ranges as one phrase (`last 30 days`, `since March`, `between March and May`): read the parts, e.g. `30 days` as a duration, and build the range in your query.
- Fractional months and years (`1.5 months`, `half a month`): dateparser does not read them.
- Languages other than English.

Known limits:

- `the 15th`, read as relative, can land in the past: dateparser ignores the future preference for a lone day number.
- A `direction` label is checked against dates, not periods: `last quarter` labelled `future` still resolves.
- A part of a period takes the whole period's next occurrence: `end of October` on 2026-10-27 gives 2027-10-31.
- Years in words (`twenty twenty-six`) are not read.
- dateparser accepts some impossible input instead of rejecting it:
  - `2026-13-01` is read as January 13.
  - `26rd` is read as the 26th.
  - A weekday that contradicts the date is ignored.

## Tests

```bash
uv sync
uv run pytest -q     # 433 passed, 6 xfailed
```

- **`tests/test_reader.py`, `tests/test_forms.py`, `tests/test_review_gpt61.py` and `tests/test_audit_fixes.py`** cover every supported form, the rules, the questions, columns, provenance, the JSON contract, and each finding of the code reviews. Each finding is tested on the whole class of input it is about (spacings, spellings, every ordinal day, every session order), not only the reported phrase.
- **`tests/test_spec_cases.py`** runs a reference set of 156 labelled cases (`tests/fixtures/`):
  - 150 pass.
  - The 6 known gaps above are marked `xfail(strict=True)` with their reason, so a case that starts passing fails the run until the list is updated.
- **Backend answers are pinned.** A dateparser upgrade that changes an answer fails a test before it reaches your code.

## License

[MIT](LICENSE). The dependencies keep their own licenses: dateparser is BSD-3-Clause, text2num is MIT.
