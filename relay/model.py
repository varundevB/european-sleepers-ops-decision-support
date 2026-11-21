"""Shared data shapes. Every lane imports from here; nobody redefines them.

Plain dataclasses, JSON-serialisable through `to_dict`. Nothing here reads a file.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# What a grid cell means, after the code is read.
KINDS = (
    "in_train",      # codes 1,2,3,4,9
    "shunt_in",      # 0  = shunted in during ride
    "shunt_out",     # -1 = shunted out during ride
    "transfer",      # -2 = separate transfer
    "reserve",       # X  = parked / reserve
    "revision",      # Rev = out of service, no rent
    "parked",        # a place name: Ddl, Zot, milaan, Wusti...
    "note",          # free text that is not a code (bedbug, wheels...)
    "colour_only",   # no value, only a fill
    "unreadable",    # could not be typed; kept, never guessed
)
PRODUCTS = {"1": "Classic couchette", "2": "Seating", "3": "Comfort Standard",
            "4": "Comfort Plus", "9": "In train, not commercial"}
STATUSES = ("violation", "warning", "ok", "unknown")
TIERS = ("hard", "soft", "proposed")
AXES = ("rules", "products", "partners", "infrastructure", "time", "money")


@dataclass(frozen=True)
class Src:
    """Where a fact came from. Nothing is shown without one."""
    file: str
    sheet: str
    cell: str          # e.g. "AB417"; "" when the fact is a whole table
    as_of: str = ""    # ISO date of the row, when there is one

    def label(self) -> str:
        return f"{self.sheet}!{self.cell}" if self.cell else self.sheet


@dataclass
class Car:
    car: str           # planning id exactly as in row 3, e.g. "71 455"
    owner: str         # Gff | RDC | EE | SVG | Tri | CD, as written in row 1
    type: str          # Bvcmz5, WL33, ...
    lines: list[str] = field(default_factory=list)   # sheets this car has a column in
    src: Src | None = None


@dataclass
class Cell:
    line: str          # Prag | Paris | Milan (sheet name)
    date: str          # ISO
    car: str
    owner: str
    type: str
    raw: str           # the cell value as text, floats normalised (1.0 -> "1"), "" if empty
    kind: str          # one of KINDS
    product: str       # "1".."4","9" when kind is in_train, else ""
    place: str         # canonical place key from network.json for kind == parked, else ""
    colour: str        # "rgb/FFFF0000" or "theme/7/0.0" or ""
    colour_meaning: str  # Legend text, "" or "UNKNOWN"
    src: Src | None = None


@dataclass
class Finding:
    rule_id: str
    tier: str          # hard | soft | proposed
    status: str        # violation | warning | ok | unknown
    message: str       # one plain sentence
    cite: str          # the rulebook sentence, from rules.json
    line: str = ""
    date: str = ""
    cars: list[str] = field(default_factory=list)
    evidence: list[Src] = field(default_factory=list)

    @property
    def blocks(self) -> bool:
        return self.tier == "hard" and self.status == "violation"


@dataclass
class OpsNight:
    line: str
    date: str
    dep_delay: int | None      # minutes; None when the cell is empty or text
    arr_delay: int | None
    pax: int | None
    pax_over_1h: int | None    # column "%>1 hour" is a passenger COUNT, not a percentage
    downgrades: int | None
    cancellations: int | None
    note: str                  # "extra info", verbatim
    dep_raw: str = ""          # text kept when the delay cell holds words ("canceled", "?")
    arr_raw: str = ""
    src: Src | None = None


@dataclass
class Step:
    verb: str          # abholen | abstellen | beistellen | shunt_out | detour | extra_transport | downgrade | cancel_car | swap
    car: str = ""
    at: str = ""       # place key
    train: str = ""    # train number
    note: str = ""


@dataclass
class AxisScore:
    score: int         # 0..100, higher is better
    headline: str      # what the bar says, e.g. "€5,000" or "0 rules broken"
    facts: list[str] = field(default_factory=list)   # each ends with its citation or source label


@dataclass
class Option:
    id: str
    title: str
    steps: list[Step] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    axes: dict[str, AxisScore] = field(default_factory=dict)   # keys = AXES
    eur: int | None = None          # total euros; None when it cannot be priced
    shunting_moves: int = 0
    extra_transports: int = 0
    delay_min: int = 0
    pax_affected: int | None = None
    refused: bool = False           # True when any finding blocks
    refused_by: list[str] = field(default_factory=list)   # rule ids
    proposed: bool = False          # True when it uses a lever of ours, not ES's
    rank: int = 0                   # 1 = best among non-refused
    origin: str = "generated"       # generated | rulebook


@dataclass
class ImpactNode:
    id: str            # "car:71 455", "train:453", "place:ELEN", "partner:TCS", "product:Paris:4", "line:Milan"
    kind: str          # car | train | place | partner | product | line | passengers
    label: str
    detail: str = ""
    place: str = ""    # place key, when it sits somewhere on the map


@dataclass
class ImpactEdge:
    src: str           # node id
    dst: str
    why: str           # one short sentence
    cite: str = ""
    hop: int = 1       # 1 = touched directly, 2 = touched because of a hop-1 node...


@dataclass
class Impact:
    nodes: list[ImpactNode] = field(default_factory=list)
    edges: list[ImpactEdge] = field(default_factory=list)


def to_dict(obj: Any) -> Any:
    """Dataclasses, lists and dicts to plain JSON types."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj
