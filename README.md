# temporal-reader

A thin wrapper over [dateparser](https://dateparser.readthedocs.io/) 1.4.3 (BSD-3-Clause, actively maintained), for English input. Number words go through [text2num](https://github.com/allo-media/text2num) 3.1.0 (MIT).

- dateparser reads the text.
- The caller's classification (`kind`, `anchor`, `direction`) decides what shape the answer takes.
- Gaps are filled by named rules and recorded in the result.
- Only a choice with no evidence either way is asked: the day/month order of `01/02/2026`.

```python
from datetime import date
from temporal_reader import TemporalContext, answer, read_temporal

ctx = TemporalContext(reference_date=date(2026, 10, 27))

read_temporal("October 26th, 2026").date                                   # 2026-10-26
read_temporal("last month", context=ctx, kind="period", anchor="relative").value
# PeriodValue(start=2026-09-01, end=2026-10-01, grain="month")
read_temporal("March 15", context=ctx, kind="date", anchor="absolute").date   # 2027-03-15 (the next one)
read_temporal("2.5 days", kind="duration").value                            # DurationValue(2.5, "day")

r = read_temporal("01/02/2026")
r.status                  # "ask"
r.question.text           # "Does '01/02/2026' mean January 2, 2026 or February 1, 2026?"
answer(r, "DMY").date     # 2026-02-01
```

From the shell:

```bash
uv run python -m temporal_reader "Wednesday" --kind date --anchor relative --direction past --context '{"reference_date": "2026-10-27"}'
```

## Three outcomes

| `status` | Meaning |
|---|---|
| `resolved` | `value` is a date, a period or a duration |
| `ask` | `question` must be forwarded to the user; send the reply back with `answer(result, reply)` |
| `not_temporal` | No value. `reason` may say why: `KIND_MISMATCH`, `DIRECTION_MISMATCH`, `MIXED_UNITS`, `MONTH_OVERFLOW_REJECTED`, `OUT_OF_RANGE`, or none |

## Rules (decided 2026-10-27)

| Gap | Rule | Recorded in |
|---|---|---|
| Missing day (`February 2026` as a date) | The 1st: 2026-02-01 | `provenance.filled` |
| Missing year or month | The next occurrence after the reference date. On 2026-10-27: `March 15` gives 2027-03-15, `15` gives 2026-11-15, `February 29` gives 2028-02-29. Today itself is not "next": `October 27` gives 2027 and `October` gives October 2027. | `provenance.filled` |
| No reference date in the session | Today's date | `provenance.reference_source`: `system_clock` |
| No `direction` on a relative expression | `future` (`Wednesday` gives the next one). The text still wins: `two weeks ago` stays in the past. | `provenance.defaults` |
| No `week_start` | `monday` (ISO 8601) | `provenance.defaults`, `provenance.policies` |
| No `month_overflow` | `clamp`: Jan 31 + 1 month = Feb 28 | `provenance.defaults`, `provenance.policies` |
| Day/month order unknown and two real readings | **Asked** | `question` |

Day/month order details:

- The order is asked only when both readings are real. A reading is real when its day and month come from the text; dateparser can invent them.
- `03/15` read day-first would make dateparser take 15 as a year and invent October 3, 2115. That reading is dropped, so `03/15` resolves to March 15 without a question.
- Year-first dates (`2026-02-01`) never depend on the order.

## Asking

The reader keeps no state between calls. An `ask` result's JSON carries a `request` field holding the text, the context, the labels and the answers so far. The client sends that JSON back with the user's reply:

```json
{
  "status": "ask",
  "question": {
    "reason": "DATE_ORDER_AMBIGUOUS",
    "field": "date_order",
    "text": "Does '01/02/2026' mean January 2, 2026 or February 1, 2026?",
    "expects": "choice",
    "options": [{"value": "MDY", "label": "January 2, 2026"}, {"value": "DMY", "label": "February 1, 2026"}]
  },
  "request": {"text": "01/02/2026", "context": {...}, "kind": null, "anchor": null, "direction": null, "answers": {}, "origin_offset": 0}
}
```

`answer(that_json, "DMY")` accepts only an offered option's value. Anything else raises `TemporalConfigError`.

## Beyond dateparser

dateparser reads none of the following. They are built on top of it.

**Rewrites (classified text only, recorded in `provenance.text_read`)**

| Written | Read as |
|---|---|
| Number words, via text2num | `October twenty-sixth` becomes "October 26th"; `the first of March` becomes "the 1st of March"; `twenty-one days` becomes "21 days" |
| next / last / this + weekday | `next Monday` becomes "Monday" plus future; `last Friday` becomes "Friday" plus past |
| `a fortnight`, `a couple of days`, `half a year` | 2 weeks, 2 days, 6 months |
| `one quarter` (a duration) | 3 months, then returned as 1 quarter |

**Forms (`forms.py`, recorded in `provenance.form`; examples on 2026-10-27)**

| Form | Examples | Value |
|---|---|---|
| `quarter` | `Q3 2026`, `2026-Q3`, `first quarter of 2026`, `last quarter`, `this quarter` | Period. Without a year: the latest one if past, else the next one. |
| `week_number` | `2026-W43`, `week 43` | ISO week, Monday start |
| `to_date` | `year to date`, `month to date`, `week to date`, `YTD` | Period from the start through today: `[2026-01-01, 2026-10-28)` |
| `weekend` | `last weekend`, `this weekend` | Saturday and Sunday: `[2026-10-24, 2026-10-26)` |
| `part_of_period` | `end of the month`, `the beginning of next week`, `mid-October`, `the first week of September` | A date (the last day, the first day, or the 15th / the middle), or a 7-day period |

**Not read, by decision:**

- `2 business days`: no business calendar.
- `the summer`: depends on the hemisphere.
- `Christmas`: no holiday calendars.
- `next weekend`: it could be this weekend or the one after.

**Known limits (from review, 2026-10-27):**

- **A direction label is checked only against dates, not periods.** `last quarter` labelled `future` still resolves to the past quarter.
- **A part of a period takes the whole period's next occurrence.**
  - `end of October` (relative, future) on 2026-10-27 gives 2027-10-31, although 2026-10-31 is still ahead.
  - `the end of the month` labelled `absolute` gives March 2027; label it `relative`.
- **dateparser misreads ordinals followed by "of the month".** When the day it returns isn't the written one (`1st of the month` read as the 27th), the result is `not_temporal` / `DAY_MISREAD`, never a wrong date.

## Reading a column of cells

`read_column(values, context=None)` reads an uploaded file's date column with **one** day/month order:

```python
r = read_column(["01/10/2026", "26.10.2026", "05/11/2026"])
r.order, r.order_source     # "DMY", "evidence"  (26.10.2026 only reads day first)
r.cells                     # {"01/10/2026": "2026-10-01", "26.10.2026": "2026-10-26", "05/11/2026": "2026-11-05"}
```

- The order comes from `context.date_order`, or else from evidence: a cell only one order reads (a day over 12) settles the column.
- With no evidence and a cell that both orders read differently, there is **one question for the column**, never one per cell. To answer it, read the column again with `TemporalContext(date_order=<the chosen value>)`.
- Cells that contradict each other (`26/10/2026` beside `10/26/2026`) get one `ORDER_CONFLICT` question.
- Cells that don't depend on the order (`2026-10-26`, `26 Oct 2026`) are always read. A cell against the order, or not a date, is `None`.
- A cell no order reads as a day and a month (`12/2026`) doesn't depend on the order, so it is read either way.
- Cells are keyed by their stripped text. Non-string cells (`None`, date objects) are skipped.
- Each distinct cell is read once. A cell missing its year gets the next occurrence, listed in `filled`.

## Classification from the caller

| Label | Values | Effect |
|---|---|---|
| `kind` | `date`, `period`, `duration` | **period:** the whole calendar period around dateparser's answer, at the precision it reported. **duration:** the exact quantity in dateparser's unit (`2.5 days`, `1.5 weeks`, `1 year 2 months` = 14 months). Months and days together (`two months and five days`) have no single unit and give `MIXED_UNITS`. |
| `anchor` | `absolute`, `relative` | **absolute:** missing parts are detected and filled by the rules above. dateparser parses the text against two unrelated base dates, and a part that changes with the base was not written. dateparser's own `REQUIRE_PARTS` is not used, because it misreads `March 15` as March 2015. **relative:** counts from the reference date. |
| `direction` | `past`, `future`; only with `anchor="relative"` | Chooses between readings that differ (`Wednesday`). A label that contradicts the text (`tomorrow` + `past`) gives `DIRECTION_MISMATCH`. |

If a label and dateparser disagree (for example `period`, but dateparser found a single day), the result is `not_temporal` / `KIND_MISMATCH`. It is never forced.

## Session context

| Field | Values | Default when unset |
|---|---|---|
| `reference_date` | `datetime.date` | today's date |
| `date_order` | `MDY`, `DMY`, `YMD` | none: a real ambiguity is asked |
| `week_start` | `monday`, `sunday` | `monday` |
| `month_overflow` | `clamp`, `reject` | `clamp` |

## Output

A resolved result also carries:

- `period` and `locale`: dateparser's precision and language.
- `details`: weekday, ISO week, quarter and day of year for a date; the number of days for a period.
- `source`: the text and its offsets.
- `provenance`: the backend version, the exact settings sent, the reference date and its source, the classification received, and the `filled`, `defaults` and `policies` described above.

## The brief's cases

`tests/test_spec_cases.py` sends the brief's 156 cases, each with the classification a correct classifier gives it. Expected values that the decisions above change are written by hand:

- **`DECIDED` (16 cases):** next occurrence, today's date, the defaults, and the direction.
- **`ACCEPTED` (25 cases):** the brief's "unsupported" inputs. Each one must be dateparser's correct reading, or an honest `not_temporal` / `ask`; never another value.

**150 pass, 6 are known failures.** Each failure carries its reason in `KNOWN`:

| Count | Cause |
|---|---|
| 1 | a year in words: `twenty twenty-six` becomes "20 26" in text2num |
| 3 | dateparser accepts something wrong: `2026-13-01` (month and day swapped), a weekday that contradicts the date, the suffix in `26rd` |
| 1 | `2 weeks later`: counted from the reference date, where the brief asks "later than what?" |
| 1 | the label conflicts with the brief: `2026` labelled `period` returns the year, while the brief asks for its role |

The failures are marked `strict`: a case that starts passing fails the run, so the list stays accurate.

## Tests

```bash
uv sync
uv run pytest -q     # 301 passed, 6 xfailed
```
