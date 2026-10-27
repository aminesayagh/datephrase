# datephrase

Read one English date phrase (`"last month"`, `"next Monday"`, `"26/10/2026"`, `"3 days"`) into a typed, JSON-ready value: a **date**, a whole **period**, or a **duration**, with a record of how it was read.

datephrase **parses nothing itself**. [Duckling](https://github.com/facebook/duckling), Meta's maintained date and time recogniser, reads the phrase. datephrase is the contract layer around it:

- **The shape you ask for.** You label the phrase as a date, a period or a duration. The answer comes back in that shape, or is refused.
- **Never a silent guess.**
  - Only Duckling's reading of the **whole** phrase counts.
  - A reading that contradicts your labels is refused.
  - The one real ambiguity, the day/month order of `03/04/2026`, comes back as a question for your user.
- **Provenance.** Every answer says what Duckling read, at which precision, against which reference date.

English only. Python 3.13 or later.

## Install and run Duckling

datephrase talks to a Duckling service over HTTP. Run one locally with Docker; it stays on your machine:

```bash
docker run -d --rm -p 127.0.0.1:8000:8000 --name duckling rasa/duckling:latest
pip install "git+https://github.com/aminesayagh/datephrase.git"
```

The service address defaults to `http://127.0.0.1:8000/parse`. Set `DATEPHRASE_DUCKLING_URL` to use another one. If the service can't be reached, `read_temporal` raises `EngineUnavailable`: that's a deployment problem, never a statement about the text.

## Quick start

```python
from datetime import date
from datephrase import TemporalContext, answer, read_temporal

ctx = TemporalContext(reference_date=date(2026, 10, 27))   # "today" for relative phrases

read_temporal("next Monday", context=ctx, kind="date", anchor="relative").value.date      # 2026-11-02
read_temporal("last month", context=ctx, kind="period", anchor="relative").value
# a PeriodValue: start 2026-09-01, end 2026-10-01 (excluded), grain "month"
read_temporal("90 minutes", kind="duration").value                                        # 90 "minute"

r = read_temporal("03/04/2026", context=ctx)
r.status, r.question.text   # "ask", "Does '03/04/2026' mean March 4, 2026 or April 3, 2026?"
answer(r, "DMY").value.date                                                               # 2026-04-03
```

From the shell:

```bash
python -m datephrase "last month" --kind period --anchor relative --context '{"reference_date": "2026-10-27"}'
```

## The contract

### What you send

- **One phrase holding one date**, cut out of the sentence by your own extractor. Leave comparison words like "before" or "in the last" outside it. A string with several dates is not checked; what comes back for it is not defined.
- **Optional labels**, usually from a classifier:

| Label | Values | Meaning |
|---|---|---|
| `kind` | `date`, `period`, `duration` | One day, a whole span, or a length of time |
| `anchor` | `absolute`, `relative` | Written in full (`October 2026`), or counted from today (`last month`) |
| `direction` | `past`, `future` | For relative phrases: which side of today the answer must be on |

- **The session context:**

| Field | Values | When unset |
|---|---|---|
| `reference_date` | `datetime.date` | today's date (recorded as `system_clock`) |
| `date_order` | `MDY`, `DMY`, `YMD` | a genuine day/month ambiguity is asked |
| `week_start` | `monday`, `sunday` | Monday, Duckling's convention (recorded in `defaults`) |

### What datephrase does with Duckling's answer

1. **Whole phrase only.** Duckling also reads parts of a phrase: "the year 2027" can come back as just "the year". A part is not the phrase, so it's refused.
2. **Day/month order.** The phrase is read with US English (month first) and UK English (day first) settings.
   - If both read it and agree, there's nothing to ask.
   - If only one reads it, that one is used: `26/10/2026`.
   - If both read it differently, the user is **asked**: `03/04/2026`. If only one of the two answers fits your labels, that one is used.
3. **Shape by label:**
   - **date:** a day. Duckling's day, hour or minute precision is accepted; a week, month, quarter or year answer (`in one year` comes back as "October 2027") is refused, since it isn't a day.
   - **period:** the whole week, month, quarter or year at Duckling's precision, or Duckling's interval. A single day is refused.
   - **duration:** Duckling's length, in Duckling's unit.
   - **no label:** whatever Duckling read.
4. **Label checks:**
   - `direction` must agree with the side of today the answer falls on.
   - `absolute` must name a date of its own: a phrase whose year, month and day all move with "today" (`tomorrow`, `next quarter`) is refused.

### What comes back

| `status` | Meaning |
|---|---|
| `resolved` | `value` is a `DateValue` (`date`, `datetime`), a `PeriodValue` (`start`, `end` excluded, `grain`), or a `DurationValue` (`quantity`, `unit`) |
| `ask` | `question` to forward to your user, with the options to send back through `answer(result, reply)` |
| `not_temporal` | No value. `reason`: `KIND_MISMATCH`, `DIRECTION_MISMATCH`, `ANCHOR_MISMATCH`, `TOO_LONG`, or none when Duckling read nothing for the whole phrase |

`result.to_dict()` is JSON-ready. `provenance` holds:

- `text_read`: what Duckling read.
- `engine`: Duckling's dim, locale, type and grain.
- `reference_date` and `reference_source`.
- `classification`: the labels you sent.
- `defaults` and `policies`.

## Duckling's conventions, which datephrase inherits

- **"next Wednesday", said on a Tuesday,** is the Wednesday of **next week**.
- **A weekend** runs from **Friday 18:00** to Monday.
- **A date without its year that has already passed this year** (`October 1`, said on October 27) is **this year's**.
- **Durations mixing months and days are converted at 30 days a month:** `two months and five days` is 65 days.
- **Weeks start on Monday.** `week_start="sunday"` shifts weekly periods back one day.

## Measured

`benchmarks/engines` runs 300 short texts, each with one date expression, through datephrase and through other engines directly. Every engine gets the same labels. Expected values come from independent calendar arithmetic.

| Engine | Correct | Other convention | **Silent wrong** | Refused |
|---|---|---|---|---|
| **datephrase (contract over Duckling)** | 246 | 10 | **0** | 44 |
| Duckling alone | 232 | 10 | 37 | 21 |
| dateparser | 195 | 0 | 39 | 66 |
| chrono-node | 192 | 7 | 81 | 20 |

The 44 refusals are what Duckling cannot read whole, or reads less precisely than asked:

- parts of a period (`the end of the month`, `mid-November`);
- year offsets (`in one year`, read to the month);
- `the year 2027`, ISO weeks (`2026-W45`), `Q1 2027`;
- some abbreviations (`2 wks`, `3 mos`).

They come back as `not_temporal`, never as a wrong value.

## Tests

```bash
uv sync
uv run pytest -q
```

- **`tests/test_contract.py`** pins the contract against a fake Duckling: shapes, refusals, the question, provenance, settings and errors. It needs no service.
- **`tests/test_live.py`** runs representative phrases against a real Duckling, and is skipped when none is running.

## License

[MIT](LICENSE). Duckling is BSD-licensed and runs as a separate service.
