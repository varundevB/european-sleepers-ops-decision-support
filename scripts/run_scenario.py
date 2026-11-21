"""Run one incident through every engine and build the demo page.

  uv run python scripts/run_scenario.py friday
  uv run python scripts/run_scenario.py night Milan 2027-01-12 "91 003" [--relax approval_country]

Writes build/<name>.json and build/<name>.html. Every figure comes from the engines or reference/.
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from relay import audit  # noqa: E402
from relay.cascade import impact_of  # noqa: E402
from relay.config import BUILD, REFERENCE  # noqa: E402
from relay.docs import decision_record, partner_messages, task_order_de  # noqa: E402
from relay.ingest import load_plan  # noqa: E402
from relay.model import to_dict  # noqa: E402
from relay.options import options_for  # noqa: E402
from relay.rules import check_day, check_steps, load_rules  # noqa: E402
from views.build import build  # noqa: E402

USER = "fleet planner (demo)"


def _ref(name):
    return json.loads((REFERENCE / name).read_text(encoding="utf-8"))


def _validation():
    p = BUILD / "validation.json"
    if not p.exists():
        return None
    v = json.loads(p.read_text())
    pl, ops = v["plan"], v["ops"]["total"]
    fut = pl["future_hard_violations"]
    n_fut = sum(fut.values())
    facts = [
        f"Ahead of today ({v['today']}): {fut.get('double_planned', 0)} car-nights where one car is written into two "
        f"trains, and {fut.get('max_length', 0)} nights with more cars written than the shortest platform limit.",
        "Over-length is a flag to check, not a proven error: the Paris and Prague trains can change length on the "
        "route (rulebook §5) and the grid does not show where.",
        f"Whole plan, {pl['days']} days ({pl['first']} to {pl['last']}), hard-rule flags: "
        + ", ".join(f"{k} {n}" for k, n in sorted(pl["hard_violations"].items(), key=lambda x: -x[1])) + ".",
    ]
    for k, ex in pl["future_examples"].items():
        if ex:
            facts.append(f"Example — {ex[0]['message']}")
    facts.append(
        f"Operations log: {ops['over_60']} of {ops['arrivals']} logged arrivals were more than 60 minutes late, "
        f"{ops['over_120']} more than 120. {ops['pax_on_late_trains']:,} passengers were on those trains.")
    facts.append(
        f"Refund exposure on those arrivals: about €{ops['refund_exposure_eur']:,} — an estimate: 25 % / 50 % refund "
        f"(rulebook) x passengers (log) x the assumed €{v['ops']['avg_fare_eur']} average fare. Not a saving claim.")
    return {"headline": f"{n_fut} conflicts are written into the plan ahead of today — found in under 2 seconds",
            "facts": facts}


BASE_ASSUMPTIONS = [
    "Average fare €100 = €41m ticket sales / 410,000 passengers (ES briefing slide). The data has no ticket prices.",
    "A shunting move has no euro price in the rulebook; moves are counted, not priced.",
    "Which rules are truly hard is not confirmed by ES. Tiers live in reference/rules.json and can be changed.",
    "Options marked PROPOSAL are ours, not ES policy.",
]


def assemble(name, title, subtitle, incident, scenario, plan, rules, network, costs, cars, findings, extra_assumptions):
    impact = impact_of(incident, scenario, plan=plan, network=network)
    # The options lane reads rules in the file's shape and calls check(steps, context); pass both
    # from the same (possibly overridden) rule set so a relaxed tier reaches the verdicts.
    options = options_for(incident, scenario, plan=plan, rules={"rules": list(rules.values())}, network=network,
                          costs=costs, check=lambda st, ctx: check_steps(st, ctx, rules=rules, network=network))
    if not options:
        raise SystemExit("the engines produced no option for this incident")
    allowed = [o for o in options if not o.refused and not o.proposed]
    if not allowed:
        raise SystemExit("every option is refused; nothing to choose")
    chosen = min(allowed, key=lambda o: o.rank)
    at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    log = BUILD / "audit.jsonl"
    ok, msg = audit.verify(log)
    prev = msg.rsplit(" ", 1)[-1] if ok and "last hash" in msg else "0" * 64
    h = audit.append(log, decision_record(incident, chosen, options, USER, at))
    order = task_order_de(chosen, scenario, network=network,
                          supersedes=1 if scenario.get("first_order") else None, audit_hash=h)
    data = {
        "title": title, "subtitle": subtitle, "generated_at": at, "network": network,
        "incident": incident, "cars": cars,
        "impact": to_dict(impact), "options": to_dict(options), "chosen": chosen.id,
        "documents": {"task_order_de": order,
                      "messages": partner_messages(chosen, scenario, impact, network=network),
                      "audit": {"hash": h, "prev": prev, "at": at, "user": USER}},
        "findings": to_dict(findings), "validation": _validation(),
        "assumptions": BASE_ASSUMPTIONS + extra_assumptions,
    }
    BUILD.mkdir(exist_ok=True)
    js = BUILD / f"{name}.json"
    js.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    out = build(js, BUILD / f"{name}.html")
    print(f"{out}  chosen={chosen.id}  audit={audit.verify(log)[1]}")
    for o in sorted(options, key=lambda o: (o.rank == 0, o.rank)):
        tag = "REFUSED by " + ",".join(o.refused_by) if o.refused else ("PROPOSAL" if o.proposed else "")
        print(f"  rank {o.rank}  {o.id:22} eur={o.eur} moves={o.shunting_moves} transports={o.extra_transports} {tag}")
    return data


def friday(plan, network, costs):
    sc = _ref("scenarios/friday.json")
    rules = load_rules()
    all_opts = options_for(sc["incident"], sc, plan=plan, network=network, costs=costs)
    findings = [f for o in all_opts if o.id == "sent" for f in o.findings]
    return assemble(
        "friday", "Relay · The real Friday (rulebook §11)", "A Friday — the rulebook gives no date",
        sc["incident"], sc, plan, rules, network, costs, sc["cars"], findings,
        ["Tie-break between the order that was sent and the first order: fewer moves in Belgian yards (rulebook §11).",
         "The rulebook's 'eight moves at Lengerich' figure is quoted but not used in our move count.",
         "Extra transport Schaarbeek–Denderleeuw has no listed price; the €5,500 default is an assumption."])


def night(plan, network, costs, line, date, car, relax):
    rules = load_rules(overrides={r: "soft" for r in relax} if relax else None)
    meta = plan.cars.get(car)
    if meta is None:
        raise SystemExit(f"car {car!r} is not in the plan")
    cell = next((c for c in plan.cells if c.car == car and c.date == date and c.line == line), None)
    if cell is None:
        raise SystemExit(f"{car} has no cell on {line} {date}")
    line_def = next(l for l in network["lines"] if l["id"] == line) if isinstance(network["lines"], list) \
        else network["lines"][line]
    incident = {"id": f"inc-{date}-{car.replace(' ', '')}", "kind": "car_broken", "car": car, "line": line,
                "date": date, "place": cell.place or line_def.get("origin", ""),
                "note": f"Car {car} ({meta.owner} {meta.type}) reported broken before departure. "
                        f"The plan has it in the {line} train: {cell.src.sheet}!{cell.src.cell} = {cell.raw!r}.",
                "cite": f"{cell.src.file} · {cell.src.sheet}!{cell.src.cell}"}
    scenario = {"id": incident["id"], "title": f"{line} train, {date}", "date": date, "trains": {}, "cars": []}
    cars = [{"car": car, "owner": meta.owner, "type": meta.type, "start": incident["place"], "note": "broken"}]
    name = f"night_{line.lower()}_{date}" + ("_relaxed" if relax else "")
    sub = f"A real night from the plan · {cell.src.sheet}!{cell.src.cell}"
    if relax:
        sub = "WHAT IF · " + ", ".join(relax) + " soft · not ES policy"
    return assemble(name, f"Relay · {line} train, {date}", sub, incident, scenario, plan, rules, network, costs,
                    cars, check_day(plan, date, rules=rules, network=network, costs=costs),
                    ["Reserve candidates come from the plan grid; whether a car is physically fit is not in the data."]
                    + ([f"WHAT IF: rule {r} is treated as soft here. This is not ES policy." for r in relax]))


def main(argv):
    network, costs = _ref("network.json"), _ref("costs.json")
    plan = load_plan()
    if argv[:1] == ["friday"]:
        friday(plan, network, costs)
    elif argv[:1] == ["night"] and len(argv) >= 4:
        relax = argv[argv.index("--relax") + 1:] if "--relax" in argv else []
        night(plan, network, costs, argv[1], argv[2], argv[3], relax)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
