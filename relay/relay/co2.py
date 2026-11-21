"""CO2 estimate for one option of a disruption.

One pure function, `co2_of`. It reads only `reference/co2.json` (factors, our-distance table, prices)
and `reference/network.json` (the place list and any distance/link fields it carries). No network, no
LLM, no other files, no side effects.

What counts (the same levers relay.options prices in euros):
  * shunting move        -- `option["shunting_moves"]` x kg per move (a diesel shunting loco working
                            a few minutes). 0 moves = 0 kg.
  * extra_transport      -- one loco-hauled run of empty cars from the step's `at` to the `at` of the
                            next `abstellen` step: km x kg per train-km.
  * detour               -- extra km x kg per train-km. The step's own `km` if it has one, else the
                            stated default detour distance.
  * everything else      -- 0 kg.

Honesty rule: if any distance or factor a step needs is missing, the answer is `kg: None` with the
reason in `note`, never a silent guess and never 0. Distances in co2.json are approximations and are
labelled as such in every `cite`; `network.json` holds no distances today, only approximate city
coordinates, so the lookup falls through to that table.
"""
from __future__ import annotations

import json
from functools import lru_cache

from relay.config import REFERENCE

# The same verbs relay.options counts as a shunting move; used only to notice a missing `shunting_moves`
# count, never to count moves ourselves (the option carries that count).
_SHUNT_VERBS = ("abholen", "abstellen", "beistellen", "shunt_out")


# ------------------------------------------------------------------ loading

@lru_cache(maxsize=1)
def _co2_doc() -> dict:
    return json.loads((REFERENCE / "co2.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _network_doc() -> dict:
    return json.loads((REFERENCE / "network.json").read_text(encoding="utf-8"))


def _doc(factors) -> dict:
    """Return a full co2 document. A caller may pass the whole file, or just its `factors` mapping."""
    if factors is None:
        return _co2_doc()
    if isinstance(factors, dict) and "factors" in factors:
        return factors
    base = dict(_co2_doc())
    base["factors"] = factors
    return base


# ------------------------------------------------------------------ small helpers

def _get(step, name, default=None):
    """Read a step field whether the step is a dict or a dataclass-like object."""
    if isinstance(step, dict):
        return step.get(name, default)
    return getattr(step, name, default)


def _num(x) -> float | None:
    """A finite number as float, else None (bool is not a measurement)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return float(x)


def _factor(doc, key):
    """(value, cite, assumption) for one factor, or None if missing/not a number."""
    f = (doc.get("factors") or {}).get(key)
    if not isinstance(f, dict):
        return None
    v = _num(f.get("value"))
    if v is None:
        return None
    return v, str(f.get("cite", "")), bool(f.get("assumption", False))


def _price(doc, key):
    """Like _factor but for the derived per-km / per-move prices."""
    f = (doc.get("prices") or {}).get(key)
    if f is None and key == "shunting_kg_per_move":
        f = doc.get("shunting_kg_per_move")
    if not isinstance(f, dict):
        return None
    v = _num(f.get("value"))
    if v is None:
        return None
    return v, str(f.get("cite", "")), bool(f.get("assumption", False))


# ------------------------------------------------------------------ distances

def _pair_key(a: str, b: str) -> list[str]:
    return [f"{a}-{b}", f"{b}-{a}"]


def _read_mapping(mapping, a, b):
    """Pull a km figure out of a mapping keyed "A-B" / "B-A"."""
    if not isinstance(mapping, dict):
        return None
    for k in _pair_key(a, b):
        v = mapping.get(k)
        if isinstance(v, dict):
            km = _num(v.get("km", v.get("distance_km")))
            if km is not None:
                return km, str(v.get("cite", "")), bool(v.get("assumption", False))
        km = _num(v)
        if km is not None:
            return km, "", True
    return None


def _distance(a: str, b: str, doc: dict, network: dict):
    """(km, cite, assumption) between two place codes, or None when no distance is known.

    Order: any distance field in network.json first (mapping, or a `links` entry carrying `km`),
    then the co2.json distances table. Nothing is derived at call time.
    """
    for key in ("distances_km", "distance_km", "distances"):
        hit = _read_mapping((network or {}).get(key), a, b)
        if hit:
            return hit
    for lk in (network or {}).get("links") or []:
        if not isinstance(lk, dict):
            continue
        ends = {lk.get("from"), lk.get("to")}
        if ends == {a, b}:
            km = _num(lk.get("km", lk.get("distance_km")))
            if km is not None:
                return km, str(lk.get("cite", "")), bool(lk.get("assumption", True))
    return _read_mapping(doc.get("distances_km"), a, b)


# ------------------------------------------------------------------ the estimate

def co2_of(option: dict, factors: dict | None = None, network: dict | None = None) -> dict:
    """Estimate the CO2 of one option.

    Returns {"kg": float | None, "parts": [{"label", "kg", "cite", "assumption"}], "note": str}.
    `kg` is None when any needed distance or factor is missing; `note` then says which and why.
    `parts` lists only the components that could be priced, each with its kg and source.
    """
    doc = _doc(factors)
    net = _network_doc() if network is None else network
    steps = list(option.get("steps") or []) if isinstance(option, dict) else list(option.steps)
    parts: list[dict] = []
    problems: list[str] = []

    def add(label, kg, cite, assumption):
        parts.append({"label": label, "kg": round(kg, 2), "cite": cite, "assumption": assumption})

    # -- shunting moves
    moves_raw = option.get("shunting_moves") if isinstance(option, dict) else getattr(option, "shunting_moves", None)
    if moves_raw is None:
        if any(_get(s, "verb") in _SHUNT_VERBS for s in steps):
            problems.append("the option has shunting-type steps but no `shunting_moves` count, so the "
                            "shunting part cannot be priced")
    else:
        moves = _num(moves_raw)
        if moves is None or moves < 0:
            problems.append(f"`shunting_moves` is not a usable count ({moves_raw!r})")
        elif moves > 0:
            p = _price(doc, "shunting_kg_per_move")
            if p is None:
                problems.append("the shunting kg-per-move factor is missing from co2.json")
            else:
                per, cite, assumed = p
                add(f"{int(moves)} shunting move(s) x {per:g} kg/move", moves * per, cite, assumed)

    # -- extra transport and detour
    for i, s in enumerate(steps):
        verb = _get(s, "verb")
        if verb == "extra_transport":
            origin = _get(s, "at") or ""
            car = _get(s, "car") or ""
            dest_step = next((t for t in steps[i + 1:]
                              if _get(t, "verb") == "abstellen" and (not car or not _get(t, "car")
                                                                     or _get(t, "car") == car)), None)
            if dest_step is None:
                dest_step = next((t for t in steps[i + 1:] if _get(t, "verb") == "abstellen"), None)
            dest = (_get(dest_step, "at") or "") if dest_step is not None else ""
            label = f"extra transport {origin or '?'}–{dest or '?'}"
            if not origin or not dest:
                problems.append(f"{label}: the run's start or end place is missing, so no distance can "
                                f"be looked up")
                continue
            dist = _distance(origin, dest, doc, net)
            p = _price(doc, "extra_transport_kg_per_train_km")
            if dist is None:
                problems.append(f"{label}: no distance for {origin}–{dest} in reference/network.json or "
                                f"in co2.json distances_km")
                continue
            if p is None:
                problems.append(f"{label}: the kg-per-train-km factor for extra transport is missing")
                continue
            km, dcite, dassumed = dist
            per, pc, passumed = p
            add(f"{label}: {km:g} km x {per:g} kg/km", km * per,
                f"{dcite} {pc}".strip(), dassumed or passumed)

        elif verb == "detour":
            extra = _num(_get(s, "km"))
            if extra is None:
                d = doc.get("detour_default_km")
                if not isinstance(d, dict) or _num(d.get("value")) is None:
                    problems.append("a detour step gives no km and no default detour distance is set in "
                                    "co2.json, so the detour cannot be priced")
                    continue
                extra = _num(d.get("value"))
                dcite, dassumed = str(d.get("cite", "")), bool(d.get("assumption", True))
            else:
                dcite, dassumed = "distance given on the step itself", False
            p = _price(doc, "detour_kg_per_train_km")
            if p is None:
                problems.append("the kg-per-train-km factor for detours is missing from co2.json")
                continue
            per, pc, passumed = p
            train = _get(s, "train") or ""
            name = f"detour of train {train}" if train else "detour"
            add(f"{name}: {extra:g} km x {per:g} kg/km", extra * per,
                f"{dcite} {pc}".strip(), dassumed or passumed)

    zero_verbs = [v for v in ("downgrade", "abstellen", "swap") if any(_get(s, "verb") == v for s in steps)]
    if zero_verbs:
        parts_note = f"{', '.join(sorted(set(zero_verbs)))} steps add 0 kg"
    else:
        parts_note = ""

    if problems:
        note = "CO2 not estimated: " + "; ".join(problems) + (
            f". Priced so far: {sum(p['kg'] for p in parts):.1f} kg, but this is incomplete." if parts else
            ". Nothing could be priced.")
        return {"kg": None, "parts": parts, "note": note}

    kg = round(sum(p["kg"] for p in parts), 2)
    assumed = [p for p in parts if p["assumption"]]
    note_bits = []
    if parts:
        note_bits.append("rough estimate from " + "; ".join(p["label"] for p in parts))
    else:
        note_bits.append("no step in this option produces a CO2 cost")
    if parts_note:
        note_bits.append(parts_note)
    if assumed:
        note_bits.append(f"{len(assumed)} of {len(parts)} component(s) rest on our own assumptions "
                         f"(see each cite); treat the total as an order of magnitude, not an account")
    if any(p["cite"].endswith("APPROXIMATION.") or "APPROXIMATION" in p["cite"] for p in parts):
        note_bits.append("distances are straight-line x 1.30, not surveyed track distances")
    return {"kg": kg, "parts": parts, "note": ". ".join(note_bits) + "."}
