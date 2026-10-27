"""python -m datephrase "October 26, 2026" --context '{"reference_date": "2026-10-26"}'"""

import argparse
import json

from . import TemporalContext, read_temporal

parser = argparse.ArgumentParser(prog="datephrase")
parser.add_argument("text")
parser.add_argument("--context", help="JSON object with TemporalContext fields")
parser.add_argument("--kind", choices=["date", "period", "duration"])
parser.add_argument("--anchor", choices=["absolute", "relative"])
parser.add_argument("--direction", choices=["past", "future"])
args = parser.parse_args()
ctx = TemporalContext.from_dict(json.loads(args.context)) if args.context else None
print(json.dumps(read_temporal(args.text, context=ctx, kind=args.kind, anchor=args.anchor, direction=args.direction).to_dict(), indent=2))
