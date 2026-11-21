"""CO2 estimate: synthetic options only, no challenge data, no network.

The expected totals are built from the same factors by hand so the test fails loudly if either the
factors or the arithmetic drift. Tolerances are loose because the distances are stated approximations.
"""
import json

import pytest

from relay.config import REFERENCE
from relay.co2 import co2_of

CO2 = json.loads((REFERENCE / "co2.json").read_text(encoding="utf-8"))


def step(verb, at="", car="", train="", note="", **extra):
    s = {"verb": verb, "car": car, "at": at, "train": train, "note": note}
    s.update(extra)
    return s


def option(*steps, moves=0):
    return {"steps": list(steps), "shunting_moves": moves}


# ------------------------------------------------------------------ shunting only

def test_shunting_moves_only():
    opt = option(step("shunt_out", "DDL"), step("downgrade", "DDL"), moves=1)
    out = co2_of(opt)
    per = CO2["shunting_kg_per_move"]["value"]
    assert out["kg"] == pytest.approx(per, abs=0.01)
    assert len(out["parts"]) == 1
    assert out["parts"][0]["label"].startswith("1 shunting move")
    assert out["parts"][0]["assumption"] is True
    assert "kg" in out["note"]


def test_downgrade_and_abstellen_add_nothing():
    opt = option(step("downgrade", "DDL"), step("abstellen", "CRA"), moves=0)
    out = co2_of(opt)
    assert out["kg"] == 0.0
    assert out["parts"] == []


# ------------------------------------------------------------------ extra transport

def test_extra_transport_goes_to_next_abstellen():
    opt = option(step("shunt_out", "DDL"), step("downgrade", "DDL"),
                 step("extra_transport", "DDL", car="71 120"),
                 step("abstellen", "CRA", car="71 120"), moves=2)
    out = co2_of(opt)
    km = CO2["distances_km"]["DDL-CRA"]["km"]
    per_km = CO2["prices"]["extra_transport_kg_per_train_km"]["value"]
    per_move = CO2["shunting_kg_per_move"]["value"]
    expected = 2 * per_move + km * per_km
    assert out["kg"] == pytest.approx(expected, rel=0.01)
    labels = [p["label"] for p in out["parts"]]
    assert any("DDL–CRA" in lab for lab in labels)


def test_network_distances_take_precedence():
    opt = option(step("extra_transport", "DDL", car="X"), step("abstellen", "CRA", car="X"))
    net = {"places": {}, "links": [], "distances_km": {"DDL-CRA": {"km": 100}}}
    out = co2_of(opt, network=net)
    per_km = CO2["prices"]["extra_transport_kg_per_train_km"]["value"]
    assert out["kg"] == pytest.approx(100 * per_km, rel=0.01)


# ------------------------------------------------------------------ detour

def test_detour_uses_step_km_when_given():
    opt = option(step("detour", "CRA", train="401", km=100), step("shunt_out", "CRA"), moves=1)
    out = co2_of(opt)
    per_km = CO2["prices"]["detour_kg_per_train_km"]["value"]
    per_move = CO2["shunting_kg_per_move"]["value"]
    assert out["kg"] == pytest.approx(100 * per_km + per_move, rel=0.01)


def test_detour_falls_back_to_default_km():
    opt = option(step("detour", "CRA", train="401"))
    out = co2_of(opt)
    default = CO2["detour_default_km"]["value"]
    per_km = CO2["prices"]["detour_kg_per_train_km"]["value"]
    assert out["kg"] == pytest.approx(default * per_km, rel=0.01)
    assert out["parts"][0]["assumption"] is True


# ------------------------------------------------------------------ honesty: unknown is not zero

def test_unknown_distance_returns_none_not_zero():
    # DDL-PRG is not in the distance table and network.json has no km fields.
    opt = option(step("extra_transport", "DDL", car="X"), step("abstellen", "PRG", car="X"))
    out = co2_of(opt)
    assert out["kg"] is None
    assert out["parts"] == []
    assert "distance" in out["note"].lower()


def test_extra_transport_without_abstellen_is_none():
    opt = option(step("extra_transport", "DDL", car="X"))
    out = co2_of(opt)
    assert out["kg"] is None
    assert "end place" in out["note"] or "distance" in out["note"].lower()


def test_missing_shunting_count_is_none_when_moves_needed():
    opt = {"steps": [step("shunt_out", "DDL")]}
    out = co2_of(opt)
    assert out["kg"] is None
    assert "shunting_moves" in out["note"]


def test_partial_unknown_never_reports_a_total():
    # A shunting move can be priced, the transport cannot: the answer is still None, with the
    # priced part kept visible so the caller can see what is missing.
    opt = option(step("shunt_out", "DDL"),
                 step("extra_transport", "DDL", car="X"), step("abstellen", "PRG", car="X"), moves=1)
    out = co2_of(opt)
    assert out["kg"] is None
    assert len(out["parts"]) == 1


# ------------------------------------------------------------------ custom factors, no file reads by the caller

def test_callers_may_pass_their_own_factors():
    doc = json.loads(json.dumps(CO2))
    doc["shunting_kg_per_move"]["value"] = 10.0
    opt = option(step("shunt_out", "DDL"), moves=3)
    out = co2_of(opt, factors=doc)
    assert out["kg"] == pytest.approx(30.0, abs=0.01)
    # the real file on disk is unaffected
    assert co2_of(opt)["kg"] == pytest.approx(3 * CO2["shunting_kg_per_move"]["value"], abs=0.01)


def test_every_part_carries_a_cite():
    opt = option(step("shunt_out", "DDL"), step("detour", "CRA", km=10), moves=1)
    out = co2_of(opt)
    assert out["kg"] is not None
    for p in out["parts"]:
        assert p["cite"], f"part without cite: {p}"
        assert isinstance(p["assumption"], bool)
