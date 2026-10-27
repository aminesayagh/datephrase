"""A column of cells (an uploaded file's date column) read with ONE day/month order, decided by evidence."""

import re
from dataclasses import replace

from .models import ColumnRead, Question, TemporalConfigError, TemporalContext
from .reader import YEAR_FIRST, _parse, _say, read_temporal

ORDERS = ("MDY", "DMY")
PAIR = re.compile(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})")
SAYS = {"MDY": "month first", "DMY": "day first"}


def read_column(values, *, context: TemporalContext | None = None) -> ColumnRead:
    """Each distinct cell as an ISO date or None, all with one order.

    The order comes from the session (context.date_order), else from evidence: a cell only one order can read
    (26/10/2026: no month 26) settles it. With no evidence and at least one cell both orders read differently
    (01/02/2026), one question is asked for the whole column, never one per cell; so is it when cells disagree.
    Cells that do not depend on the order (2026-10-26, 26 Oct 2026) are read either way."""
    context = context or TemporalContext()
    if not isinstance(context, TemporalContext):
        raise TemporalConfigError("context must be a TemporalContext")
    cells = list(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))
    # ponytail: every distinct cell costs a few dateparser calls (~ms each); cache by cell if columns get large

    # the orders that read each cell as a day and a month of its own numbers ("12/2026" has none: order-free)
    reads = {cell: [o for o in ORDERS if _reads_as(cell, o)] for cell in cells if _order_matters(cell)}
    evidence, both = {o: [] for o in ORDERS}, []
    for cell, real in reads.items():
        if len(real) == 1:
            evidence[real[0]].append(cell)
        elif len(real) == 2 and len({_parse(cell, _settings(o)).date_obj.date() for o in real}) == 2:
            both.append(cell)

    order, source, question = context.date_order, "session" if context.date_order else None, None
    if order is None:
        decided = [o for o in ORDERS if evidence[o]]
        if len(decided) == 1:
            order, source = decided[0], "evidence"
        elif len(decided) == 2:
            question = _question("ORDER_CONFLICT", evidence["MDY"][0], evidence["DMY"][0], context, conflict=True)
        elif both:
            question = _question("DATE_ORDER_AMBIGUOUS", both[0], both[0], context)

    out, filled = {}, {}
    for cell in cells:
        real = reads.get(cell)
        if real and question and (cell in both or len(real) == 1):
            out[cell] = None  # waiting on the question ("02/02/2026", read alike either way, does not wait)
            continue
        if real and order in ORDERS and order not in real:
            out[cell] = None  # against the column's order (31/12 read month first)
            continue
        r = read_temporal(cell, context=replace(context, date_order=order) if order and real else context,
                          kind="date", anchor="absolute")
        out[cell] = r.date.isoformat() if r.status == "resolved" and r.date else None
        if out[cell] and r.provenance.filled:
            filled[cell] = r.provenance.filled
    return ColumnRead(order, source, question, out, filled, evidence)


def _order_matters(cell: str) -> bool:
    return bool(PAIR.search(cell)) and not YEAR_FIRST.match(cell)


def _reads_as(cell: str, order: str) -> bool:
    """Does dateparser read the cell in this order? Told month first, it silently reads 26/10/2026 day first, so its
    month and day must be the cell's own numbers in that order."""
    a, b = map(int, PAIR.search(cell).groups())
    d = _parse(cell, _settings(order))
    return bool(d) and (d.date_obj.month, d.date_obj.day) == ((a, b) if order == "MDY" else (b, a))


def _settings(order: str) -> dict:
    return {"PREFER_LOCALE_DATE_ORDER": False, "DATE_ORDER": order}


def _question(reason: str, mdy_cell: str, dmy_cell: str, context: TemporalContext, conflict: bool = False) -> Question:
    def reading(cell, order):  # what choosing this order gives, with every rule applied (next occurrence...)
        r = read_temporal(cell, context=replace(context, date_order=order), kind="date", anchor="absolute")
        return f"{SAYS[order]}, as in '{cell}' = {_say(r.date)}" if r.date else SAYS[order]
    if conflict:
        text = (f"This column mixes orders: '{dmy_cell}' only reads day first, '{mdy_cell}' only month first. "
                "Which order does the column use? Cells against it stay unread.")
    else:
        text = f"Are the dates in this column, such as '{mdy_cell}', written day first or month first?"
    return Question(reason, "date_order", text, "choice",
                    (("MDY", reading(mdy_cell, "MDY")), ("DMY", reading(dmy_cell, "DMY"))))
