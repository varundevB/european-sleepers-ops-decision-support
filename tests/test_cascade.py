"""Lane C cascade: the Friday graph and a hand-built plan."""
import json
from dataclasses import dataclass, field

from relay.cascade import impact_of
from relay.config import REFERENCE
from relay.model import Car, Cell

FRIDAY = json.loads((REFERENCE / "scenarios" / "friday.json").read_text())


def test_friday_contains_required_nodes():
    im = impact_of(FRIDAY["incident"], FRIDAY)
    ids = {n.id for n in im.nodes}
    need = {"car:71 002", "car:71 120", "train:474", "train:453", "place:ELEN", "place:HBTH", "place:DDL",
            "line:Paris", "line:Prag", "line:Milan", "partner:TCS"}
    assert need <= ids
    assert "car:71 455" not in ids          # its train did not change
    assert all(n.place for n in im.nodes if n.kind == "place")
    assert not any(n.kind == "passengers" for n in im.nodes)   # no count in the file


def test_friday_edges_have_why_cite_and_hop():
    im = impact_of(FRIDAY["incident"], FRIDAY)
    ids = {n.id for n in im.nodes}
    assert im.edges
    for e in im.edges:
        assert e.why.strip() and e.cite.strip() and 1 <= e.hop <= 3
        assert e.src in ids and e.dst in ids
    assert any(e.src == "car:71 120" and e.dst == "line:Milan" for e in im.edges)


@dataclass
class FakePlan:
    cells: list = field(default_factory=list)
    cars: dict = field(default_factory=dict)


def test_generic_car_broken_on_milan():
    plan = FakePlan([Cell("Milan", "2030-01-07", "T 101", "RDC", "Bvcmz5", "2", "in_train", "2", "", "", "")],
                    {"T 101": Car("T 101", "RDC", "Bvcmz5", ["Milan"])})
    im = impact_of({"kind": "car_broken", "car": "T 101", "line": "Milan", "date": "2030-01-07"}, plan=plan)
    ids = {n.id for n in im.nodes}
    assert {"car:T 101", "place:ELEN", "line:Milan", "product:Milan:2", "partner:TCS", "partner:Arenaways",
            "place:DDL", "line:Prag"} <= ids
    assert all(e.why and e.cite for e in im.edges)
