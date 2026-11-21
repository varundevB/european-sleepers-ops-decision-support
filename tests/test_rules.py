"""Lane B tests. Synthetic, hand-built cells only."""
from dataclasses import dataclass, field

import pytest

from relay.model import Cell, Src, Step
from relay.rules import check_day, check_steps, check_train, load_rules

D = "2027-01-01"  # a Friday


def cell(car, line="Milan", kind="in_train", product="1", owner="RDC", ctype="Bvcmz5", date=D,
         colour_meaning="", n=0):
    return Cell(line=line, date=date, car=car, owner=owner, type=ctype, raw=product, kind=kind,
                product=product if kind == "in_train" else "", place="", colour="",
                colour_meaning=colour_meaning, src=Src("synthetic.xlsx", line, f"G{4 + n}", date))


def good_mix(line="Milan", extra=0):
    """2x CP (WL33), 2x CS, 1 seat, 1 couchette + `extra` couchettes; all RDC."""
    cs = [cell("10 001", line, product="4", ctype="WL33"), cell("10 002", line, product="4", ctype="WL33"),
          cell("10 003", line, product="3"), cell("10 004", line, product="3"),
          cell("10 005", line, product="2", ctype="Bimz"), cell("10 006", line, product="1")]
    return cs + [cell(f"20 {i:03d}", line) for i in range(extra)]


def by(findings, rule, status=None):
    return [f for f in findings if f.rule_id == rule and (status is None or f.status == status)]


@dataclass
class FakePlan:
    cells: list = field(default_factory=list)
    cars: dict = field(default_factory=dict)


# ---- loading / overrides ----

def test_load_rules_reads_all_fourteen():
    r = load_rules()
    assert len(r) == 14 and r["approval_country"]["tier"] == "hard"


def test_override_turns_violation_into_warning():
    cs = [cell("30 001", owner="EE")]
    hard = by(check_train(cs, "Milan", D), "approval_country")
    soft = by(check_train(cs, "Milan", D, rules=load_rules(overrides={"approval_country": "soft"})),
              "approval_country")
    assert [f.status for f in hard] == ["violation"] and hard[0].blocks
    assert [f.status for f in soft] == ["warning"] and soft[0].tier == "soft" and not soft[0].blocks


def test_override_rejects_bad_input():
    with pytest.raises(KeyError):
        load_rules(overrides={"nope": "soft"})
    with pytest.raises(ValueError):
        load_rules(overrides={"approval_country": "medium"})


# ---- approval_country ----

def test_approval_ee_in_italy_violation_with_cite_and_evidence():
    f = by(check_train([cell("30 001", owner="EE")], "Milan", D), "approval_country")
    assert f[0].status == "violation" and "IT" in f[0].message and "30 001" in f[0].message
    assert f[0].cite.startswith("§3") and f[0].evidence[0].sheet == "Milan"


def test_approval_gff_rules():
    assert by(check_train([cell("91 309", owner="Gff", ctype="Bimz")], "Milan", D), "approval_country", "violation")
    assert not by(check_train([cell("91 308", owner="Gff", ctype="Bimz")], "Milan", D), "approval_country")
    assert by(check_train([cell("50 001", owner="Gff", ctype="BDCMbdzf")], "Paris", D), "approval_country", "violation")
    assert not by(check_train([cell("50 001", owner="Gff", ctype="BDCMbdzf")], "Prag", D), "approval_country")


def test_approval_ok_for_rdc_and_unknown_owner():
    ok = by(check_train([cell("71 120")], "Milan", D, include_ok=True), "approval_country")
    assert [f.status for f in ok] == ["ok"]
    unk = by(check_train([cell("40 001", owner="SVG")], "Prag", D, include_ok=True), "approval_country")
    assert [f.status for f in unk] == ["unknown"]


# ---- max_length ----

def test_max_length_milan_counts_shunted():
    cs = good_mix(extra=6) + [cell("90 001", kind="shunt_in"), cell("90 002", kind="shunt_out")]  # 14 > 13
    f = by(check_train(cs, "Milan", D), "max_length")
    assert f[0].status == "violation" and "14" in f[0].message and "13" in f[0].message


def test_max_length_prag_limit_15_and_paris_13():
    assert by(check_train(good_mix("Prag", extra=10), "Prag", D), "max_length", "violation")       # 16 > 15
    assert not by(check_train(good_mix("Prag", extra=9), "Prag", D), "max_length")                 # 15
    assert by(check_train(good_mix("Paris", extra=8), "Paris", D), "max_length", "violation")      # 14 > 13
    ok = by(check_train(good_mix("Paris", extra=7), "Paris", D, include_ok=True), "max_length")
    assert [f.status for f in ok] == ["ok"]


def test_max_length_exchange_en_route_is_unknown():
    cs = good_mix("Paris", extra=6) + [cell("90 001", "Paris", kind="shunt_in"),
                                       cell("90 002", "Paris", kind="shunt_in")]  # base 12, all 14
    f = by(check_train(cs, "Paris", D), "max_length")
    assert f[0].status == "unknown" and "2 shunted in" in f[0].message and "exchange" in f[0].message


# ---- product rules ----

def test_product_matches_car():
    bad = [cell("71 455", product="1", ctype="WL33"), cell("91 309", product="3", ctype="Bimz")]
    assert len(by(check_train(bad, "Prag", D), "product_matches_car", "violation")) == 2
    good = [cell("71 455", product="4", ctype="WL33"), cell("91 309", product="9", ctype="Bimz")]
    assert not by(check_train(good, "Prag", D), "product_matches_car")


def test_product_mix():
    assert not by(check_train(good_mix(), "Milan", D), "product_mix")
    f = by(check_train(good_mix()[2:], "Milan", D), "product_mix")
    assert f[0].status == "warning" and "product 4: 0 of 2" in f[0].message


def test_second_conductor_from_nine_commercial_cars():
    f = by(check_train(good_mix(extra=3), "Milan", D), "second_conductor")  # 9 commercial
    assert f[0].status == "warning" and "3,000" in f[0].message
    assert "1,600" in by(check_train(good_mix("Paris", extra=3), "Paris", D), "second_conductor")[0].message
    assert not by(check_train(good_mix(extra=2), "Milan", D), "second_conductor")


# ---- doors / HV ----

def test_doors_and_hv_unknown_without_order():
    f = check_train(good_mix(), "Milan", D)
    assert [x.status for x in by(f, "door_orientation")] == ["unknown"]
    assert [x.status for x in by(f, "high_voltage_rear")] == ["unknown"]
    no_sleeper = [cell("71 120")]
    assert not by(check_train(no_sleeper, "Milan", D), "door_orientation")


def o(car, ctype="Bvcmz5", door=None, hv=True):
    return {"car": car, "type": ctype, "door_end": door, "hv": hv}


def test_door_orientation_with_order():
    good = [o("A", "WL33", "front"), o("B"), o("C", "WL33", "rear")]
    assert not by(check_train([], "Milan", D, order=good), "door_orientation")
    bad_front = [o("A", "WL33", "rear"), o("B")]
    bad_tail = [o("B"), o("C", "WL33", "front")]
    assert by(check_train([], "Milan", D, order=bad_front), "door_orientation", "violation")
    assert by(check_train([], "Milan", D, order=bad_tail), "door_orientation", "violation")
    # middle: doorless ends of two sleepers back to back
    mid = [o("X"), o("A", "WL33", "front"), o("B", "WL33", "rear"), o("Y")]
    assert by(check_train([], "Milan", D, order=mid), "door_orientation", "violation")
    unk = [o("X"), o("A", "WL33", None), o("Y")]
    assert by(check_train([], "Milan", D, order=unk), "door_orientation", "unknown")


def test_high_voltage_rear_with_order():
    bad = [o("A", hv=False), o("B"), o("C")]
    f = by(check_train([], "Milan", D, order=bad), "high_voltage_rear")
    assert f[0].status == "violation" and "A" in f[0].cars
    assert not by(check_train([], "Milan", D, order=[o("A"), o("B")]), "high_voltage_rear")
    last = by(check_train([], "Milan", D, order=[o("A"), o("B", hv=False)]), "high_voltage_rear")
    assert [x.status for x in last] == ["unknown"]  # loco side change unknown: never "ok"


# ---- day level ----

def test_check_day_double_planned_and_static_unknown_once():
    cells = good_mix("Milan") + good_mix("Paris")[:1] + [cell("99 999", "Prag", date="2027-01-02")]
    f = check_day(FakePlan(cells), D)
    dp = by(f, "double_planned")
    assert len(dp) == 1 and dp[0].status == "violation" and dp[0].cars == ["10 001"]
    assert len(dp[0].evidence) == 2
    assert len(by(f, "workshop_interval")) == 1 and by(f, "workshop_interval")[0].status == "unknown"
    assert not by(check_day(FakePlan(good_mix("Milan")), D), "double_planned")


def test_workshop_interval_not_per_car_in_check_train():
    assert not by(check_train(good_mix(), "Milan", D), "workshop_interval")
    assert len(by(check_train(good_mix(), "Milan", D, include_unknown_static=True), "workshop_interval")) == 1


def test_p_repeat_defect_proposal():
    marks = [cell("10 006", date=f"2026-12-{d}", kind="reserve", colour_meaning="Car not usable") for d in (24, 27, 30)]
    f = by(check_day(FakePlan(good_mix() + marks), D), "p_repeat_defect")
    assert len(f) == 1 and f[0].tier == "proposed" and f[0].message.startswith("PROPOSAL:")
    few = marks[:2]
    assert not by(check_day(FakePlan(good_mix() + few), D), "p_repeat_defect")


# ---- check_steps ----

CTX = {"cars": {"70 057": {"owner": "EE", "type": "Bocmh6"}, "91 309": {"owner": "Gff", "type": "Bimz"},
                "71 120": {"owner": "RDC", "type": "Bvcmz5"},
                "71 999": {"owner": "RDC", "type": "Bvcmz5", "cs": True, "line": "Paris"}},
       "trains": {"401": {"line": "Milan"}, "474": {"line": "Paris"}}, "has_pax": set()}


def test_check_steps_refuses_ee_and_gff309_onto_milan_allows_rdc():
    ee = check_steps([Step("beistellen", car="70 057", train="401", at="DDL")], CTX)
    gff = check_steps([Step("beistellen", car="91 309", train="401", at="DDL")], CTX)
    rdc = check_steps([Step("beistellen", car="71 120", train="401", at="DDL")], CTX)
    for f in (ee, gff):
        assert f[0].rule_id == "approval_country" and f[0].blocks and f[0].cite.startswith("§3")
    assert rdc == []


def test_check_steps_pax_weekend_mattress():
    ctx = dict(CTX, has_pax={"71 120"})
    assert by(check_steps([Step("shunt_out", car="71 120", at="DDL")], ctx), "no_shunt_with_pax", "violation")
    assert not check_steps([Step("shunt_out", car="71 120", at="DDL")], CTX)
    sat = dict(CTX, date="2027-01-02")
    assert by(check_steps([Step("abholen", car="71 120", at="ELEN")], sat), "weekend_workshop", "warning")
    assert not check_steps([Step("abholen", car="71 120", at="ELEN")], dict(CTX, date="2027-01-01"))
    assert not check_steps([Step("abholen", car="71 120", at="DDL")], sat)  # not a workshop
    assert not check_steps([Step("abholen", car="71 120", at="ELEN")], CTX)  # no date
    cs = check_steps([Step("beistellen", car="71 999", train="401", at="DDL")], CTX)
    assert by(cs, "mattress_stays", "warning")
    assert not check_steps([Step("beistellen", car="71 999", train="474", at="ELEN")], CTX)


def test_advisory_mode_flags_instead_of_refusing():
    adv = load_rules(advisory=True)
    assert adv["approval_country"]["tier"] == "soft" and adv["double_planned"]["tier"] == "hard"
    f = check_steps([Step("beistellen", car="70 057", train="401", at="DDL")], CTX, rules=adv)
    assert f and f[0].rule_id == "approval_country" and f[0].status == "warning" and not f[0].blocks
