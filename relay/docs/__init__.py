"""Documents for one decision: the German task order to TCS, partner messages, plan diff, decision record.

Templates only. Every car number written comes from the option's steps; places come from network.json.
"""
from __future__ import annotations

import json
from functools import lru_cache

from relay.config import REFERENCE
from relay.model import AXES, Cell, Impact, Option, Step, to_dict

WORKSHOPS = {  # place key -> recipient role and German place name (network.json workshops, table 6)
    "ELEN": "Werkstatt Lengerich (RDC)",
    "EMSTG": "Werkstatt Münster (EuroExpress)",
    "CRA": "Werkstatt Crailsheim (GFF)",
}
TCS = "TCS Disposition"
ARENAWAYS = "Arenaways operations"
NIGHT_DUTY = "European Sleeper night duty service"
SENDER = "European Sleeper, Flottenplanung"


@lru_cache(maxsize=1)
def _default_network() -> dict:
    return json.loads((REFERENCE / "network.json").read_text(encoding="utf-8"))


def _place(network: dict, key: str) -> str:
    """Place name from network.json; an unknown key is printed as the key itself, marked."""
    p = network.get("places", {}).get(key)
    return p["name"] if p else f"{key} (Ort nicht in network.json)"


def _car(scenario: dict, car: str) -> str:
    for c in scenario.get("cars", []):
        if c.get("car") == car:
            return f"{car} ({c.get('owner', '?')}, {c.get('type', '?')})"
    return f"{car} (Eigentümer/Typ unbekannt)"


def _train_line(network: dict, scenario: dict, train: str) -> str:
    """Line key for a train number: scenario first, then network.json outbound/return numbers."""
    line = scenario.get("trains", {}).get(train, {}).get("line", "")
    if line:
        return line
    for key, ln in network.get("lines", {}).items():
        if train in (ln.get("outbound"), ln.get("return")):
            return key
    return ""


def _steps(raw: list[dict]) -> list[Step]:
    fields = Step.__dataclass_fields__
    return [Step(**{k: v for k, v in s.items() if k in fields}) for s in raw]


def _version(scenario: dict, supersedes: int | None) -> tuple[int, int | None]:
    if supersedes is not None:
        return supersedes + 1, supersedes
    if scenario.get("first_order"):
        return 2, 1
    return 1, None


def _line_de(step: Step, scenario: dict, network: dict) -> str:
    car, at = _car(scenario, step.car), _place(network, step.at) if step.at else ""
    if step.verb == "abholen":
        return f"Wagen {car} in {at} abholen."
    if step.verb == "beistellen":
        return f"Wagen {car} in {at} dem Zug {step.train} beistellen."
    if step.verb == "abstellen":
        return f"Wagen {car} in {at} abstellen."
    if step.verb == "shunt_out":
        return f"Wagen {car} aus Zug {step.train} aussetzen" + (f" in {at}." if at else ".")
    if step.verb == "extra_transport":
        return f"Zusatztransport für Wagen {car}" + (f" nach {at}" if at else "") + " (mit Rangierbegleiter)."
    if step.verb == "detour":
        return f"Zug {step.train} über {at} umleiten."
    if step.verb == "swap":
        return f"Wagen {car} tauschen" + (f" in {at}." if at else ".")
    return ""  # downgrade / cancel_car are passenger matters, not TCS work


def _destination(steps: list[Step]) -> dict[str, tuple[str, str, str]]:
    """car -> (verb, train, place) of its last beistellen/abstellen."""
    out = {}
    for s in steps:
        if s.verb in ("beistellen", "abstellen") and s.car:
            out[s.car] = (s.verb, s.train, s.at)
    return out


def _dest_de(d: tuple[str, str, str], network: dict) -> str:
    verb, train, at = d
    return f"dem Zug {train} in {_place(network, at)} beistellen" if verb == "beistellen" else f"in {_place(network, at)} abstellen"


def task_order_de(option: Option, scenario: dict, *, network=None, supersedes: int | None = None,
                  audit_hash: str | None = None) -> str:
    """Auftrag an TCS in German, grouped per train number, then moves not tied to a train (rulebook §9)."""
    network = network or _default_network()
    steps = option.steps
    # a car collected and then added to a train belongs under that train
    car_train = {s.car: s.train for s in steps if s.verb == "beistellen" and s.train}
    groups: dict[str, list[str]] = {}
    loose: list[str] = []
    for s in steps:
        text = _line_de(s, scenario, network)
        if not text:
            continue
        train = s.train or car_train.get(s.car, "")
        (groups.setdefault(train, []) if train else loose).append(f"- {text}")

    version, old = _version(scenario, supersedes)
    date = scenario.get("date")
    out = ["Auftrag an TCS", f"An: {TCS}", f"Von: {SENDER}",
           f"Datum: {date}" if date else "Datum: Freitag (Datum im Regelwerk nicht angegeben)",
           f"Version {version} — ersetzt Version {old}" if old else f"Version {version}", ""]

    if scenario.get("first_order") and old:
        before, after = _destination(_steps(scenario["first_order"])), _destination(steps)
        changes = []
        for car, d in after.items():
            if car not in before:
                changes.append(f"- {car}: neu — {_dest_de(d, network)}.")
            elif before[car] != d:
                changes.append(f"- {car}: bisher {_dest_de(before[car], network)}, jetzt {_dest_de(d, network)}.")
        dropped = [c for c in before if c not in after]
        if dropped:
            changes.append(f"- {len(dropped)} Wagen aus Version {old} entfallen in diesem Auftrag.")
        out += [f"Änderung gegenüber Version {old}:"] + (changes or ["- keine"]) + [""]

    for train, lines in groups.items():
        line = network.get("lines", {}).get(_train_line(network, scenario, train), {})
        out += [f"Zug {train}" + (f" ({line['name']})" if line else "")] + lines + [""]
    if loose:
        out += ["Ohne Zugbindung"] + loose + [""]
    out.append(f"Audit-Prüfsumme: {audit_hash or '(wird nach Freigabe eingetragen)'}")
    return "\n".join(out) + "\n"


def _milan_trains(network: dict) -> set[str]:
    ln = network.get("lines", {}).get("Milan", {})
    return {ln.get("outbound", ""), ln.get("return", "")} - {""}


def partner_messages(option: Option, scenario: dict, impact: Impact | None = None, *, network=None) -> list[dict]:
    """One short message per party the steps touch. `impact` is accepted for the contract and not used."""
    network = network or _default_network()
    steps = option.steps
    version, old = _version(scenario, None)
    trains = sorted({s.train for s in steps if s.train})
    msgs = [{"to": TCS, "subject": f"Auftrag an TCS, Version {version}",
             "body": (f"Guten Tag, anbei der Auftrag Version {version}"
                      + (f", er ersetzt Version {old}" if old else "")
                      + f". Betroffene Züge: {', '.join(trains) or 'keine'}. Bitte Eingang bestätigen.")}]

    for key, who in WORKSHOPS.items():
        pick = [s.car for s in steps if s.verb == "abholen" and s.at == key and s.car]
        drop = [s.car for s in steps if s.verb == "abstellen" and s.at == key and s.car]
        if not (pick or drop):
            continue
        parts = []
        if pick:
            parts.append(f"TCS holt heute folgende Wagen ab: {', '.join(pick)}.")
        if drop:
            parts.append(f"TCS stellt heute folgende Wagen bei Ihnen ab: {', '.join(drop)}.")
        msgs.append({"to": who, "subject": f"Wagenbewegungen {_place(network, key)}",
                     "body": "Guten Tag, " + " ".join(parts) + " Den Auftrag erhält TCS direkt."})

    milan = [s for s in steps if s.train in _milan_trains(network)]
    if milan:
        cars = sorted({s.car for s in milan if s.car})
        tr = sorted({s.train for s in milan})
        msgs.append({"to": ARENAWAYS, "subject": f"Change to train {', '.join(tr)}",
                     "body": f"Hello, the composition of train {', '.join(tr)} changes"
                             + (f" (cars {', '.join(cars)})" if cars else "") + ". TCS has the task order."})

    pax = option.pax_affected is not None and option.pax_affected > 0
    downgrade = any(s.verb in ("downgrade", "cancel_car") for s in steps)
    if pax or downgrade:
        what = [f"{option.pax_affected} passengers affected"] if pax else []
        if downgrade:
            what.append("the option downgrades or cancels a car")
        msgs.append({"to": NIGHT_DUTY, "subject": f"Passenger impact: {option.title}",
                     "body": f"Hello, {'; '.join(what)}. A decision on passengers is needed (rulebook §6)."})
    return msgs


def plan_diff(before: list[Cell], after: list[Cell]) -> list[dict]:
    """Cells whose raw code changed, keyed by (line, date, car). A missing cell counts as ""."""
    b = {(c.line, c.date, c.car): c for c in before}
    a = {(c.line, c.date, c.car): c for c in after}
    keys = list(a) + [k for k in b if k not in a]
    out = []
    for k in keys:
        cb, ca = b.get(k), a.get(k)
        rb, ra = cb.raw if cb else "", ca.raw if ca else ""
        if rb == ra:
            continue
        src = (ca and ca.src) or (cb and cb.src)
        out.append({"line": k[0], "date": k[1], "car": k[2], "before": rb, "after": ra,
                    "src": src.label() if src else ""})
    return out


def decision_record(incident: dict, option: Option, options: list[Option], user: str, at: str) -> dict:
    """What was chosen, what else was on the table, what refused it, who decided and when."""
    others = []
    for o in options:
        if o.id == option.id:
            continue
        rec = {"id": o.id, "title": o.title, "rank": o.rank, "refused": o.refused}
        if o.refused:
            rec["refused_by"] = list(o.refused_by)
            rec["refusal"] = [f.message for f in o.findings if f.blocks]
        others.append(rec)
    return {
        "incident": to_dict(incident),
        "chosen": {"id": option.id, "title": option.title, "steps": to_dict(option.steps),
                   "axes": {k: (option.axes[k].headline if k in option.axes else None) for k in AXES},
                   "eur": option.eur, "shunting_moves": option.shunting_moves,
                   "extra_transports": option.extra_transports, "delay_min": option.delay_min},
        "others": others,
        "decided_by": user,
        "decided_at": at,
    }


def friday_sent_option(scenario: dict) -> Option:
    """The option ES sent on the Friday, built straight from the scenario JSON (no scoring)."""
    sent = next(o for o in scenario["options_in_rulebook"] if o["id"] == "sent")
    return Option(id=sent["id"], title=sent["title"], steps=_steps(sent["steps"]), origin="rulebook")
