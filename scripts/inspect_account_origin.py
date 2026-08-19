"""Inspect the earliest visible Core activity for a supplied account."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from hl_reconciler.hypercore import HyperCoreClient
from hl_reconciler.timeutils import parse_local_cutoff, to_millis


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("address")
    p.add_argument("--cutoff", required=True, help="ISO local/UTC cutoff")
    p.add_argument("--timezone", default="UTC")
    p.add_argument("--days", type=int, default=45)
    args = p.parse_args()

    core = HyperCoreClient()
    cutoff_ms = to_millis(parse_local_cutoff(args.cutoff, args.timezone))
    end_ms = cutoff_ms + args.days * 86400000
    fills = core.fills_range(args.address, cutoff_ms + 1, end_ms)
    funding = core.funding_range(args.address, cutoff_ms + 1, end_ms)
    ledger = core.ledger_range(args.address, cutoff_ms + 1, end_ms)

    events = []
    for source, rows in (("fill", fills), ("funding", funding), ("ledger", ledger)):
        for row in rows:
            events.append({"source": source, "time": int(row.get("time") or 0), "row": row})
    events.sort(key=lambda x: x["time"])

    print(json.dumps({
        "address": args.address,
        "cutoff_ms": cutoff_ms,
        "cutoff_utc": datetime.fromtimestamp(cutoff_ms / 1000, timezone.utc).isoformat(),
        "first_events": events[:50],
        "counts": {"fills": len(fills), "funding": len(funding), "ledger": len(ledger)},
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
