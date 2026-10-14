"""Apply captured historical delivery queries in explicit bounded batches."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dingtalk_delivery_repair import apply_delivery_reverification  # noqa: E402
from app.store import AutoReplyStore  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--db", type=Path, required=True)
parser.add_argument("--proofs", type=Path, required=True)
parser.add_argument("--offset", type=int, default=0)
parser.add_argument("--limit", type=int, default=10)
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
proofs = json.loads(args.proofs.read_text())[args.offset:args.offset + args.limit]
if args.offset < 0 or not 1 <= args.limit <= 10:
    parser.error("require a nonnegative offset and a batch limit from 1 to 10")
store = AutoReplyStore(args.db) if args.apply else None
for proof in proofs:
    applied = apply_delivery_reverification(store, proof) if args.apply else False
    print(json.dumps({"key": proof["key"], "state": proof["verification"]["state"], "applied": applied}))
