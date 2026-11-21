"""CLI: uv run python -m relay.rules day 2027-01-01"""
import sys
from collections import Counter

from relay.rules.core import check_day


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "day":
        print("usage: python -m relay.rules day YYYY-MM-DD", file=sys.stderr)
        return 2
    date = argv[1]
    try:
        from relay.ingest import load_plan
    except ImportError as e:
        print(f"relay.ingest is not available yet ({e}); cannot load the plan. Nothing checked.", file=sys.stderr)
        return 1
    plan = load_plan()
    findings = check_day(plan, date)
    counts = Counter((f.status, f.rule_id) for f in findings)
    for status in ("violation", "warning", "unknown", "ok"):
        group = [f for f in findings if f.status == status]
        if not group:
            continue
        print(f"\n== {status.upper()} ({len(group)}) ==")
        for f in group:
            src = ", ".join(s.label() for s in f.evidence[:3])
            print(f"[{f.rule_id}/{f.tier}] {f.line or '-'}: {f.message}" + (f"  <{src}>" if src else ""))
    print("\nsummary:", ", ".join(f"{r}={s}:{n}" for (s, r), n in sorted(counts.items())) or "no findings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
