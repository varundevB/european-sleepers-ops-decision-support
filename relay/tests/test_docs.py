import json
import re

from relay.config import REFERENCE
from relay.docs import (decision_record, friday_sent_option, partner_messages, plan_diff,
                        task_order_de)
from relay.model import AxisScore, Cell, Finding, Option, Src, Step

SCENARIO = json.loads((REFERENCE / "scenarios" / "friday.json").read_text(encoding="utf-8"))
CAR_RE = re.compile(r"\b\d{2} \d{3}\b")


def _order():
    return task_order_de(friday_sent_option(SCENARIO), SCENARIO)


def _section(text, head):
    block = text.split(f"\n{head}", 1)[1]
    return block.split("\n\n", 1)[0]


def test_order_places_each_moved_car_where_the_scenario_says():
    text = _order()
    z474, z453, loose = _section(text, "Zug 474"), _section(text, "Zug 453"), _section(text, "Ohne Zugbindung")
    for car in ("71 120", "71 002"):
        assert f"Wagen {car} (RDC, Bvcmz5) in Lengerich (ELEN) dem Zug 474 beistellen." in z474
        assert f"Wagen {car} (RDC, Bvcmz5) in Lengerich (ELEN) abholen." in z474
    for car, desc in (("71 455", "RDC, WL33"), ("71 118", "EE, Bvcmz5"), ("70 057", "EE, Bocmh6")):
        assert f"Wagen {car} ({desc}) in Bad Bentheim dem Zug 453 beistellen." in z453
        assert car not in z474
    assert "Wagen 91 044 (RDC, Bvcmz6) in Lengerich (ELEN) abstellen." in loose


def test_order_has_only_step_cars_and_no_invented_date():
    text = _order()
    steps = {s["car"] for s in next(o for o in SCENARIO["options_in_rulebook"] if o["id"] == "sent")["steps"]}
    assert set(CAR_RE.findall(text)) == steps
    assert "91 309" not in text
    assert "Datum: Freitag (Datum im Regelwerk nicht angegeben)" in text
    assert not re.search(r"\b(19|20)\d{2}\b|\d{1,2}\.\d{1,2}\.", text)


def test_order_header_version_and_change_section():
    text = _order()
    assert text.startswith("Auftrag an TCS\n")
    assert "Version 2 — ersetzt Version 1" in text
    change = _section(text, "Änderung gegenüber Version 1")
    assert "71 120: bisher dem Zug 453 in Bad Bentheim beistellen, jetzt dem Zug 474" in change
    assert "71 002: neu" in change
    assert "71 455" not in change
    assert "Audit-Prüfsumme: abc123" in task_order_de(friday_sent_option(SCENARIO), SCENARIO, audit_hash="abc123")
    assert "Version 4 — ersetzt Version 3" in task_order_de(friday_sent_option(SCENARIO), SCENARIO, supersedes=3)


def test_partner_messages_friday():
    msgs = partner_messages(friday_sent_option(SCENARIO), SCENARIO)
    to = [m["to"] for m in msgs]
    assert to[0] == "TCS Disposition"
    assert "Werkstatt Lengerich (RDC)" in to
    assert not any("Arenaways" in t for t in to)
    assert not any("night duty" in t for t in to)
    assert not any("Münster" in t or "Crailsheim" in t for t in to)  # sent option collects only at Lengerich
    lengerich = next(m for m in msgs if m["to"] == "Werkstatt Lengerich (RDC)")
    assert "71 120" in lengerich["body"] and "71 002" in lengerich["body"] and "91 044" in lengerich["body"]


def test_partner_messages_milan_and_passengers():
    opt = Option(id="x", title="swap", pax_affected=12,
                 steps=[Step("abholen", car="71 118", at="EMSTG"), Step("beistellen", car="71 118", train="401", at="KAR")])
    to = [m["to"] for m in partner_messages(opt, SCENARIO)]
    assert "Arenaways operations" in to
    assert "Werkstatt Münster (EuroExpress)" in to
    assert "European Sleeper night duty service" in to
    assert "Werkstatt Lengerich (RDC)" not in to


def _cell(car, raw, cell):
    return Cell(line="Paris", date="2026-10-02", car=car, owner="RDC", type="Bvcmz5", raw=raw, kind="",
                product="", place="", colour="", colour_meaning="", src=Src("f.xlsx", "Paris", cell))


def test_plan_diff():
    before = [_cell("71 120", "X", "G4"), _cell("71 002", "Rev", "H4"), _cell("71 455", "1", "I4")]
    after = [_cell("71 120", "3", "G4"), _cell("71 002", "3", "H4"), _cell("71 455", "1", "I4")]
    diff = plan_diff(before, after)
    assert diff == [
        {"line": "Paris", "date": "2026-10-02", "car": "71 120", "before": "X", "after": "3", "src": "Paris!G4"},
        {"line": "Paris", "date": "2026-10-02", "car": "71 002", "before": "Rev", "after": "3", "src": "Paris!H4"},
    ]
    assert plan_diff(before, before) == []
    removed = plan_diff(before, after[:2])
    assert removed[-1]["car"] == "71 455" and removed[-1]["after"] == ""


def test_decision_record():
    chosen = Option(id="sent", title="t", steps=[Step("abholen", car="71 120", at="ELEN")], rank=1,
                    axes={"money": AxisScore(80, "€0")}, eur=0, shunting_moves=0)
    bad = Option(id="b", title="EE to Milan", refused=True, refused_by=["approval_country"],
                 findings=[Finding("approval_country", "hard", "violation", "EE not approved in IT", "§3")])
    other = Option(id="a", title="a", rank=2)
    rec = decision_record(SCENARIO["incident"], chosen, [chosen, other, bad], "planner", "2026-11-21T10:00")
    assert rec["chosen"]["id"] == "sent" and rec["chosen"]["axes"]["money"] == "€0"
    assert rec["chosen"]["axes"]["rules"] is None
    assert rec["chosen"]["steps"][0]["car"] == "71 120"
    assert {o["id"] for o in rec["others"]} == {"a", "b"}
    b = next(o for o in rec["others"] if o["id"] == "b")
    assert b["refused_by"] == ["approval_country"] and b["refusal"] == ["EE not approved in IT"]
    assert rec["decided_by"] == "planner" and rec["incident"]["id"] == "inc-friday-1"
    json.dumps(rec)
