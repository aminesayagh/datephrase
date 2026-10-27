# Date engines on 300 single-date texts (reference Tuesday 2026-10-27)

## Resolution: the date phrase alone -> value

| Engine | Correct | Other convention | Wrong (silent) | Refused | Asked | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| datephrase (Duckling contract) | 246 | 10 | 0 | 44 | 0 | 3.3 | 14.5 |
| dateparser.parse | 195 | 0 | 39 | 66 | 0 | 0.5 | 0.9 |
| dateparser.search_dates | 151 | 0 | 118 | 31 | 0 | 0.6 | 1.0 |
| duckling | 232 | 10 | 37 | 21 | 0 | 1.1 | 4.0 |
| chrono-node | 192 | 7 | 81 | 20 | 0 | 0.1 | 0.3 |
| datefinder | 117 | 9 | 28 | 146 | 0 | 0.0 | 0.1 |

## Detection: find the date in the sentence

| Engine | Exact span | Overlapping | Wrong span | Missed |
|---|---|---|---|---|
| dateparser.search_dates | 164 | 84 | 25 | 27 |
| duckling | 212 | 65 | 4 | 19 |
| chrono-node | 226 | 48 | 8 | 18 |
| datefinder | 138 | 16 | 2 | 144 |

## Correct / wrong by family

| Family | n | datephrase (Duckling contract) | dateparser.parse | dateparser.search_dates | duckling | chrono-node | datefinder |
|---|---|---|---|---|---|---|---|
| iso | 15 | 15 / 0 | 15 / 0 | 15 / 0 | 15 / 0 | 15 / 0 | 15 / 0 |
| named_mdy | 20 | 18 / 0 | 20 / 0 | 15 / 5 | 18 / 0 | 18 / 0 | 20 / 0 |
| named_dmy | 20 | 20 / 0 | 20 / 0 | 20 / 0 | 20 / 0 | 20 / 0 | 20 / 0 |
| numeric_unambiguous | 20 | 20 / 0 | 20 / 0 | 15 / 5 | 10 / 5 | 20 / 0 | 20 / 0 |
| numeric_ambiguous | 10 | 10 / 0 | 0 / 10 | 0 / 10 | 0 / 10 | 0 / 10 | 0 / 10 |
| month_year | 15 | 12 / 0 | 15 / 0 | 0 / 15 | 12 / 0 | 15 / 0 | 0 / 10 |
| year | 5 | 0 / 0 | 5 / 0 | 0 / 5 | 1 / 4 | 1 / 4 | 0 / 0 |
| quarter | 15 | 11 / 0 | 0 / 0 | 0 / 4 | 11 / 0 | 0 / 7 | 0 / 0 |
| iso_week | 5 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| relative_day | 10 | 10 / 0 | 10 / 0 | 6 / 4 | 10 / 0 | 10 / 0 | 6 / 4 |
| offset | 35 | 27 / 0 | 35 / 0 | 35 / 0 | 27 / 8 | 35 / 0 | 8 / 4 |
| weekday | 25 | 18 / 0 | 8 / 0 | 16 / 8 | 18 / 0 | 9 / 9 | 14 / 0 |
| calendar_period | 20 | 18 / 0 | 18 / 0 | 0 / 18 | 18 / 0 | 12 / 8 | 0 / 0 |
| part_of_period | 15 | 2 / 0 | 0 / 0 | 0 / 11 | 7 / 8 | 5 / 10 | 0 / 0 |
| missing_year | 15 | 14 / 0 | 15 / 0 | 15 / 0 | 14 / 0 | 15 / 0 | 1 / 0 |
| duration | 30 | 30 / 0 | 0 / 27 | 0 / 28 | 30 / 0 | 0 / 29 | 0 / 0 |
| abbreviation | 15 | 11 / 0 | 5 / 2 | 5 / 4 | 11 / 2 | 7 / 4 | 5 / 0 |
| with_time | 10 | 10 / 0 | 9 / 0 | 9 / 1 | 10 / 0 | 10 / 0 | 8 / 0 |
