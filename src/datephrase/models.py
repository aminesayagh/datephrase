from dataclasses import dataclass, field, fields
from datetime import date, datetime
from typing import Literal

Status = Literal["resolved", "ask", "not_temporal"]
DATE_ORDERS = ("MDY", "DMY", "YMD")
WEEK_STARTS = ("monday", "sunday")
KINDS = ("date", "period", "duration")
ANCHORS = ("absolute", "relative")
MONTH_OVERFLOWS = ("clamp", "reject")
DIRECTIONS = ("past", "future")
WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class TemporalConfigError(ValueError):
    """Bad caller configuration, as opposed to text the backend could not read."""


@dataclass(frozen=True)
class TemporalContext:
    """The session's settings. Unset ones get a default (today's date, Monday weeks, clamped months), recorded in
    provenance; date_order has none: a real day/month ambiguity is asked.

    reference_date: the date relative expressions count from ("tomorrow"); today's date when None.
    date_order: MDY, DMY or YMD for numeric dates such as 01/02/2026.
    week_start: monday or sunday, for whole weeks.
    month_overflow: clamp (Jan 31 + 1 month = Feb 28) or reject, when the target month is too short."""

    reference_date: date | None = None
    date_order: str | None = None
    week_start: str | None = None
    month_overflow: str | None = None

    def __post_init__(self):
        rd = self.reference_date
        if rd is not None and type(rd) is not date:  # datetime is a date subclass
            raise TemporalConfigError(f"reference_date must be a datetime.date, got {type(rd).__name__}")
        for name, allowed in (("date_order", DATE_ORDERS), ("week_start", WEEK_STARTS),
                              ("month_overflow", MONTH_OVERFLOWS)):
            if getattr(self, name) not in (None, *allowed):
                raise TemporalConfigError(f"{name} must be one of {allowed} or None, got {getattr(self, name)!r}")

    @classmethod
    def from_dict(cls, d: dict) -> "TemporalContext":
        d = dict(d)
        unknown = d.keys() - {f.name for f in fields(cls)}
        if unknown:
            raise TemporalConfigError(f"unknown context keys: {sorted(unknown)}")
        if isinstance(d.get("reference_date"), str):
            try:
                d["reference_date"] = date.fromisoformat(d["reference_date"])
            except ValueError:
                raise TemporalConfigError(f"reference_date is not an ISO date: {d['reference_date']!r}") from None
        return cls(**d)

    def to_dict(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["reference_date"] = self.reference_date.isoformat() if self.reference_date else None
        return d


@dataclass(frozen=True)
class DateValue:
    date: date
    datetime: datetime  # dateparser's full answer: a time of day stays here
    kind: Literal["date"] = field(default="date", init=False)

    def to_dict(self) -> dict:
        return {"kind": "date", "date": self.date.isoformat(), "datetime": self.datetime.isoformat()}


@dataclass(frozen=True)
class PeriodValue:
    """A whole calendar period, [start, end): end is the first day after it."""

    start: date
    end: date
    grain: str
    kind: Literal["period"] = field(default="period", init=False)

    def to_dict(self) -> dict:
        return {"kind": "period", "start": self.start.isoformat(), "end": self.end.isoformat(), "grain": self.grain}


@dataclass(frozen=True)
class DurationValue:
    """A length of time in its own unit: 3 months stays 3 months, 2.5 days stays 2.5 days."""

    quantity: int | float  # exact for what dateparser reads: 2.5 days, 1.5 weeks
    unit: str
    kind: Literal["duration"] = field(default="duration", init=False)

    def to_dict(self) -> dict:
        return {"kind": "duration", "quantity": self.quantity, "unit": self.unit}


Value = DateValue | PeriodValue | DurationValue


@dataclass(frozen=True)
class Source:
    """The candidate text with outer whitespace removed. Offsets are code points, end-exclusive."""

    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Provenance:
    backend: str
    settings: dict = field(default_factory=dict)
    reference_date: date | None = None  # set when the answer depends on it
    reference_source: str | None = None  # "session", or "system_clock" when the session gave none
    kind: str | None = None  # the caller's classification, as received
    anchor: str | None = None
    direction: str | None = None  # as labelled by the classifier; None when not sent
    filled: tuple[str, ...] = ()  # parts the text lacked, taken from the next occurrence after the reference date
    defaults: tuple[str, ...] = ()  # defaults applied for what the classifier or session left unset
    policies: dict = field(default_factory=dict)  # the session settings the answer used: week_start, month_overflow
    text_read: str | None = None  # the text as given to dateparser, when rewritten ("next Monday" -> "Monday")
    form: str | None = None  # set when the value comes from a form built on dateparser (forms.py), e.g. "quarter"

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "settings": self.settings,
            "reference_date": self.reference_date.isoformat() if self.reference_date else None,
            "reference_source": self.reference_source,
            "classification": {"kind": self.kind, "anchor": self.anchor, "direction": self.direction},
            "filled": list(self.filled),
            "defaults": list(self.defaults),
            "policies": self.policies,
            "text_read": self.text_read,
            "form": self.form,
        }


@dataclass(frozen=True)
class Question:
    """What the reader needs from the user. The answer goes back through `answer(result, value)`.

    expects: "choice" (one of `options`' values), "date" (ISO, e.g. 2026-10-26) or "integer"."""

    reason: str
    field: str
    text: str
    expects: str = "choice"
    options: tuple[tuple[str, str], ...] = ()  # (value to send back, label to show)

    def to_dict(self) -> dict:
        return {"reason": self.reason, "field": self.field, "text": self.text, "expects": self.expects,
                "options": [{"value": v, "label": label} for v, label in self.options]}


@dataclass(frozen=True)
class Request:
    """Everything needed to read the same text again once the user answers: the reader keeps no state."""

    text: str
    context: TemporalContext
    kind: str | None
    anchor: str | None
    answers: dict
    origin_offset: int = 0
    direction: str | None = None

    def to_dict(self) -> dict:
        return {"text": self.text, "context": self.context.to_dict(), "kind": self.kind, "anchor": self.anchor,
                "direction": self.direction, "answers": self.answers, "origin_offset": self.origin_offset}


@dataclass(frozen=True)
class TemporalReadResult:
    """resolved: `value` is set. ask: `question` is set; forward it and call `answer()` with the reply.
    not_temporal: neither; `reason` may say why. `period` is dateparser's precision: day, week, month or year."""

    status: Status
    source: Source
    provenance: Provenance
    request: Request
    value: Value | None = None
    question: Question | None = None
    reason: str | None = None
    period: str | None = None
    locale: str | None = None

    def __post_init__(self):
        if (self.status == "resolved") != (self.value is not None) or (self.status == "ask") != (self.question is not None):
            raise ValueError(f"inconsistent {self.status} result: value={self.value!r}, question={self.question!r}")

    @property
    def date(self) -> date | None:
        return self.value.date if isinstance(self.value, DateValue) else None

    def to_dict(self) -> dict:
        d = {
            "status": self.status,
            "value": self.value.to_dict() if self.value else None,
            "question": self.question.to_dict() if self.question else None,
            "reason": self.reason,
            "period": self.period,
            "locale": self.locale,
            "source": {"text": self.source.text, "start": self.source.start, "end": self.source.end},
            "details": _details(self.value),
            "provenance": self.provenance.to_dict(),
        }
        if self.question:
            d["request"] = self.request.to_dict()  # sent back with the answer
        return d


def _details(v: Value | None) -> dict | None:
    if isinstance(v, DateValue):
        d = v.date
        return {
            "weekday": WEEKDAY_NAMES[d.weekday()],  # not strftime: that follows the host locale
            "iso_week": f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}",
            "quarter": (d.month - 1) // 3 + 1,
            "day_of_year": d.timetuple().tm_yday,
        }
    if isinstance(v, PeriodValue):
        return {"days": (v.end - v.start).days}
    return None


@dataclass(frozen=True)
class ColumnRead:
    """One day/month order for a whole column of cells, and each distinct cell read with it.

    order: MDY or DMY, or None when no cell depends on it or the question is still open.
    order_source: "session" (context.date_order), "evidence" (a cell only one order reads, e.g. 26/10/2026), or None.
    question: set when no cell decides (or cells disagree); answer it by reading the column again with
    TemporalContext(date_order=<the chosen option's value>).
    cells: each distinct cell -> ISO date, or None (not a date, against the order, or waiting on the question).
    filled: cells whose missing parts were filled by rule (a missing year: the next occurrence)."""

    order: str | None
    order_source: str | None
    question: Question | None
    cells: dict
    filled: dict
    evidence: dict

    def to_dict(self) -> dict:
        return {
            "order": self.order,
            "order_source": self.order_source,
            "question": self.question.to_dict() if self.question else None,
            "cells": self.cells,
            "filled": {cell: list(parts) for cell, parts in self.filled.items()},
            "evidence": self.evidence,
        }
