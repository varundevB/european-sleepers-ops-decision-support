"""Lane C options: hand-built objects only, no challenge data."""
import json
import sys
from dataclasses import dataclass, field

import pytest

from relay.config import REFERENCE
from relay.model import AXES, Car, Cell, Finding
from relay.options import options_for

FRIDAY = json.loads((REFERENCE / "scenarios" / "friday.json").read_text())


@dataclass
class FakePlan:
    cells: list = field(default_factory=list)
    cars: dict = field(default_factory=dict)


def _cell(line, car, owner, typ, kind, product="", place="", date="2030-01-07"):
    return Cell(line, date, car, owner, typ, "", kind, product, place, "", "")


def fake_plan():
    rows = [("Milan", "T 101", "RDC", "Bvcmz5", "in_train", "2", ""),   # the broken car, sold as seating
            ("Milan", "T 201", "EE", "Bvcmz5", "reserve", "", "DDL"),   # EE: may not run to Italy
            ("Milan", "T 309", "Gff", "Bimz", "parked", "", "DDL"),     # GFF 309: may not run to Italy
            ("Prag", "T 301", "RDC", "Bvcmz5", "parked", "", "ELEN"),   # fine, but needs a transport
            ("Paris", "T 401", "RDC", "Bvcmz5", "in_train", "1", ""),   # busy in another train
            ("Prag", "T 501", "RDC", "WL33", "reserve", "", "DDL")]     # sleeper: cannot carry seating
    p = FakePlan()
    for line, car, owner, typ, kind, prod, place in rows:
        p.cells.append(_cell(line, car, owner, typ, kind, prod, place))
        p.cars[car] = Car(car, owner, typ, [line])
    return p


def fake_check(steps, context):
    """Approval rule only: EE and GFF 309 may not run to Italy (§3)."""
    out = []
    for s in steps:
        info = context["cars"].get(s.car, {})
        line = context["trains"].get(s.train, {}).get("line")
        if s.verb == "beistellen" and line == "Milan" and (info.get("owner") == "EE" or s.car.endswith("309")):
            out.append(Finding("approval_country", "hard", "violation", f"{s.car} may not run to Italy.",
                               "§3: EuroExpress may not run to Italy; GFF car 309 may not run to Italy", cars=[s.car]))
    return out


def no_check(steps, context):
    return []


MILAN = {"kind": "car_broken", "car": "T 101", "line": "Milan", "date": "2030-01-07"}


def test_friday_reproduces_rulebook_ranking_from_computed_numbers():
    opts = {o.id: o for o in options_for(FRIDAY["incident"], FRIDAY, check=no_check)}
    sent, a, b = opts["sent"], opts["rejected_a"], opts["rejected_b"]
    assert sent.rank == 1
    for rej in (a, b):
        assert rej.rank > sent.rank
        # the reason the rulebook gives: more extra transports and more shunting moves
        assert rej.extra_transports > sent.extra_transports
        assert rej.shunting_moves > sent.shunting_moves
    assert opts["keep_first_order"].rank > sent.rank
    assert all(o.origin == "rulebook" for o in opts.values())
    # counts come from the steps
    for o in opts.values():
        assert o.shunting_moves == sum(s.verb in ("abholen", "abstellen", "beistellen", "shunt_out") for s in o.steps)
        assert o.extra_transports == sum(s.verb == "extra_transport" for s in o.steps)
        assert all(s.note for s in o.steps)


def test_friday_euros_come_from_costs_json():
    opts = {o.id: o for o in options_for(FRIDAY["incident"], FRIDAY, check=no_check)}
    assert opts["sent"].eur is None              # only shunting: no price exists
    assert opts["rejected_a"].eur == 5000        # §12 Bentheim–Münster–Lengerich
    assert opts["rejected_b"].eur == 5000 + 5500  # + DDL–SCH, default marked assumption
    assert any("ASSUMPTION" in f for f in opts["rejected_b"].axes["money"].facts)


@pytest.mark.parametrize("case", ["friday", "milan"])
def test_every_option_has_six_axes(case):
    opts = (options_for(FRIDAY["incident"], FRIDAY, check=no_check) if case == "friday"
            else options_for(MILAN, plan=fake_plan(), check=fake_check))
    assert opts
    for o in opts:
        assert tuple(o.axes) == AXES
        for ax in o.axes.values():
            assert 0 <= ax.score <= 100 and ax.headline and ax.facts


def test_ee_and_gff309_into_milan_are_refused_with_citation():
    opts = options_for(MILAN, plan=fake_plan(), check=fake_check)
    by = {o.id: o for o in opts}
    for oid in ("swap_T201", "swap_T309"):
        o = by[oid]
        assert o.refused and o.rank == 0 and "approval_country" in o.refused_by
        assert o.axes["rules"].score == 0
        assert any("§3" in f for f in o.axes["rules"].facts)
    ok = by["swap_T301"]
    assert not ok.refused and ok.rank >= 1 and ok.extra_transports == 1
    assert "swap_T401" not in by and "swap_T501" not in by   # busy elsewhere / wrong product
    refused_pos = [i for i, o in enumerate(opts) if o.refused]
    assert refused_pos == list(range(len(opts) - len(refused_pos), len(opts)))


def test_no_option_mentions_a_car_outside_scenario_or_plan():
    known = {c["car"] for c in FRIDAY["cars"]}
    for o in options_for(FRIDAY["incident"], FRIDAY, check=no_check):
        assert {s.car for s in o.steps if s.car} <= known
    plan = fake_plan()
    for o in options_for(MILAN, plan=plan, check=fake_check):
        assert {s.car for s in o.steps if s.car} <= set(plan.cars)


def test_eur_is_none_when_nothing_can_be_priced():
    by = {o.id: o for o in options_for(MILAN, plan=fake_plan(), check=fake_check)}
    assert by["run_short"].eur is None
    assert by["run_short"].axes["money"].headline == "cannot be priced"


def test_milan_detour_refused_prague_detour_priced():
    by = {o.id: o for o in options_for(MILAN, plan=fake_plan(), check=no_check)}
    assert by["detour"].refused and "detour_line" in by["detour"].refused_by
    prag = dict(MILAN, line="Prag")
    by = {o.id: o for o in options_for(prag, plan=fake_plan(), check=no_check)}
    d = by["detour"]
    assert not d.refused and d.delay_min == 120 and d.eur == 2000
    assert d.pax_affected is None and not any("Refund estimate" in f for f in d.axes["money"].facts)


def test_refund_only_with_known_pax_and_states_assumption():
    prag = dict(MILAN, line="Prag", pax=10)
    d = {o.id: o for o in options_for(prag, plan=fake_plan(), check=no_check)}["detour"]
    assert d.eur == 2000 + 10 * 100 * 25 // 100
    assert any("€100 average-fare ASSUMPTION" in f for f in d.axes["money"].facts)


def test_proposals_are_marked_and_not_ranked():
    opts = options_for(MILAN, plan=fake_plan(), check=no_check)
    props = [o for o in opts if o.proposed]
    assert props and all(o.title.startswith("PROPOSAL:") and o.rank == 0 for o in props)
    assert any("§13" in f for o in props for f in o.axes["products"].facts)


def test_no_hv_option_cites_table_7():
    inc = dict(MILAN, kind="car_no_hv")
    by = {o.id: o for o in options_for(inc, plan=fake_plan(), check=no_check)}
    assert any("table 7" in s.note for s in by["hv_at_back"].steps)


def test_missing_rule_checker_is_never_a_pass(monkeypatch):
    monkeypatch.setitem(sys.modules, "relay.rules", None)   # import now fails
    for o in options_for(FRIDAY["incident"], FRIDAY):
        assert o.findings == []
        assert o.axes["rules"].headline == "rules not checked" and o.axes["rules"].score == 0
