"""Benchmark maintained date engines on the 300 cases: detection (find the date in the sentence) and resolution
(read the date phrase into a value). Adapters only translate each engine's own answer into the same shape;
none adds date logic. Run with Duckling on 127.0.0.1:8000 and chrono-node installed in CHRONO_DIR.

    uv run --with datefinder python benchmarks/engines/run.py
"""

import json
import os
import statistics
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import dateparser
from dateparser.date import DateDataParser
from dateparser.search import search_dates
from dateutil.relativedelta import relativedelta

HERE = Path(__file__).parent
REF = datetime(2026, 10, 27, tzinfo=timezone.utc)
CASES = [json.loads(line) for line in (HERE / "cases.jsonl").read_text().splitlines()]
CHRONO_DIR = os.environ.get("CHRONO_DIR")
DUCKLING = os.environ.get("DUCKLING", "http://127.0.0.1:8000/parse")
MONTHS_PER = {"month": 1, "quarter": 3, "year": 12}
SECONDS_PER = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "fortnight": 1209600}


# ---------- shared shapes ----------

def as_date(d):
    return {"kind": "date", "date": d.isoformat()}


def as_period(start, end):
    return {"kind": "period", "start": start.isoformat(), "end": end.isoformat()}


def shape(d, grain, kind):
    """The engine's date in the shape the classifier asked for (the same labels datephrase receives): a date stays the
    date the engine returned; a period is the whole period at the engine's own grain."""
    return as_date(d) if kind != "period" else widen(d, grain)


def widen(d, grain):
    """A whole period from an engine-reported grain (week starts Monday); a finer grain stays a date."""
    if grain == "week":
        s = d - timedelta(days=d.weekday())
        return as_period(s, s + timedelta(days=7))
    if grain == "month":
        s = d.replace(day=1)
        return as_period(s, s + relativedelta(months=1))
    if grain == "quarter":
        s = date(d.year, (d.month - 1) // 3 * 3 + 1, 1)
        return as_period(s, s + relativedelta(months=3))
    if grain == "year":
        return as_period(date(d.year, 1, 1), date(d.year + 1, 1, 1))
    return as_date(d)


def length(quantity, unit):
    unit = unit.rstrip("s")
    if unit in MONTHS_PER:
        return ("months", quantity * MONTHS_PER[unit])
    return ("seconds", quantity * SECONDS_PER[unit])


# ---------- engines: resolve(span) -> value | "ask" | None ; detect(text) -> span text | None ----------

def dp_resolve(span, kind):
    data = DateDataParser(languages=["en"], settings={"RELATIVE_BASE": REF.replace(tzinfo=None),
                                                       "PREFER_DATES_FROM": "future"}).get_date_data(span)
    if not data or not data.date_obj:
        return None
    return shape(data.date_obj.date(), data.period, kind)


def dp_search_detect(text):
    found = search_dates(text, languages=["en"], settings={"RELATIVE_BASE": REF.replace(tzinfo=None),
                                                           "PREFER_DATES_FROM": "future"})
    return found[0][0] if found else None


def dp_search_resolve(span):
    found = search_dates(span, languages=["en"], settings={"RELATIVE_BASE": REF.replace(tzinfo=None),
                                                           "PREFER_DATES_FROM": "future"})
    return as_date(found[0][1].date()) if found else None


def duckling(text):
    body = urllib.parse.urlencode({"locale": "en_US", "text": text, "tz": "UTC", "dims": '["time","duration"]',
                                   "reftime": int(REF.timestamp() * 1000)}).encode()
    with urllib.request.urlopen(DUCKLING, body, timeout=10) as r:
        return [e for e in json.loads(r.read()) if not e.get("latent")]


def duckling_value(e, kind):
    v = e["value"]
    if e["dim"] == "duration":
        return {"kind": "duration", "quantity": v["value"], "unit": v["unit"]}
    if v["type"] == "interval":
        start, end = v.get("from"), v.get("to")
        if not (start and end):
            return None
        return as_period(date.fromisoformat(start["value"][:10]), date.fromisoformat(end["value"][:10]))
    return shape(date.fromisoformat(v["value"][:10]), v["grain"], kind)


def duckling_resolve(span, kind):
    entities = duckling(span)
    return duckling_value(max(entities, key=lambda e: e["end"] - e["start"]), kind) if entities else None


def duckling_detect(text):
    entities = duckling(text)
    return max(entities, key=lambda e: e["end"] - e["start"])["body"] if entities else None


class Chrono:
    def __init__(self):
        self.p = subprocess.Popen(["node", str(HERE / "chrono_worker.js")], cwd=CHRONO_DIR, text=True,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=os.environ | {
                                      "TZ": "UTC", "NODE_PATH": f"{CHRONO_DIR}/node_modules"})

    def parse(self, text):
        self.p.stdin.write(json.dumps({"text": text, "ref": REF.isoformat()}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())


chrono = None


def chrono_value(r, kind):
    start = r["start"]
    d = date.fromisoformat(start["date"][:10])
    if r.get("end"):
        return as_period(d, date.fromisoformat(r["end"]["date"][:10]) + timedelta(days=1))
    known = set(start["known"])
    if kind != "period" or "day" in known or "weekday" in known or not known & {"month", "year"}:
        return as_date(d)
    return widen(d, "month" if "month" in known else "year")


def chrono_resolve(span, kind):
    results = chrono.parse(span)
    return chrono_value(results[0], kind) if results else None


def chrono_detect(text):
    results = chrono.parse(text)
    return results[0]["text"] if results else None


def datefinder_resolve(span):
    import datefinder
    found = list(datefinder.find_dates(span, base_date=REF.replace(tzinfo=None)))
    return as_date(found[0].date()) if found else None


def datefinder_detect(text):
    import datefinder
    found = list(datefinder.find_dates(text, source=True, base_date=REF.replace(tzinfo=None)))
    return found[0][1] if found else None


def datephrase_resolve(case):
    from datephrase import TemporalContext, read_temporal
    r = read_temporal(case["span"], context=TemporalContext(reference_date=REF.date()), kind=case["kind"],
                      anchor=case["anchor"], direction=case["direction"])
    if r.status == "ask":
        return "ask"
    if r.status != "resolved":
        return None
    v = r.value.to_dict()
    return {k: v[k] for k in ("kind", "date") if k in v} if v["kind"] == "date" else (
        {k: v[k] for k in ("kind", "start", "end")} if v["kind"] == "period" else
        {"kind": "duration", "quantity": v["quantity"], "unit": v["unit"]})


ENGINES = {  # name: (resolve(case) -> value, detect(text) -> span or None)
    "datephrase (Duckling contract)": (datephrase_resolve, None),
    "dateparser.parse": (lambda c: dp_resolve(c["span"], c["kind"]), None),
    "dateparser.search_dates": (lambda c: dp_search_resolve(c["span"]), dp_search_detect),
    "duckling": (lambda c: duckling_resolve(c["span"], c["kind"]), duckling_detect),
    "chrono-node": (lambda c: chrono_resolve(c["span"], c["kind"]), chrono_detect),
    "datefinder": (lambda c: datefinder_resolve(c["span"]), datefinder_detect),
}


# ---------- scoring ----------

def judge(expected, got, alternative=None):
    """correct | convention (the other reading of "next <weekday>") | wrong (a silent wrong value) | refused | asked."""
    if alternative and got == alternative:
        return "convention"
    if expected["kind"] == "ambiguous":
        return "correct" if got == "ask" else "refused" if got is None else "wrong"
    if got == "ask":
        return "asked"
    if got is None:
        return "refused"
    if got["kind"] != expected["kind"]:
        return "wrong"
    if got["kind"] == "date":
        return "correct" if got["date"] == expected["date"] else "wrong"
    if got["kind"] == "period":
        return "correct" if (got["start"], got["end"]) == (expected["start"], expected["end"]) else "wrong"
    try:
        same = length(got["quantity"], got["unit"]) == length(expected["quantity"], expected["unit"])
    except KeyError:
        same = False
    return "correct" if same else "wrong"


def detected(case, found):
    if found is None:
        return "missed"
    f, s = found.strip(" .,").lower(), case["span"].lower()
    if f == s:
        return "exact"
    return "overlap" if s in f or f in s else "wrong_span"


def main():
    global chrono
    chrono = Chrono()
    rows, summary = [], {}
    for name, (resolve, detect) in ENGINES.items():
        times, outcomes, spans = [], {}, {}
        for case in CASES:
            t = time.perf_counter()
            try:
                got = resolve(case)
            except Exception as e:  # an engine crash is reported, never hidden
                got = {"kind": "error", "error": f"{type(e).__name__}: {e}"[:120]}
            times.append((time.perf_counter() - t) * 1000)
            verdict = "error" if isinstance(got, dict) and got.get("kind") == "error" else judge(case["expected"], got, case.get("alternative"))
            outcomes[case["id"]] = verdict
            row = {"engine": name, "id": case["id"], "family": case["family"], "span": case["span"],
                   "expected": case["expected"], "got": got, "verdict": verdict}
            if detect:
                try:
                    found = detect(case["text"])
                except Exception as e:
                    found = None
                spans[case["id"]] = detected(case, found)
                row["detected"], row["detection"] = found, spans[case["id"]]
            rows.append(row)
        summary[name] = {"outcomes": outcomes, "spans": spans, "p50_ms": statistics.median(times),
                         "p95_ms": sorted(times)[int(len(times) * 0.95)]}
    (HERE / "results.jsonl").write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    (HERE / "report.md").write_text(report(summary))
    print(report(summary))


def report(summary):
    families = list(dict.fromkeys(c["family"] for c in CASES))
    out = ["# Date engines on 300 single-date texts (reference Tuesday 2026-10-27)", "",
           "## Resolution: the date phrase alone -> value", "",
           "| Engine | Correct | Other convention | Wrong (silent) | Refused | Asked | p50 ms | p95 ms |",
           "|---|---|---|---|---|---|---|---|"]
    for name, s in summary.items():
        v = list(s["outcomes"].values())
        out.append(f"| {name} | {v.count('correct')} | {v.count('convention')} | {v.count('wrong') + v.count('error')} | {v.count('refused')} | "
                   f"{v.count('asked')} | {s['p50_ms']:.1f} | {s['p95_ms']:.1f} |")
    out += ["", "## Detection: find the date in the sentence", "", "| Engine | Exact span | Overlapping | Wrong span | Missed |",
            "|---|---|---|---|---|"]
    for name, s in summary.items():
        if s["spans"]:
            v = list(s["spans"].values())
            out.append(f"| {name} | {v.count('exact')} | {v.count('overlap')} | {v.count('wrong_span')} | {v.count('missed')} |")
    out += ["", "## Correct / wrong by family", "", "| Family | n | " + " | ".join(summary) + " |",
            "|---|---|" + "---|" * len(summary)]
    for fam in families:
        ids = [c["id"] for c in CASES if c["family"] == fam]
        cells = []
        for s in summary.values():
            v = [s["outcomes"][i] for i in ids]
            cells.append(f"{v.count('correct')} / {v.count('wrong') + v.count('error')}")
        out.append(f"| {fam} | {len(ids)} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main()
