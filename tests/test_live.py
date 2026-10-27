"""Representative phrases against a real Duckling (skipped when none is running at DATEPHRASE_DUCKLING_URL).
Start one with: docker run -d --rm -p 127.0.0.1:8000:8000 rasa/duckling"""

from datetime import date

import pytest

from datephrase import EngineUnavailable, TemporalContext, read_temporal

NOW = TemporalContext(reference_date=date(2026, 10, 27))  # a Tuesday

try:
    read_temporal("today", context=NOW)
except EngineUnavailable:
    pytest.skip("no Duckling running", allow_module_level=True)


def show(r):
    v = r.value
    if v is None:
        return f"{r.status} {r.reason or ''}".strip()
    if v.kind == "period":
        return f"{v.start}..{v.end}"
    if v.kind == "duration":
        return f"{v.quantity} {v.unit}"
    return str(v.date)


@pytest.mark.parametrize("text, labels, expected", [
    ("October 26, 2026", {"kind": "date", "anchor": "absolute"}, "2026-10-26"),
    ("26/10/2026", {"kind": "date", "anchor": "absolute"}, "2026-10-26"),
    ("03/04/2026", {"kind": "date", "anchor": "absolute"}, "ask DATE_ORDER_AMBIGUOUS"),
    ("next Monday", {"kind": "date", "anchor": "relative", "direction": "future"}, "2026-11-02"),
    ("in two weeks", {"kind": "date", "anchor": "relative", "direction": "future"}, "2026-11-10"),
    ("last month", {"kind": "period", "anchor": "relative", "direction": "past"}, "2026-09-01..2026-10-01"),
    ("the third quarter of 2026", {"kind": "period", "anchor": "absolute"}, "2026-07-01..2026-10-01"),
    ("3 days", {"kind": "duration"}, "3 day"),
    ("90 minutes", {"kind": "duration"}, "90 minute"),
    ("the year 2027", {"kind": "period", "anchor": "absolute"}, "not_temporal"),   # Duckling reads only "the year"
    ("tomorrow", {"kind": "date", "anchor": "absolute"}, "not_temporal ANCHOR_MISMATCH"),
    ("cotton", {}, "not_temporal"),
])
def test_live(text, labels, expected):
    assert show(read_temporal(text, context=NOW, **labels)) == expected
