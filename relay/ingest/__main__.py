"""CLI: where <car> <date> | train <line> <date> | summary | ops | orders."""
from __future__ import annotations

import sys
from collections import Counter

from relay.ingest import car_id, composition, load_ops, load_orders, load_plan, where_is
from relay.ingest.ops import LAST_DROPPED
from relay.model import PRODUCTS

USAGE = ('usage: python -m relay.ingest where "71 455" 2026-10-02 | train Paris 2026-10-02 | '
         'summary | ops | orders')


def _line(c) -> str:
    what = c.kind
    if c.product:
        what += f" {c.product} ({PRODUCTS[c.product]})"
    if c.place:
        what += f" at {c.place}"
    colour = f"  colour {c.colour} = {c.colour_meaning}" if c.colour else ""
    return f"{c.car:>7}  {c.raw!r:>8}  {what}{colour}  [{c.src.label()}]"


def main(argv: list[str]) -> int:
    if not argv:
        print(USAGE)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "where" and len(args) == 2:
        plan = load_plan()
        if not car_id(plan, args[0]):
            print(f"car {args[0]!r} is not in row 3 of any grid sheet")
            return 1
        hits = where_is(plan, args[0], args[1])
        cid = car_id(plan, args[0])
        print(f"{cid} on {args[1]}:")
        for ln in plan.cars[cid].lines:
            c = next((h for h in hits if h.line == ln), None)
            print(f"  {ln:6} " + (_line(c) if c else "no entry (empty cell, no fill)"))
        return 0
    if cmd == "train" and len(args) == 2:
        cells = composition(load_plan(), args[0], args[1])
        print(f"{args[0]} {args[1]}: {len(cells)} cars (in_train / shunt_in / shunt_out)")
        for c in cells:
            print("  " + _line(c))
        return 0
    if cmd == "summary":
        plan = load_plan()
        print(f"cars: {len(plan.cars)}  cells: {len(plan.cells)}  legend colours: {len(plan.legend)}")
        for k, n in Counter(c.kind for c in plan.cells).most_common():
            print(f"  {k:12} {n}")
        print(f"rejects: {len(plan.rejects)}")
        for r in plan.rejects[:15]:
            print(f"  {r['sheet']}!{r['cell']}  {r['raw']!r}  {r['reason']}")
        return 0
    if cmd == "ops":
        nights = load_ops()
        print(f"nights: {len(nights)}  by line: {dict(Counter(n.line for n in nights))}")
        print(f"dropped: {LAST_DROPPED}")
        return 0
    if cmd == "orders":
        o = load_orders()
        print(f"items: {len(o['items'])}  by sheet: {dict(Counter(i['sheet'] for i in o['items']))}")
        print(f"unread: {o['unread']}")
        print(o["note"])
        return 0
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
