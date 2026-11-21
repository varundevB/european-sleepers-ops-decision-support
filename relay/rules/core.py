"""Rule checker. Rules are data: tier, cite and params come from reference/rules.json.

Status: a hard rule gives "violation", a soft or proposed rule gives "warning", a rule that lacks
data gives "unknown". "ok" findings are only returned with include_ok=True.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from relay.config import REFERENCE
from relay.model import TIERS, Cell, Finding, Step

TRAIN_KINDS = ("in_train", "shunt_in", "shunt_out")
COMMERCIAL = ("1", "2", "3", "4")
MOVE_VERBS = ("abholen", "abstellen", "beistellen", "shunt_out", "swap", "extra_transport")


# ---------- loading ----------

def _json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_rules(path=REFERENCE / "rules.json", overrides: dict[str, str] | None = None) -> dict:
    """rule_id -> rule dict. overrides: rule_id -> tier, e.g. {"approval_country": "soft"}."""
    rules = {r["id"]: dict(r) for r in _json(path)["rules"]}
    for rid, tier in (overrides or {}).items():
        if rid not in rules:
            raise KeyError(f"unknown rule id in overrides: {rid}")
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
        rules[rid]["tier"] = tier
    return rules


def _defaults(rules, network, costs=None):
    rules = rules if rules is not None else load_rules()
    network = network if network is not None else _json(REFERENCE / "network.json")
    costs = costs if costs is not None else _json(REFERENCE / "costs.json")
    return rules, network, costs


# ---------- helpers ----------

def _broken(rule: dict) -> str:
    return "violation" if rule["tier"] == "hard" else "warning"


def _find(rule: dict, status: str, message: str, line="", date="", cells=(), cars=None) -> Finding:
    if rule["tier"] == "proposed" and not message.startswith("PROPOSAL:"):
        message = "PROPOSAL: " + message
    cars = cars if cars is not None else list(dict.fromkeys(c.car for c in cells))
    return Finding(rule_id=rule["id"], tier=rule["tier"], status=status, message=message,
                   cite=rule["cite"], line=line, date=date, cars=list(cars),
                   evidence=[c.src for c in cells if c.src is not None])


def _countries(network: dict, line: str) -> list[str]:
    return list(network.get("lines", {}).get(line, {}).get("countries", []))


def _denials(rule: dict, owner: str, car: str, ctype: str, countries: list[str]) -> list[str]:
    """Countries on the route where this car is denied by params.deny."""
    out = []
    for d in rule["params"].get("deny", []):
        if d["country"] not in countries:
            continue
        if d.get("owner", "").lower() != (owner or "").lower():
            continue
        if "car_suffix" in d and not car.replace(" ", "").endswith(d["car_suffix"]):
            continue
        if "type_prefix" in d and not (ctype or "").upper().startswith(d["type_prefix"].upper()):
            continue
        out.append(d["country"])
    return out


# ---------- one function per rule (train level) ----------

def rule_approval_country(rule, cells, line, date, network, include_ok=False) -> list[Finding]:
    countries = _countries(network, line)
    unknown_owners = {o.lower() for o in rule["params"].get("unknown_owners", [])}
    out = []
    for c in cells:
        out += _approval_one(rule, c.car, c.owner, c.type, line, date, countries, unknown_owners,
                             cells=[c], include_ok=include_ok)
    return out


def _approval_one(rule, car, owner, ctype, line, date, countries, unknown_owners, cells=(),
                  include_ok=False, how="runs in") -> list[Finding]:
    if not countries:
        return [_find(rule, "unknown", f"Car {car}: line {line!r} has no country list, approval cannot be checked.",
                      line, date, cells, [car])]
    if not owner or owner.lower() in unknown_owners:
        return [_find(rule, "unknown", f"Car {car} (owner {owner or 'not given'}, {ctype}) {how} the {line} train "
                      f"through {', '.join(countries)}, but the rulebook has no approval statement for this owner.",
                      line, date, cells, [car])]
    denied = _denials(rule, owner, car, ctype, countries)
    if denied:
        return [_find(rule, _broken(rule), f"Car {car} ({owner}, {ctype}) {how} the {line} train, which runs "
                      f"through {', '.join(denied)}, where this car is not approved.", line, date, cells, [car])]
    if include_ok:
        return [_find(rule, "ok", f"Car {car} ({owner}, {ctype}) is approved for {', '.join(countries)}.",
                      line, date, cells, [car])]
    return []


def rule_max_length(rule, cells, line, date, network, include_ok=False) -> list[Finding]:
    limits = rule["params"].get("limits", {})
    countries = _countries(network, line)
    limited = {k: limits[k] for k in countries if k in limits}
    if not limited:
        return [_find(rule, "unknown", f"No length limit is known for any country on the {line} line.",
                      line, date, cells)]
    limit = min(limited.values())
    where = "/".join(k for k, v in limited.items() if v == limit)
    base = sum(c.kind == "in_train" for c in cells)
    s_in = sum(c.kind == "shunt_in" for c in cells)
    s_out = sum(c.kind == "shunt_out" for c in cells)
    can_change = network["lines"][line].get("can_change_length_en_route", False)
    total = base + s_in + s_out
    if not can_change:
        if total > limit:
            return [_find(rule, _broken(rule), f"The {line} train has {total} cars on {date} ({base} in train, "
                          f"{s_in} shunted in, {s_out} shunted out); the limit is {limit} ({where}) and this line "
                          f"cannot change length en route.", line, date, cells)]
        return [_find(rule, "ok", f"The {line} train has {total} cars, within the limit of {limit} ({where}).",
                      line, date, cells)] if include_ok else []
    if base > limit:
        return [_find(rule, _broken(rule), f"The {line} train has {base} cars in train on {date} (plus {s_in} "
                      f"shunted in, {s_out} shunted out); the limit is {limit} ({where}).", line, date, cells)]
    if total > limit:
        return [_find(rule, "unknown", f"The {line} train has {base} cars in train, within the limit of {limit} "
                      f"({where}), plus {s_in} shunted in and {s_out} shunted out ({total} in all); whether it is too "
                      f"long depends on the country where the exchange happens, which the grid does not say.",
                      line, date, cells)]
    return [_find(rule, "ok", f"The {line} train has {base} cars in train plus {s_in} shunted in and {s_out} "
                  f"shunted out, within the limit of {limit} ({where}).", line, date, cells)] if include_ok else []


def rule_product_matches_car(rule, cells, line, date, include_ok=False) -> list[Finding]:
    cp = set(rule["params"].get("always_cp", []))
    seat = set(rule["params"].get("always_seat", []))
    out = []
    for c in cells:
        if c.kind != "in_train":
            continue
        t = (c.type or "").strip()
        if t in cp:
            want, name = ("4", "9"), "Comfort Plus (4)"
        elif t in seat:
            want, name = ("2", "9"), "seating (2)"
        else:
            continue
        if not c.product:
            out.append(_find(rule, "unknown", f"Car {c.car} ({t}) is in the {line} train but its product is not "
                             f"readable.", line, date, [c]))
        elif c.product not in want:
            out.append(_find(rule, _broken(rule), f"Car {c.car} ({t}) is always {name} but is planned as product "
                             f"{c.product} in the {line} train.", line, date, [c]))
        elif include_ok:
            out.append(_find(rule, "ok", f"Car {c.car} ({t}) is planned as product {c.product}.", line, date, [c]))
    return out


def rule_product_mix(rule, cells, line, date, include_ok=False) -> list[Finding]:
    counts = {}
    for c in cells:
        if c.kind == "in_train" and c.product:
            counts[c.product] = counts.get(c.product, 0) + 1
    short = {p: (n, counts.get(p, 0)) for p, n in rule["params"].get("min", {}).items() if counts.get(p, 0) < n}
    if short:
        parts = ", ".join(f"product {p}: {have} of {need}" for p, (need, have) in short.items())
        return [_find(rule, _broken(rule), f"The {line} train on {date} sells too few cars of a product ({parts}).",
                      line, date, [c for c in cells if c.kind == "in_train"])]
    return [_find(rule, "ok", f"The {line} train has the minimum product mix.", line, date, cells)] if include_ok else []


def rule_second_conductor(rule, cells, line, date, costs, include_ok=False) -> list[Finding]:
    need = rule["params"].get("from_occupied_cars", 9)
    comm = [c for c in cells if c.kind == "in_train" and c.product in COMMERCIAL]
    if len(comm) >= need:
        sc = costs.get("second_conductor", {})
        eur = sc.get("eur", {}).get(line)
        money = f"about €{eur:,} per one-way trip ({sc.get('cite', '')})" if eur is not None else "cost UNKNOWN"
        return [_find(rule, _broken(rule), f"The {line} train has {len(comm)} commercial cars on {date} (from {need} "
                      f"a second conductor is needed): {money}.", line, date, comm)]
    return [_find(rule, "ok", f"The {line} train has {len(comm)} commercial cars, one conductor suffices.",
                  line, date, comm)] if include_ok else []


def rule_door_orientation(rule, cells, line, date, order=None, include_ok=False) -> list[Finding]:
    """Reading of rulebook §5 "Doors": a sleeper has both doors at one end; at its doorless end the
    passengers rely on the neighbouring car having doors right after the gangway. Directly behind the
    locomotive or at the tail there is no such neighbour, so a sleeper first in the train must have its
    doors at the front (the locomotive end) and a sleeper last must have them at the rear (the outer end).
    Doors at the inner end are wrong. In the middle, a sleeper whose doorless end faces another sleeper
    needs that sleeper's doors at the shared gangway (non-sleepers have doors at both ends, so are fine).
    door_end is relative to the train: "front" = towards the locomotive."""
    sleepers = set(rule["params"].get("sleeper_types", []))
    if order is None:
        sl = [c for c in cells if (c.type or "").strip() in sleepers]
        if not sl:
            return []
        return [_find(rule, "unknown", f"The {line} train on {date} has sleeper(s) {', '.join(c.car for c in sl)} "
                      f"but the car order and door ends are not given, so door positions cannot be checked.",
                      line, date, sl)]
    out = []
    n = len(order)
    is_sl = [(o.get("type") or "").strip() in sleepers for o in order]
    for i, o in enumerate(order):
        if not is_sl[i]:
            continue
        car, de = o.get("car", "?"), o.get("door_end")
        if de not in ("front", "rear"):
            out.append(_find(rule, "unknown", f"Sleeper {car} at position {i + 1} of {n}: door end not given.",
                             line, date, cars=[car]))
            continue
        bad = None
        if i == 0 and de != "front":
            bad = f"Sleeper {car} stands directly behind the locomotive with its doors at the inner (rear) end."
        elif i == n - 1 and de != "rear":
            bad = f"Sleeper {car} is last in the train (position {n}) with its doors at the inner (front) end."
        else:
            j = i + 1 if de == "front" else i - 1          # neighbour at the doorless end
            want = "front" if de == "front" else "rear"    # neighbour's end facing this car
            if 0 <= j < n and is_sl[j] and order[j].get("door_end") != want:
                if order[j].get("door_end") is None:
                    out.append(_find(rule, "unknown", f"Sleeper {car} has a sleeper neighbour {order[j].get('car')} "
                                     f"at its doorless end whose door end is not given.", line, date,
                                     cars=[car, order[j].get("car", "?")]))
                    continue
                bad = (f"Sleeper {car} (position {i + 1}) has its doorless end against sleeper "
                       f"{order[j].get('car')}, which has no doors at that gangway either.")
        if bad:
            out.append(_find(rule, _broken(rule), bad, line, date, cars=[car]))
        elif include_ok:
            out.append(_find(rule, "ok", f"Sleeper {car} doors are at a usable end.", line, date, cars=[car]))
    return out


def rule_high_voltage_rear(rule, cells, line, date, order=None, include_ok=False) -> list[Finding]:
    """§5: a car without high voltage may only run empty, last in the train. Whether the locomotive
    changes sides on the run is not in any file, so a last-placed no-HV car is never reported "ok"."""
    if order is None:
        return [_find(rule, "unknown", f"The high-voltage state and order of the cars in the {line} train on "
                      f"{date} are not given, so this cannot be checked.", line, date, cells)]
    out = []
    n = len(order)
    for i, o in enumerate(order):
        car, hv = o.get("car", "?"), o.get("hv")
        if hv is None:
            out.append(_find(rule, "unknown", f"Car {car} (position {i + 1} of {n}): high-voltage state not given.",
                             line, date, cars=[car]))
        elif hv is False and i != n - 1:
            out.append(_find(rule, _broken(rule), f"Car {car} has no high voltage but stands at position {i + 1} of "
                             f"{n}; it breaks power to the {n - 1 - i} car(s) behind it and must be last.",
                             line, date, cars=[car]))
        elif hv is False:
            out.append(_find(rule, "unknown", f"Car {car} has no high voltage and is last; it must run empty and "
                             f"the locomotive must not change sides on the run, which no file states.",
                             line, date, cars=[car]))
    if not out and include_ok:
        out.append(_find(rule, "ok", f"All {n} cars in the {line} train have high voltage.", line, date, cells))
    return out


def rule_workshop_interval(rule, line="", date="") -> list[Finding]:
    return [_find(rule, "unknown", "No odometer reading exists in any provided file, so the 55,000-60,000 km "
                  "workshop interval cannot be checked for any car.", line, date, cars=[])]


# ---------- day level ----------

def rule_double_planned(rule, cells, date, include_ok=False) -> list[Finding]:
    by_car: dict[str, list[Cell]] = {}
    for c in cells:
        if c.date == date and c.kind in TRAIN_KINDS:
            by_car.setdefault(c.car, []).append(c)
    out = []
    for car, cs in by_car.items():
        lines = sorted({c.line for c in cs})
        if len(lines) > 1:
            where = ", ".join(f"{c.line} ({c.src.label() if c.src else 'no source'})" for c in cs)
            out.append(_find(rule, _broken(rule), f"Car {car} is planned in {len(lines)} trains on {date}: {where}.",
                             "", date, cs, [car]))
    return out


def rule_p_repeat_defect(rule, cells, date, cars, include_ok=False) -> list[Finding]:
    """PROPOSAL: count distinct dates in the window (ending on `date`) with a not-usable / pay-attention fill."""
    win = rule["params"].get("window_days", 10)
    need = rule["params"].get("min_marks", 3)
    end = dt.date.fromisoformat(date)
    start = (end - dt.timedelta(days=win - 1)).isoformat()
    marks: dict[str, dict[str, Cell]] = {}
    for c in cells:
        if c.car in cars and start <= c.date <= date:
            m = (c.colour_meaning or "").lower()
            if "not usable" in m or "pay attention" in m:
                marks.setdefault(c.car, {}).setdefault(c.date, c)
    out = []
    for car, by_date in sorted(marks.items()):
        if len(by_date) >= need:
            cs = list(by_date.values())
            out.append(_find(rule, "warning", f"PROPOSAL: car {car} was marked not usable / pay attention on "
                             f"{len(by_date)} days between {start} and {date} (threshold {need}).", "", date, cs, [car]))
    return out


# ---------- public API ----------

def check_train(cells: list[Cell], line: str, date: str, *, rules=None, network=None,
                order: list[dict] | None = None, costs=None, include_ok=False,
                include_unknown_static=False) -> list[Finding]:
    """All train-level rules for one train-night. cells = that train's cells (in_train/shunt_in/shunt_out)."""
    rules, network, costs = _defaults(rules, network, costs)
    cells = [c for c in cells if c.kind in TRAIN_KINDS]
    if line not in network.get("lines", {}):
        return [_find(rules["max_length"], "unknown", f"Line {line!r} is not in network.json; no rule can be "
                      f"checked.", line, date, cells)]
    f = []
    f += rule_approval_country(rules["approval_country"], cells, line, date, network, include_ok)
    f += rule_max_length(rules["max_length"], cells, line, date, network, include_ok)
    f += rule_product_matches_car(rules["product_matches_car"], cells, line, date, include_ok)
    f += rule_product_mix(rules["product_mix"], cells, line, date, include_ok)
    f += rule_second_conductor(rules["second_conductor"], cells, line, date, costs, include_ok)
    f += rule_door_orientation(rules["door_orientation"], cells, line, date, order, include_ok)
    f += rule_high_voltage_rear(rules["high_voltage_rear"], cells, line, date, order, include_ok)
    if include_unknown_static:
        f += rule_workshop_interval(rules["workshop_interval"], line, date)
    return f


def check_day(plan, date: str, *, rules=None, network=None, costs=None, include_ok=False) -> list[Finding]:
    """Every train that night + double_planned + workshop_interval (once) + p_repeat_defect."""
    rules, network, costs = _defaults(rules, network, costs)
    day = [c for c in plan.cells if c.date == date and c.kind in TRAIN_KINDS]
    f = []
    for line in network.get("lines", {}):
        cells = [c for c in day if c.line == line]
        if cells:
            f += check_train(cells, line, date, rules=rules, network=network, costs=costs, include_ok=include_ok)
    f += rule_double_planned(rules["double_planned"], day, date, include_ok)
    f += rule_workshop_interval(rules["workshop_interval"], "", date)
    if "p_repeat_defect" in rules:
        f += rule_p_repeat_defect(rules["p_repeat_defect"], plan.cells, date, {c.car for c in day}, include_ok)
    return f


def check_steps(steps: list[Step], context: dict, *, rules=None, network=None,
                include_ok=False) -> list[Finding]:
    """Judge a proposed option. context = {"cars": {car: {"owner","type", optional "cs": bool, "line"}},
    "trains": {num: {"line"}}, "has_pax": set, optional "date": ISO}.

    weekend_workshop follows §7: collection (abholen) at a workshop is not possible on Saturday or
    Sunday; dropping (abstellen) is possible Sunday to Thursday, so a drop on Friday or Saturday warns."""
    rules, network, _ = _defaults(rules, network, {})
    cars = context.get("cars", {})
    trains = context.get("trains", {})
    has_pax = set(context.get("has_pax") or ())
    date = context.get("date") or ""
    workshops = {w["place"] for w in network.get("workshops", {}).values()}
    ac = rules["approval_country"]
    unknown_owners = {o.lower() for o in ac["params"].get("unknown_owners", [])}
    f: list[Finding] = []
    for s in steps:
        info = cars.get(s.car, {})
        if s.verb == "beistellen" and s.train:
            line = trains.get(s.train, {}).get("line", "")
            if not line:
                f.append(_find(ac, "unknown", f"Car {s.car} onto train {s.train}: the train's line is not known.",
                               cars=[s.car]))
            else:
                f += _approval_one(ac, s.car, info.get("owner", ""), info.get("type", ""), line, date,
                                   _countries(network, line), unknown_owners, include_ok=include_ok,
                                   how=f"would be put onto train {s.train} of")
                ms = rules["mattress_stays"]
                if info.get("cs"):
                    home = info.get("line", "")
                    if not home:
                        f.append(_find(ms, "unknown", f"Comfort Standard car {s.car} onto train {s.train} ({line}): "
                                       f"its current line is not given.", line, date, cars=[s.car]))
                    elif home != line:
                        f.append(_find(ms, _broken(ms), f"Comfort Standard car {s.car} would move from the {home} "
                                       f"line to train {s.train} ({line}); its mattresses stay with it.",
                                       line, date, cars=[s.car]))
        if s.car and s.verb in MOVE_VERBS and s.car in has_pax:
            r = rules["no_shunt_with_pax"]
            f.append(_find(r, _broken(r), f"Car {s.car} has passengers on board and cannot be moved ({s.verb}"
                           f"{' at ' + s.at if s.at else ''}).", date=date, cars=[s.car]))
        if s.verb in ("abholen", "abstellen") and s.at in workshops and date:
            wd = dt.date.fromisoformat(date).weekday()  # Mon=0
            bad = (s.verb == "abholen" and wd in (5, 6)) or (s.verb == "abstellen" and wd in (4, 5))
            if bad:
                r = rules["weekend_workshop"]
                f.append(_find(r, _broken(r), f"Car {s.car}: {s.verb} at workshop {s.at} on "
                               f"{dt.date.fromisoformat(date).strftime('%A')} {date}, when the workshop is closed "
                               f"or the car would stand outside over the weekend.", date=date, cars=[s.car]))
    return f
