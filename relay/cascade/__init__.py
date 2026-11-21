"""What one incident touches, as a small graph.

Edges are added only where the rulebook or a reference file proves the link; each carries a one-sentence
`why` and a `cite`. `hop` is the breadth-first distance from the incident car (clamped to 3) so a view can
animate the spread. No passenger count is invented: a passengers node appears only when the incident gives one.
"""
from __future__ import annotations

import json
from collections import deque

from relay.config import REFERENCE
from relay.model import PRODUCTS, Impact, ImpactEdge, ImpactNode

MOVE_VERBS = ("abholen", "abstellen", "beistellen", "shunt_out")


def _load(name):
    return json.loads((REFERENCE / name).read_text())


def _train_lines(network, scenario=None) -> dict[str, str]:
    """Train number -> line key, from network.json (table 0) and the scenario's own train list."""
    out = {}
    for key, ln in network["lines"].items():
        out[ln["outbound"]] = key
        out[ln["return"]] = key
    for num, t in ((scenario or {}).get("trains") or {}).items():
        out[num] = t["line"]
    return out


class _Graph:
    def __init__(self, network):
        self.network = network
        self.nodes: dict[str, ImpactNode] = {}
        self.edges: list[ImpactEdge] = []

    def node(self, nid, kind, label, detail="", place=""):
        if nid not in self.nodes:
            self.nodes[nid] = ImpactNode(nid, kind, label, detail, place)
        elif place and not self.nodes[nid].place:
            self.nodes[nid].place = place
        return nid

    def place(self, key):
        p = self.network["places"].get(key, {})
        return self.node(f"place:{key}", "place", p.get("name", key), p.get("role", ""), key)

    def line(self, key):
        ln = self.network["lines"].get(key, {})
        return self.node(f"line:{key}", "line", ln.get("name", key))

    def train(self, num, place=""):
        return self.node(f"train:{num}", "train", f"Train {num}", place=place)

    def partner(self, name, detail):
        return self.node(f"partner:{name}", "partner", name, detail)

    def edge(self, src, dst, why, cite):
        if any(e.src == src and e.dst == dst for e in self.edges):
            return
        self.edges.append(ImpactEdge(src, dst, why, cite))

    def finish(self, root) -> Impact:
        dist = {root: 0}
        q = deque([root])
        out: dict[str, list[str]] = {}
        for e in self.edges:
            out.setdefault(e.src, []).append(e.dst)
        while q:
            n = q.popleft()
            for d in out.get(n, []):
                if d not in dist:
                    dist[d] = dist[n] + 1
                    q.append(d)
        for e in self.edges:
            e.hop = max(1, min(3, dist.get(e.src, 2) + 1))
        return Impact(list(self.nodes.values()), self.edges)


def _partners(g: _Graph, trains: dict[str, str], lines: set[str]):
    tcs = g.partner("TCS", "Train Charter Services")
    for num in trains:
        g.edge(f"train:{num}", tcs, f"TCS does the car exchanges and shunting for train {num} and gets the "
               "re-sent task order.", "§6: TCS ... does the car exchanges and the shunting; §9: the order to TCS is sent again")
    for key in lines:
        if "IT" in g.network["lines"].get(key, {}).get("countries", []):
            g.edge(f"line:{key}", g.partner("Arenaways", "runs the train in Italy"),
                   "The Milan line runs into Italy, where Arenaways runs the train.", "§6: Arena Ways runs the train in Italy")


def _shared_lines(g: _Graph, line: str):
    """Other lines that share a stabling yard with `line`, or a network.json link."""
    lines = g.network["lines"]
    for other, ln in lines.items():
        if other == line:
            continue
        for yard in set(lines[line].get("stabling", [])) & set(ln.get("stabling", [])):
            g.edge(f"line:{line}", g.place(yard), f"The {line} train is stabled and shunted at {yard}.",
                   "table 3; network.json lines.stabling")
            g.edge(g.place(yard), g.line(other), f"The {other} train uses the same yard, so a change there "
                   "competes for the same tracks and shunters.", "table 3; network.json lines.stabling")
    for lk in g.network["links"]:
        if lk["from"] == line and lk["to"] in lines:
            g.edge(f"line:{line}", g.line(lk["to"]), f"Cars move from the {line} line to the {lk['to']} line.", lk["cite"])


def _friday(incident, scenario, network) -> Impact:
    """Scenario mode: the cars whose steps differ between the first order and the sent order are touched."""
    g = _Graph(network)
    tl = _train_lines(network, scenario)
    cars = {c["car"]: c for c in scenario.get("cars", [])}
    root_car = incident["car"]
    root = g.node(f"car:{root_car}", "car", root_car, cars.get(root_car, {}).get("note", ""),
                  incident.get("place", ""))
    owner = cars.get(root_car, {}).get("owner", "")
    ws = network["workshops"].get(owner)
    if ws:
        g.edge(root, g.place(ws["place"]), f"{root_car} is an {owner} car and became available at its "
               f"workshop {ws['place']}.", f"{incident.get('cite', '§11')}; {ws['cite']}")

    first = scenario.get("first_order", [])
    sent = next((o for o in scenario.get("options_in_rulebook", []) if o.get("status") == "sent"), None)
    new = sent["steps"] if sent else []
    # A car is touched when the train it is added to differs between the two orders (the sent list in the
    # JSON leaves out collection steps, so only beistellen is compared).
    key = lambda s: (s["verb"], s["car"], s.get("train", ""), s.get("at", ""))
    old_keys = {key(s) for s in first if s["verb"] == "beistellen"}
    new_keys = {key(s) for s in new if s["verb"] == "beistellen"}
    touched = [c for c in dict.fromkeys([s["car"] for s in first + new])
               if {k for k in old_keys if k[1] == c} != {k for k in new_keys if k[1] == c}]
    if root_car not in touched:
        touched.insert(0, root_car)

    used_trains, used_lines = set(), set()
    for car in touched:
        cn = g.node(f"car:{car}", "car", car, cars.get(car, {}).get("note", ""), cars.get(car, {}).get("start", ""))
        for s in new:
            if s["car"] == car and s["verb"] == "beistellen":
                t = g.train(s["train"], s.get("at", ""))
                src = root if car == root_car else f"train:{s['train']}"
                dst = t if car == root_car else cn
                why = (f"The changed order adds {car} to train {s['train']} at {s.get('at', '')}." if car == root_car
                       else f"Because of the call, {car} goes onto train {s['train']} at {s.get('at', '')} as well.")
                g.edge(src, dst, why, "§11: 71 120 goes back to the Paris train together with 71 002; table 8")
                used_trains.add(s["train"])
                if s.get("at") == "ELEN":
                    g.edge(g.place("ELEN"), t, f"Train {s['train']} exchanges cars at Lengerich, which has one "
                           "access track, southbound.", "§7 table 6: Lengerich for RDC cars; one access track, southbound")
        for s in first:
            if s["car"] == car and s["verb"] == "beistellen" and key(s) not in new_keys:
                t = g.train(s["train"], s.get("at", ""))
                g.edge(cn, t, f"The first order added {car} to train {s['train']} at {s.get('at', '')}; the changed "
                       f"order takes it off, so fewer cars are added there.",
                       "§11: only three cars are added at Bad Bentheim")
                if s.get("at"):
                    g.edge(t, g.place(s["at"]), f"Train {s['train']} receives the collected cars at {s['at']}.",
                           "§11; table 8")
                used_trains.add(s["train"])
            if s["car"] == car and s.get("aim") and "Milan" in s["aim"]:
                g.edge(cn, g.line("Milan"), f"{car} was meant to pass on to the Milan train for extra capacity; "
                       "the Milan train loses that capacity.", "§11: with the aim of passing it on to the Milan train")
                used_lines.add("Milan")
                via = tl.get(s.get("train", ""), "")
                if via:
                    g.edge(g.line(via), g.place("DDL"), f"RDC cars for Milan ride the {via} line to Denderleeuw.",
                           "§9: exchanged onto the Prague line, brought to Denderleeuw")
                    g.edge(g.place("DDL"), g.line("Milan"), "At Denderleeuw they are shunted into the Milan train "
                           "when it is in.", "§9; network.json links Paris->Milan")
    for num in used_trains:
        if num in tl:
            g.edge(f"train:{num}", g.line(tl[num]), f"Train {num} runs on the {tl[num]} line.", "table 0")
            used_lines.add(tl[num])
    _partners(g, used_trains, used_lines)
    _pax(g, incident, root)
    return g.finish(root)


def _pax(g, incident, root):
    if incident.get("pax") is not None:
        g.edge(root, g.node("passengers", "passengers", f"{incident['pax']} passengers"),
               "Passengers on board are affected.", incident.get("pax_src", "incident"))


def _generic(incident, plan, network) -> Impact:
    g = _Graph(network)
    car, line, date = incident.get("car", ""), incident.get("line", ""), incident.get("date", "")
    info = plan.cars.get(car) if plan is not None else None
    cells = [c for c in (plan.cells if plan is not None else [])
             if c.car == car and c.date == date and (not line or c.line == line)]
    root = g.node(f"car:{car}", "car", car, incident.get("kind", ""), incident.get("place", ""))
    owner = info.owner if info else (cells[0].owner if cells else "")
    ws = network["workshops"].get(owner)
    if ws:
        wp = g.place(ws["place"])
        g.edge(root, wp, f"{car} is an {owner} car; repairs go to {ws['place']}.", ws["cite"])
        for lk in network["links"]:
            if ws["place"] in (lk["from"], lk["to"]) and lk.get("cost_key"):
                other = lk["to"] if lk["from"] == ws["place"] else lk["from"]
                g.edge(wp, g.place(other), f"The workshop is linked to {other} by extra transport.",
                       f"{lk['cite']}; network.json links")
    lines = set()
    for c in cells:
        label = c.src.label() if c.src else c.line
        if c.kind in ("in_train", "shunt_in", "shunt_out"):
            g.edge(root, g.line(c.line), f"{car} is planned in the {c.line} train on {date}.", label)
            lines.add(c.line)
        if c.product:
            p = g.node(f"product:{c.line}:{c.product}", "product", PRODUCTS.get(c.product, c.product))
            g.edge(root, p, f"{car} carries the sold product {PRODUCTS.get(c.product, c.product)}.", label)
    if line and line in network["lines"]:
        lines.add(line)
        g.line(line)
        if not any(e.dst == f"line:{line}" for e in g.edges):
            g.edge(root, f"line:{line}", f"The incident names the {line} line.", "incident")
    trains = {}
    if incident.get("train"):
        trains[incident["train"]] = line
        g.edge(f"line:{line}", g.train(incident["train"]), f"Train {incident['train']} runs on the {line} line.",
               "table 0")
    tcs = g.partner("TCS", "Train Charter Services")
    for ln in lines:
        g.edge(f"line:{ln}", tcs, "TCS does every exchange, shunt and workshop move on this line.",
               "§6: TCS ... does the car exchanges and the shunting, takes cars to the workshop")
        _shared_lines(g, ln)
    _partners(g, {}, lines | {e.dst[5:] for e in g.edges if e.dst.startswith("line:")})
    _pax(g, incident, root)
    return g.finish(root)


def impact_of(incident: dict, scenario: dict | None = None, *, plan=None, network=None) -> Impact:
    network = network or _load("network.json")
    if scenario and scenario.get("first_order") is not None:
        return _friday(incident, scenario, network)
    return _generic(incident, plan, network)
