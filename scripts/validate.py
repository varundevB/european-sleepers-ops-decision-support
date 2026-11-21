"""Scan the whole plan with the rule checker and read the operations log. Writes build/validation.json.

Nothing here is a forecast. Refund exposure is an estimate resting on the stated average-fare assumption.
"""
import collections
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from relay.config import BUILD, REFERENCE  # noqa: E402
from relay.ingest import load_ops, load_plan  # noqa: E402
from relay.rules import check_day  # noqa: E402


class _Slice:
    def __init__(self, cells, cars):
        self.cells, self.cars = cells, cars


def scan_plan(plan, today: str) -> dict:
    by = collections.defaultdict(list)
    for c in plan.cells:
        by[c.date].append(c)
    dates = sorted(by)
    out = collections.Counter()
    future = collections.Counter()
    examples = collections.defaultdict(list)
    for d in dates:
        lo = (datetime.date.fromisoformat(d) - datetime.timedelta(days=10)).isoformat()
        window = [c for k in dates if lo <= k <= d for c in by[k]] if False else None
        cells = []
        dd = datetime.date.fromisoformat(d)
        for i in range(11):
            cells += by.get((dd - datetime.timedelta(days=i)).isoformat(), [])
        for f in check_day(_Slice(cells, plan.cars), d):
            if f.tier == "hard" and f.status == "violation":
                out[f.rule_id] += 1
                if d >= today:
                    future[f.rule_id] += 1
                    if len(examples[f.rule_id]) < 4:
                        examples[f.rule_id].append({"date": d, "message": f.message})
    return {"days": len(dates), "first": dates[0], "last": dates[-1], "hard_violations": dict(out),
            "future_hard_violations": dict(future), "future_examples": dict(examples)}


def read_ops(costs) -> dict:
    fare = costs["avg_fare_eur"]["eur"]
    r25, r50 = costs["refund"]["over_60_min"], costs["refund"]["over_120_min"]
    res = {}
    tot = collections.Counter()
    for line in ("Prag", "Paris", "Milan"):
        res[line] = collections.Counter()
    for n in load_ops():
        line = n.line if n.line in res else next((k for k in res if k.lower()[:3] in n.line.lower()), None)
        if line is None:
            continue
        c = res[line]
        if n.arr_delay is None:
            continue
        c["arrivals"] += 1
        if n.arr_delay > 60:
            c["over_60"] += 1
        if n.arr_delay > 120:
            c["over_120"] += 1
        if n.arr_delay > 60 and n.pax:
            share = r50 if n.arr_delay > 120 else r25
            c["pax_on_late_trains"] += n.pax
            c["refund_exposure_eur"] += round(n.pax * fare * share)
        if (n.downgrades or 0) > 0 or (n.cancellations or 0) > 0:
            c["nights_with_downgrade_or_cancel"] += 1
    for line, c in res.items():
        tot.update(c)
    return {"per_line": {k: dict(v) for k, v in res.items()}, "total": dict(tot), "avg_fare_eur": fare}


def main():
    today = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    costs = json.loads((REFERENCE / "costs.json").read_text())
    plan = load_plan()
    out = {"today": today, "plan": scan_plan(plan, today), "ops": read_ops(costs)}
    BUILD.mkdir(exist_ok=True)
    (BUILD / "validation.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
