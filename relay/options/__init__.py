"""Options for one incident, scored on the six AXES and ranked.

Counting, from the steps only:
  * each abholen / abstellen / beistellen / shunt_out step = one shunting move;
  * each extra_transport step = one extra transport, priced from costs.json by the network.json link
    between its place and the car's next place (cost_key), else costs.json `default` (marked assumption);
  * each detour step = costs.json detour.delay_min_low minutes (low end) and the midpoint of eur_min..eur_max;
  * a shunting move has no euro price (costs.json shunting_move.eur is null). eur is None when nothing in
    the option can be priced, never 0.

Ranking (non-refused options of ES's own levers; rank 1 = best), in this order:
  1. fewer extra transports
  2. fewer shunting moves                       (§1/§9: keep shunting to a minimum)
  3. fewer shunting moves in Belgian yards       (§11: the sent order was chosen "to prevent shunting
                                                  and exchanges in Belgium")
  4. lower delay minutes
  5. lower euros (an option that cannot be priced sorts after a priced one)
Refused options (any blocking finding) get rank 0 and sort last. Proposals (levers of ours, proposed=True)
are not ranked against ES's levers: rank 0, listed after the ranked options and before the refused ones.
"""
from __future__ import annotations

import json

from relay.config import REFERENCE
from relay.model import AXES, PRODUCTS, AxisScore, Finding, Option, Step

MOVE_VERBS = ("abholen", "abstellen", "beistellen", "shunt_out")
RESTRICTIVE = {
    "PAR": "Le Bourget: shunting only under very strict rules TCS does not comply with today (§7, table 3)",
    "ELEN": "Lengerich: one access track, southbound; efficient only with train 474 (table 6)",
    "SCH": "Schaarbeek: Prague train on Mondays; an exchange with Milan then needs a same-day transport (§2)",
}
NOT_CHECKED = "rules not checked: the rule checker (relay.rules) was not available"


def _load(name):
    return json.loads((REFERENCE / name).read_text())


def _train_lines(network, scenario=None) -> dict[str, str]:
    out = {}
    for key, ln in network["lines"].items():
        out[ln["outbound"]] = key
        out[ln["return"]] = key
    for num, t in ((scenario or {}).get("trains") or {}).items():
        out[num] = t["line"]
    return out


# ---------------------------------------------------------------- counting

def _transport_cost(steps, i, network, costs):
    """(eur, fact) for the extra_transport at index i."""
    s = steps[i]
    dest = next((t.at for t in steps[i + 1:] if t.car == s.car and t.at and t.at != s.at), "")
    for lk in network["links"]:
        if {lk["from"], lk["to"]} == {s.at, dest} and lk.get("cost_key") in costs["extra_transport"]:
            c = costs["extra_transport"][lk["cost_key"]]
            return c["eur"], f"Extra transport {s.at}–{dest} for {s.car}: €{c['eur']:,} — {c['cite']}"
    c = costs["extra_transport"]["default"]
    return c["eur"], (f"Extra transport {s.at}–{dest or '?'} for {s.car}: €{c['eur']:,}, no priced link in "
                      f"network.json, so the default is used (ASSUMPTION) — {c['cite']}")


def _count(opt: Option, network, costs):
    """Set shunting_moves, extra_transports, delay_min, eur from the steps. Returns money facts."""
    be = {k for k, p in network["places"].items() if p.get("country") == "BE"}
    opt.shunting_moves = sum(s.verb in MOVE_VERBS for s in opt.steps)
    opt.extra_transports = sum(s.verb == "extra_transport" for s in opt.steps)
    d = costs["detour"]
    detours = sum(s.verb == "detour" for s in opt.steps)
    opt.delay_min = detours * d["delay_min_low"]
    eur, facts = None, []
    for i, s in enumerate(opt.steps):
        if s.verb == "extra_transport":
            e, f = _transport_cost(opt.steps, i, network, costs)
            eur = (eur or 0) + e
            facts.append(f)
        elif s.verb == "detour":
            mid = (d["eur_min"] + d["eur_max"]) // 2
            eur = (eur or 0) + mid
            facts.append(f"Detour of train {s.train}: €{mid:,}, the midpoint of €{d['eur_min']:,}–€{d['eur_max']:,} "
                         f"(ASSUMPTION: midpoint) — {d['cite']}")
    opt.eur = eur
    belgium = sum(s.verb in MOVE_VERBS and s.at in be for s in opt.steps)
    return facts, belgium


def _rank_key(o: Option, belgium: int):
    return (o.extra_transports, o.shunting_moves, belgium, o.delay_min,
            o.eur if o.eur is not None else float("inf"))


# ---------------------------------------------------------------- rules

def _get_check(check):
    if check is not None:
        return check
    try:
        from relay.rules import check_steps  # lane B; may not exist yet
        return check_steps
    except Exception:
        return None


def _apply_rules(opt: Option, check, context):
    if check is None:
        return False
    try:
        found = list(check(opt.steps, context))
    except Exception as exc:  # a broken checker must not look like a pass
        context.setdefault("_errors", []).append(f"{opt.id}: {exc!r}")
        return False
    opt.findings.extend(found)
    return True


def _rules_axis(opt: Option, checked: bool) -> AxisScore:
    blocking = [f for f in opt.findings if f.blocks]
    opt.refused = bool(blocking)
    opt.refused_by = list(dict.fromkeys(f.rule_id for f in blocking))
    if not checked and not opt.findings:
        return AxisScore(0, "rules not checked", [f"{NOT_CHECKED} — relay.options"])
    warn = [f for f in opt.findings if f.status == "warning"]
    unk = [f for f in opt.findings if f.status == "unknown"]
    facts = [f"{f.status.upper()} {f.rule_id}: {f.message} — {f.cite}" for f in blocking + warn + unk]
    if not checked:
        facts.append(f"{NOT_CHECKED} — relay.options")
    if blocking:
        return AxisScore(0, f"refused by {', '.join(opt.refused_by)}", facts)
    score = max(10, 100 - 15 * len(warn) - 5 * len(unk))
    head = f"0 rules broken, {len(warn)} warning(s), {len(unk)} unknown"
    if unk:
        facts.append(f"{len(unk)} rule(s) could not be checked; unknown is not a pass — rules.json")
    if checked and not facts:
        facts.append("The rule checker returned no warning or violation for these steps — rules.json via check_steps")
    return AxisScore(score, head, facts)


# ---------------------------------------------------------------- axes

def _axes(opt, *, checked, money_facts, belgium, product_facts, product_score, pax, costs, line_of):
    a = {"rules": _rules_axis(opt, checked)}
    a["products"] = AxisScore(product_score, product_facts[0][0], [f for _, f in product_facts])

    tcs = opt.shunting_moves + opt.extra_transports + sum(s.verb == "detour" for s in opt.steps)
    arena = any(line_of.get(s.train) == "Milan" and s.verb in ("beistellen", "swap") for s in opt.steps)
    ws = sorted({s.at for s in opt.steps if s.at in ("ELEN", "EMSTG", "CRA")})
    pf = [f"TCS: {tcs} action(s) (moves, transports, detours) and 1 re-sent task order — §6: TCS does the car "
          "exchanges and the shunting; §9: the order to TCS is sent again"]
    if arena:
        pf.append("Arenaways: the changed car runs into Italy on the Milan train — §6: Arena Ways runs the train in Italy")
    for w in ws:
        pf.append(f"Workshop {w}: a collection or drop to arrange — table 6")
    if opt.proposed:
        pf.append("Proposal: partner actions not counted — relay.options")
    n = tcs + len(ws) + int(arena)
    a["partners"] = AxisScore(max(0, 100 - 5 * n), f"{n} partner action(s)", pf)

    places = sorted({s.at for s in opt.steps if s.verb in MOVE_VERBS and s.at in RESTRICTIVE})
    inf = [f"{opt.shunting_moves} shunting move(s): one per abholen/abstellen/beistellen/shunt_out step — "
           "relay.options count; §9: keep shunting to a minimum"]
    inf += [f"Uses {RESTRICTIVE[p]}" for p in places]
    if belgium:
        inf.append(f"{belgium} move(s) in Belgium — §11: the sent order avoided shunting and exchanges in Belgium")
    if any(s.at == "ELEN" and s.train == "474" for s in opt.steps):
        inf.append("A southbound Lengerich exchange of one car in and one out takes about eight moves; this count "
                   "uses one per step, not that figure — table 6")
    a["infrastructure"] = AxisScore(max(0, 100 - 5 * opt.shunting_moves - 10 * len(places) - 5 * belgium),
                                    f"{opt.shunting_moves} shunting moves", inf)

    dm = opt.delay_min
    tf = [f"{dm} min delay from detours (low end of {costs['detour']['delay_min_low']}–"
          f"{costs['detour']['delay_min_high']} min) — {costs['detour']['cite']}"]
    if dm > 120:
        tf.append(f"Over 2 hours: 50% refund — {costs['refund']['cite']}")
    elif dm > 60:
        tf.append(f"Over 1 hour: 25% refund — {costs['refund']['cite']}")
    if not any(s.verb == "detour" for s in opt.steps):
        tf.append("Shunting time is not given per move in the rulebook; delay from moves is not estimated — relay.options")
    a["time"] = AxisScore(100 if dm == 0 else 60 if dm <= 60 else 40 if dm <= 120 else 20, f"{dm} min delay", tf)

    mf = list(money_facts)
    eur = opt.eur
    share = costs["refund"]["over_120_min"] if dm > 120 else costs["refund"]["over_60_min"] if dm > 60 else 0
    if share and pax is not None:
        fare = costs["avg_fare_eur"]["eur"]
        ref = int(pax * fare * share)
        eur = (eur or 0) + ref
        mf.append(f"Refund estimate {pax} pax × €{fare} × {share:.0%} = €{ref:,}; rests on the €{fare} average-fare "
                  f"ASSUMPTION — {costs['avg_fare_eur']['cite']}")
    elif share:
        mf.append("Refunds due but no passenger count is known, so not estimated — costs.json refund")
    opt.eur = eur
    if opt.shunting_moves:
        mf.append(f"{opt.shunting_moves} shunting move(s) not priced — {costs['shunting_move']['cite']}")
    if eur is None:
        a["money"] = AxisScore(50, "cannot be priced", mf or ["Nothing in this option has a price — costs.json"])
    else:
        a["money"] = AxisScore(max(0, 100 - eur // 200), f"€{eur:,}", mf)
    opt.axes = {k: a[k] for k in AXES}


def _finish(opts, extras, *, check, context, network, costs, line_of, pax):
    checked_any = check is not None
    for o in opts:
        x = extras[o.id]
        money, belgium = _count(o, network, costs)
        o.pax_affected = pax
        ok = _apply_rules(o, check, context) if checked_any else False
        _axes(o, checked=ok, money_facts=money, belgium=belgium, product_facts=x["products"],
              product_score=x["product_score"], pax=pax, costs=costs, line_of=line_of)
        x["belgium"] = belgium
    ranked = sorted([o for o in opts if not o.refused and not o.proposed],
                    key=lambda o: _rank_key(o, extras[o.id]["belgium"]))
    for i, o in enumerate(ranked, 1):
        o.rank = i
    for o in opts:
        if o.refused or o.proposed:
            o.rank = 0
    return ranked + [o for o in opts if o.proposed and not o.refused] + [o for o in opts if o.refused]


# ---------------------------------------------------------------- scenario mode (§11)

# What each car in the Friday was wanted for. Free text in the rulebook, so written here with its sentence.
FRIDAY_WANTS = [
    ("71 002", "Paris", "§11: 71 002 ... was needed in the Paris train"),
    ("71 120", "Milan", "§11: the first order took 71 120 to the Prague train, with the aim of passing it on to "
                        "the Milan train for extra capacity"),
    ("91 309", "Paris", "table 8: 91 309 parked in Denderleeuw and wanted in the Paris train"),
]


def _st(d, note=""):
    return Step(d["verb"], d.get("car", ""), d.get("at", ""), d.get("train", ""), note or d.get("note", "") or
                (f"aim: {d['aim']}" if d.get("aim") else ""))


def _scenario_steps(scenario):
    first = scenario["first_order"]
    by_id = {o["id"]: o for o in scenario["options_in_rulebook"]}
    sent_json = by_id["sent"]["steps"]
    # The sent list in the JSON leaves out where four cars were collected; table 8 and the first order say so.
    sent = []
    for d in sent_json:
        if d["verb"] != "abholen" and not any(s.car == d["car"] for s in sent):
            pick = next((f for f in first if f["car"] == d["car"] and f["verb"] == "abholen"), None)
            if pick:
                sent.append(_st(pick, f"implied: the first order collects it here; table 8: {d['car']} was "
                                      "collected before it was added or parked"))
        sent.append(_st(d, "sent order (friday.json); §11, table 8"))

    keep = [_st(d, "first order (friday.json); §11") for d in first] + [
        Step("shunt_out", "71 120", "DDL", "452", "§9: brought to Denderleeuw on the Prague line ... (train 452 = "
             "Prague return, table 0; ASSUMPTION: the westbound run is the one that reaches Denderleeuw)"),
        Step("beistellen", "71 120", "DDL", "401", "§9: shunted into the Milan train there when it is in; "
             "§11 superseded option: would need shunting and exchanges in Belgium"),
    ]
    p309 = [
        Step("beistellen", "91 309", "DDL", "453", "§11 (a): collecting it in Denderleeuw on the Prague train"),
        Step("shunt_out", "91 309", "HBTH", "453", "§11 (a): taking it to Bad Bentheim"),
        Step("extra_transport", "91 309", "HBTH", "", "§11 (a): moving it to the Paris train with an extra transport"),
        Step("beistellen", "91 309", "ELEN", "474", "§11 (a): ... to the Paris train (474 passes Lengerich, table 6; "
             "ASSUMPTION: the hand-over is at Lengerich)"),
    ]
    rej_a = [Step(s.verb, s.car, s.at, s.train, s.note) for s in sent] + p309
    rej_b = [Step(s.verb, s.car, s.at, s.train, s.note) for s in sent
             if not (s.car == "71 120" and s.verb == "beistellen")] + p309 + [
        Step("beistellen", "71 120", "HBTH", "452", "§11 (b): swapping it for 71 120, which would then travel via Bad "
             "Bentheim (ASSUMPTION: on the westbound Prague train 452, table 0)"),
        Step("shunt_out", "71 120", "SCH", "452", "§11 (b): ... Schaarbeek; §2: on Mondays the Prague train goes to "
             "Schaarbeek"),
        Step("extra_transport", "71 120", "SCH", "", "§2: exchanging cars between the two that day needs a transport "
             "on the same day (network.json link DDL–SCH)"),
        Step("beistellen", "71 120", "DDL", "401", "§11 (b): ... and Denderleeuw into the Milan train"),
    ]
    return {"sent": sent, "keep_first_order": keep, "rejected_a": rej_a, "rejected_b": rej_b}


def _scenario_products(steps, line_of, wants):
    if not wants:
        return 50, [("product effect UNKNOWN", "No product data for this scenario — relay.options")]
    got, facts = 0, []
    for car, line, cite in wants:
        final = [s for s in steps if s.car == car and s.verb == "beistellen"]
        ok = bool(final) and line_of.get(final[-1].train) == line
        got += ok
        facts.append(f"{car} {'reaches' if ok else 'does not reach'} the {line} train — {cite}")
    score = round(100 * got / len(wants))
    return score, [(f"{got} of {len(wants)} wanted cars delivered", f) for f in facts]


def _scenario(incident, scenario, network, costs, check):
    line_of = _train_lines(network, scenario)
    steps = _scenario_steps(scenario)
    wants = FRIDAY_WANTS if scenario.get("id") == "friday_s11" else []
    opts, extras = [], {}
    for o in scenario["options_in_rulebook"]:
        opt = Option(o["id"], o["title"], steps=steps.get(o["id"], []), origin="rulebook")
        score, pf = _scenario_products(opt.steps, line_of, wants)
        extras[o["id"]] = {"products": pf, "product_score": score}
        opts.append(opt)
    context = {"cars": {c["car"]: {"owner": c["owner"], "type": c["type"]} for c in scenario.get("cars", [])},
               "trains": {n: {"line": l} for n, l in line_of.items()}, "has_pax": set(incident.get("has_pax", []))}
    return _finish(opts, extras, check=check, context=context, network=network, costs=costs,
                   line_of=line_of, pax=incident.get("pax"))


# ---------------------------------------------------------------- generic mode

SWAP_AT = {"Milan": ("DDL", "table 7: shunted out when in Denderleeuw"),
           "Prag": ("DDL", "table 7: shunted out when in Denderleeuw"),
           "Paris": ("WUS", "§7: a broken car goes to Wustermark or straight to the workshop")}
LEND_S13 = ("§13: Swapping cars between lines during the night run. The Milan and Paris trains share part of their "
            "route, but tracks have to be booked, there is no shunting capacity, DB InfraGO restricts it, and any "
            "delay breaks the plan.")
DETOUR_S13 = ("§13: Taking the Prague train through the workshop areas as a detour. The detour and the change of "
              "locomotive side cost 2 to 3 hours of delay. For Milano line not at all possible (too far away)")


def _can_carry(car_type: str, product: str, rules) -> tuple[bool, str]:
    p = next((r for r in rules["rules"] if r["id"] == "product_matches_car"), {"params": {}})["params"]
    cp, seat = p.get("always_cp", []), p.get("always_seat", [])
    if car_type in cp:
        return product == "4", ""
    if car_type in seat:
        return product == "2", ""
    if product == "4":
        return False, ""
    if product == "3":
        return True, "whether it is a mattress (CS) car is UNKNOWN (order tabs) — §3"
    return product in ("1", "2"), ""


def _generic(incident, plan, network, costs, rules, check):
    car, line, date = incident["car"], incident["line"], incident["date"]
    kind = incident.get("kind", "car_broken")
    line_of = _train_lines(network)
    train = incident.get("train") or network["lines"][line]["outbound"]
    tnote = "" if incident.get("train") else (f"train {train}: ASSUMPTION, outbound number of the {line} line "
                                             "(table 0); the plan cell has no direction")
    at, at_cite = incident.get("place") and (incident["place"], "incident") or SWAP_AT.get(line, ("", ""))
    cells = [c for c in plan.cells if c.date == date]
    mine = next((c for c in cells if c.car == car and c.line == line), None)
    product = mine.product if mine else ""
    pname = PRODUCTS.get(product, "UNKNOWN product")
    src = mine.src.label() if mine and mine.src else line
    info = plan.cars.get(car)
    owner = info.owner if info else (mine.owner if mine else "")
    ws = network["workshops"].get(owner, {})
    out = Step("shunt_out", car, at, train, f"{at_cite}; {tnote}".strip("; "))
    opts, extras = [], {}

    def add(opt, delivered: bool | None, pfacts):
        score = 50 if delivered is None else 100 if delivered else 0
        head = (f"{pname} delivered" if delivered else f"{pname} loses a car" if delivered is False
                else "product effect UNKNOWN")
        extras[opt.id] = {"products": [(head, f) for f in pfacts], "product_score": score}
        opts.append(opt)

    busy = {c.car for c in cells if c.kind in ("in_train", "shunt_in", "shunt_out", "transfer", "revision")}
    seen, cands = set(), []
    for c in sorted(cells, key=lambda c: (c.line != line, c.car)):
        if c.kind in ("reserve", "parked") and c.car != car and c.car not in busy and c.car not in seen:
            seen.add(c.car)
            ok, why = _can_carry(c.type, product, rules)
            if ok:
                cands.append((c, why))
    for c, why in cands[:5]:
        steps = [Step(out.verb, out.car, out.at, out.train, out.note)]
        csrc = c.src.label() if c.src else c.line
        if c.place and c.place != at:
            steps += [Step("abholen", c.car, c.place, "", f"reserve parked at {c.place} ({csrc})"),
                      Step("extra_transport", c.car, c.place, "", f"from {c.place} to {at}")]
        elif c.place:
            steps.append(Step("abholen", c.car, c.place, "", f"reserve parked at {c.place} ({csrc})"))
        else:
            steps.append(Step("abholen", c.car, "", "", f"code {c.raw or c.kind}: where it stands is not in the "
                                                        f"plan cell, UNKNOWN; a transport may be needed ({csrc})"))
        steps.append(Step("beistellen", c.car, at, train, f"{c.owner} {c.type} takes over {pname}"))
        f = [f"{c.car} ({c.owner} {c.type}) is reserve/parked on {date} and can carry {pname} — {csrc}; rules.json "
             "product_matches_car"]
        if why:
            f.append(why)
        add(Option(f"swap_{c.car.replace(' ', '')}", f"Swap in reserve {c.car} for {car}", steps), True, f)
    if not cands:
        pass  # no reserve: nothing invented

    short = [Step(out.verb, out.car, out.at, out.train, out.note),
             Step("downgrade", car, at, train, "table 7: passengers are spread over other cars, private compartments "
                                               "may be downgraded to shared")]
    add(Option("run_short", f"Run short: take {car} out, spread or downgrade its passengers", short), False,
        [f"{pname} on {car} loses its car; passengers spread over other cars or downgraded — table 7; {src}",
         "In the worst case the booking is cancelled — §3: people might need to be downgraded or even canceled"])

    if ws:
        wp = ws["place"]
        tr = short + [Step("extra_transport", car, at, "", f"to the {owner} workshop {wp} — {ws['cite']}"),
                      Step("abstellen", car, wp, "", f"parked at the workshop — {ws['cite']}")]
        add(Option("workshop_transport", f"Run short and send {car} to {wp} by extra transport", tr), False,
            [f"{pname} on {car} still loses its car tonight — table 7; {src}"])

    if ws and line in ("Prag", "Milan"):
        d = Option("detour", f"Detour train {train} through the {ws['place']} workshop area to drop {car}",
                   [Step("detour", "", ws["place"], train, DETOUR_S13),
                    Step("shunt_out", car, ws["place"], train, f"dropped at the workshop — {ws['cite']}")])
        if line == "Milan":
            d.findings.append(Finding("detour_line", "hard", "violation",
                                      "A detour through the workshop areas is not possible on the Milan line.",
                                      DETOUR_S13, line, date, [car]))
        add(d, False, [f"{pname} on {car} loses its car unless a replacement waits at the workshop — {src}"])

    if kind == "car_no_hv":
        back = [Step("shunt_out", car, at, train, "table 7: no high voltage, the car can only run at the back"),
                Step("beistellen", car, at, train, "re-added at the back, empty — §5: it can only run empty at the "
                                                   "back of the train, and not on a run where the locomotive changes sides"),
                Step("downgrade", car, at, train, "table 7: passengers must move out; spread over other cars")]
        add(Option("hv_at_back", f"Run {car} empty at the back", back), False,
            [f"{pname} on {car} carries no passengers — table 7",
             "Whether the locomotive changes sides on this run is UNKNOWN from the plan — §5"])

    if line in ("Milan", "Paris"):
        other = "Paris" if line == "Milan" else "Milan"
        lend = Option("p_lend", f"PROPOSAL: lend a car from the {other} train to the {line} train where their routes meet",
                      [Step("swap", "", "", network["lines"][other]["outbound"],
                            f"which car: the planner's choice; the {other} train loses that car's product")],
                      proposed=True)
        add(lend, None, [f"Not ES practice today. {LEND_S13}"])
    pre = Option("p_preposition", f"PROPOSAL: keep an approved {pname} reserve pre-positioned at {at}", [],
                 proposed=True)
    add(pre, None, ["Ours, not ES policy — rules.json p_no_reserve_in_reach",
                    "Not ES practice today. §13: Parking cars outside a workshop over the weekend. Graffiti risk"])

    cars_ctx = {k: {"owner": v.owner, "type": v.type} for k, v in plan.cars.items()}
    context = {"cars": cars_ctx, "trains": {n: {"line": l} for n, l in line_of.items()} | {train: {"line": line}},
               "has_pax": set(incident.get("has_pax", []))}
    return _finish(opts, extras, check=check, context=context, network=network, costs=costs,
                   line_of=line_of | {train: line}, pax=incident.get("pax"))


def options_for(incident: dict, scenario: dict | None = None, *, plan=None, rules=None,
                network=None, costs=None, check=None) -> list[Option]:
    network = network or _load("network.json")
    costs = costs or _load("costs.json")
    rules = rules or _load("rules.json")
    check = _get_check(check)
    if scenario and scenario.get("options_in_rulebook"):
        return _scenario(incident, scenario, network, costs, check)
    if incident.get("kind") in ("car_broken", "car_no_hv") and plan is not None:
        return _generic(incident, plan, network, costs, rules, check)
    return []
