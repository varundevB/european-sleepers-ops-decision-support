"""Run one incident through every engine and build the demo page.

  uv run python scripts/run_scenario.py friday
  uv run python scripts/run_scenario.py night Milan 2027-01-12 "91 003" [--strict]

By default the rulebook is a baseline: bending it needs a sign-off, it does not refuse the option.
--strict shows the same night with the rulebook applied as law.

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
        f"Whole plan, {pl['days']} days ({pl['first']} to {pl['last']}), rule flags: "
        + ", ".join(f"{k} {n}" for k, n in sorted(pl["hard_violations"].items(), key=lambda x: -x[1]) if k != "product_matches_car") + ".",
        f"Separately, {pl['hard_violations'].get('product_matches_car', 0)} cells carry a product code that does not fit "
        "the car type in the rulebook's table. Most are a seating type the rulebook does not list — a grid inconsistency "
        "to tidy, not a rule broken.",
    ]
    for k, ex in pl["future_examples"].items():
        if ex:
            facts.append(f"Example — {ex[0]['message']}")
    facts.append(
        f"Operations log: {ops['over_60']} of {ops['arrivals']} logged arrivals were more than 60 minutes late, "
        f"{ops['over_120']} more than 120. {ops['pax_on_late_trains']:,} passengers were on those trains.")
    facts.append(
        f"Refund exposure on those arrivals: about €{ops['refund_exposure_eur']:,} — an estimate: 25 % / 50 % refund "
        f"(rulebook) x the log's own count of passengers more than 1 h late ({ops.get('late_rows_with_logged_count', 0)} "
        f"of {ops['over_60']} late arrivals have one; the rest count everyone aboard) x the assumed "
        f"€{v['ops']['avg_fare_eur']} average fare. Ceiling if everyone aboard claimed: "
        f"€{ops.get('refund_upper_bound_eur', 0):,}. Not a saving claim.")
    return {"headline": f"{n_fut} conflicts are written into the plan ahead of today — found in under 2 seconds",
            "facts": facts}


BASE_ASSUMPTIONS = [
    "Average fare €100 = €41m ticket sales / 410,000 passengers (ES briefing slide). The data has no ticket prices.",
    "A shunting move is priced at €300 — our placeholder; the rulebook gives no price. Change it in reference/costs.json.",
    "A car taken out is priced as 35 passengers × €100 refunded in full — the worst case, our assumption.",
    "Every total is split: the part from ES's cost table and the part resting on our assumptions.",
    "The rulebook is treated as a baseline: bending a rule needs a sign-off, it does not refuse the option. "
    "Only what cannot physically happen is refused.",
    "Options marked PROPOSAL are ours, not ES policy.",
]


CASE_TYPES = [
    ["Unavailable car (defect)", "built", "night scenarios: a car reported broken before departure"],
    ["Changed release date from a workshop", "built", "the Friday case, rulebook §11: a car released earlier than planned"],
    ["Maintenance deadline or workshop delay", "not built", ""],
    ["Transfer of cars between lines", "partly", "appears as an action and as a PROPOSAL; not a case type of its own"],
    ["Product or capacity mismatch", "partly", "found by the whole-plan scan; no actions generated yet"],
    ["Late train or missed exchange", "not built", ""],
    ["Unavailable shunting, transport or partner service", "not built", ""],
    ["Timetable change", "not built", ""],
]


def _live(line, date, network):
    """Public running data for that night, if scripts/public_night.py has cut it. None otherwise."""
    p = REFERENCE / "public" / f"infrabel_{date}.json"
    if not p.exists():
        return None
    pub = json.loads(p.read_text(encoding="utf-8"))
    from relay.ingest.ops import load_ops
    day = datetime.date.fromisoformat(date)
    before = (day - datetime.timedelta(days=1)).isoformat()
    rows = [{"line": n.line, "date": n.date, "dep_delay": n.dep_delay, "arr_delay": n.arr_delay, "pax": n.pax,
             "pax_over_1h": n.pax_over_1h, "downgrades": n.downgrades, "cancellations": n.cancellations,
             "note": n.note, "cite": f"{n.src.file} · {n.src.sheet}!{n.src.cell}"}
            for n in load_ops() if n.date in (before, date) and (n.line == line or n.date == date)]
    return {"date": date, "train": pub["train"], "planned_dep": pub["points"][0]["plan_dep"],
            "source": pub["source"], "licence": pub["licence"], "url": pub["url"],
            "points": pub["points"], "fleet": pub["fleet"], "ops": rows, "line": line,
            "label": "REPLAY · ES workbook + Infrabel open data (CC0) · not a live feed"}


def assemble(name, title, subtitle, incident, scenario, plan, rules, network, costs, cars, findings, extra_assumptions,
             live=None):
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
    by_option = {}
    for o in options:
        if o.refused or o.proposed:
            continue
        try:
            by_option[o.id] = {
                "task_order_de": task_order_de(o, scenario, network=network,
                                               supersedes=1 if scenario.get("first_order") else None,
                                               audit_hash=h if o.id == chosen.id else None),
                "messages": partner_messages(o, scenario, impact, network=network)}
        except Exception as e:  # a document we cannot draft is shown as missing, never invented
            by_option[o.id] = {"error": f"{type(e).__name__}: {e}"}
    data = {
        "live": live, "case_types": CASE_TYPES, "user": USER,
        "title": title, "subtitle": subtitle, "generated_at": at, "network": network,
        "incident": incident, "cars": cars,
        "impact": to_dict(impact), "options": to_dict(options), "chosen": chosen.id,
        "documents": {"task_order_de": order,
                      "messages": partner_messages(chosen, scenario, impact, network=network),
                      "by_option": by_option,
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
    rules = load_rules(advisory=True)
    all_opts = options_for(sc["incident"], sc, plan=plan, network=network, costs=costs)
    findings = [f for o in all_opts if o.id == "sent" for f in o.findings]
    return assemble(
        "friday", "Relay · The real Friday (rulebook §11)", "A Friday — the rulebook gives no date",
        sc["incident"], sc, plan, rules, network, costs, sc["cars"], findings,
        ["Tie-break between the order that was sent and the first order: fewer moves in Belgian yards (rulebook §11).",
         "The rulebook's 'eight moves at Lengerich' figure is quoted but not used in our move count.",
         "Extra transport Schaarbeek–Denderleeuw has no listed price; the €5,500 default is an assumption."])


def night(plan, network, costs, line, date, car, strict):
    rules = load_rules(advisory=not strict)
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
    name = f"night_{line.lower()}_{date}" + ("_strict" if strict else "")
    sub = f"A real night from the plan · {cell.src.sheet}!{cell.src.cell}"
    if strict:
        sub = "WHAT IF · the rulebook applied as law"
    return assemble(name, f"Relay · {line} train, {date}", sub, incident, scenario, plan, rules, network, costs,
                    cars, check_day(plan, date, rules=rules, network=network, costs=costs),
                    ["Reserve candidates come from the plan grid; whether a car is physically fit is not in the data."]
                    + ([f"The plan cell for this night already shows code {cell.raw} (in train, not commercial): the grid records "
                        "what was done. The actions are priced as if the car were still sold, the worst case."]
                       if str(cell.raw) == "9" else [])
                    + (["WHAT IF: every baseline rule is applied as a hard refusal on this page."] if strict else []),
                    live=_live(line, date, network))


def main(argv):
    network, costs = _ref("network.json"), _ref("costs.json")
    plan = load_plan()
    if argv[:1] == ["friday"]:
        friday(plan, network, costs)
    elif argv[:1] == ["night"] and len(argv) >= 4:
        night(plan, network, costs, argv[1], argv[2], argv[3], "--strict" in argv)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
