"""Lane A tests. Synthetic workbooks only; real-file tests skip when the data is absent."""
import datetime as dt

import openpyxl
import pytest
from openpyxl.styles import PatternFill
from openpyxl.styles.colors import Color

from relay.config import FLEET_XLSX, WEEKINFO_XLSX
from relay.ingest import composition, load_ops, load_orders, load_plan, where_is
from relay.ingest.ops import LAST_DROPPED
from relay.ingest.plan import type_value

RED = PatternFill("solid", fgColor="FFFF0000")
THEME9 = PatternFill("solid", fgColor=Color(theme=9, tint=0.0))
THEME9_TINT = PatternFill("solid", fgColor=Color(theme=9, tint=0.4))


def make_fleet(path, values):
    """values: {(sheet, row, col): value or (value, fill)}; car ids '70 001'.. in G..; dates from B4."""
    wb = openpyxl.Workbook()
    lg = wb.active
    lg.title = "Legend"
    lg["A2"], lg["A2"].fill = "not usable", RED
    lg["A6"], lg["A6"].fill = "confirmed", THEME9
    for sheet in ("Prag", "Paris", "Milan"):
        ws = wb.create_sheet(sheet)
        for i, c in enumerate(range(7, 10)):
            ws.cell(1, c, "Gff" if i < 2 else "EE")
            ws.cell(2, c, "WL30")
            ws.cell(3, c, f"70 00{i + 1}")
        ws.cell(1, 10, "RDC"), ws.cell(2, 10, "Bvcmz"), ws.cell(3, 10, 207.0)  # float id in row 3
        for r in range(4, 8):
            ws.cell(r, 2, dt.datetime(2026, 10, r - 3))
    wb.create_sheet("Order Prs")
    for (sheet, r, c), v in values.items():
        cell = wb[sheet].cell(r, c)
        if isinstance(v, tuple):
            cell.value, cell.fill = v
        else:
            cell.value = v
    wb.save(path)
    return path


@pytest.fixture
def plan(tmp_path):
    p = make_fleet(tmp_path / "fleet.xlsx", {
        ("Prag", 4, 7): 1.0, ("Prag", 4, 8): "Ddl", ("Prag", 4, 9): "ddl", ("Prag", 4, 10): "milaan",
        ("Prag", 5, 7): "=-1", ("Prag", 5, 8): 1.30416666666667, ("Prag", 5, 9): (None, RED),
        ("Prag", 5, 10): ("X", THEME9),
        ("Prag", 6, 7): (4.0, THEME9_TINT), ("Prag", 6, 8): "UHD", ("Prag", 6, 9): 452.0,
        ("Prag", 6, 10): "XD",
        ("Paris", 4, 7): 0.0, ("Paris", 4, 8): -1.0, ("Paris", 4, 9): "x", ("Paris", 4, 10): 3.0,
        ("Paris", 5, 7): "Rev", ("Paris", 5, 8): -2.0, ("Paris", 5, 9): "Milaan",
        ("Paris", 9, 7): "orphan",   # no date in B9
        ("Order Prs", 1, 1): dt.datetime(2026, 10, 1),
        ("Order Prs", 2, 1): "61 80 50-70 001-7 [BA]", ("Order Prs", 2, 2): 32.0,
        ("Order Prs", 3, 1): "61 80 50-70  002-7",   # double space: not read
    })
    return load_plan(p)


def cell(plan, line, date, car):
    return next(c for c in plan.cells if (c.line, c.date, c.car) == (line, date, car))


def test_whole_float_normalised(plan):
    c = cell(plan, "Prag", "2026-10-01", "70 001")
    assert (c.raw, c.kind, c.product) == ("1", "in_train", "1")
    assert c.src.label() == "Prag!G4" and c.src.as_of == "2026-10-01"


def test_float_car_id_normalised(plan):
    assert "207" in plan.cars and plan.cars["207"].owner == "RDC"
    assert plan.cars["70 001"].lines == ["Prag", "Paris", "Milan"]


def test_place_aliases_exact(plan):
    assert cell(plan, "Prag", "2026-10-01", "70 003").place == "DDL"  # Ddl
    c = cell(plan, "Prag", "2026-10-01", "70 002")
    assert (c.kind, c.place) == ("parked", "DDL")                      # ddl
    assert cell(plan, "Prag", "2026-10-01", "207").place == "MIL"     # milaan
    c = cell(plan, "Paris", "2026-10-02", "70 003")                    # Milaan: not in the table
    assert (c.kind, c.place) == ("note", "")


def test_formula_text_and_fraction_rejected(plan):
    a = cell(plan, "Prag", "2026-10-02", "70 001")
    b = cell(plan, "Prag", "2026-10-02", "70 002")
    assert a.kind == b.kind == "unreadable" and a.raw == "=-1"
    cells = {(r["sheet"], r["cell"]) for r in plan.rejects}
    assert {("Prag", "G5"), ("Prag", "H5"), ("Paris", "G9")} <= cells
    assert all(r["reason"] for r in plan.rejects)


def test_colour_keys_theme_vs_rgb(plan):
    co = cell(plan, "Prag", "2026-10-02", "70 003")
    assert (co.kind, co.raw, co.colour, co.colour_meaning) == ("colour_only", "", "rgb/FFFF0000", "not usable")
    x = cell(plan, "Prag", "2026-10-02", "207")
    assert (x.kind, x.colour, x.colour_meaning) == ("reserve", "theme/9/0.0", "confirmed")
    tinted = cell(plan, "Prag", "2026-10-03", "70 001")                 # same theme, other tint
    assert tinted.colour == "theme/9/0.4" and tinted.colour_meaning == "UNKNOWN"
    assert cell(plan, "Prag", "2026-10-01", "70 001").colour == ""


def test_codes(plan):
    k = {c.car: c.kind for c in plan.cells if c.line == "Paris" and c.date == "2026-10-01"}
    assert k == {"70 001": "shunt_in", "70 002": "shunt_out", "70 003": "reserve", "207": "in_train"}
    assert cell(plan, "Paris", "2026-10-02", "70 001").kind == "revision"
    assert cell(plan, "Paris", "2026-10-02", "70 002").kind == "transfer"
    assert cell(plan, "Prag", "2026-10-03", "70 002").kind == "note"   # UHD: not guessed
    assert cell(plan, "Prag", "2026-10-03", "70 003").raw == "452"     # number, not a code
    assert cell(plan, "Prag", "2026-10-03", "70 003").kind == "note"
    assert cell(plan, "Prag", "2026-10-03", "207").kind == "note"      # XD is not reserve


def test_type_value_unit():
    assert type_value("Chiasso")[:3] == ("parked", "", "CHI")
    assert type_value("chiasso")[0] == "note"
    assert type_value(9)[:2] == ("in_train", "9")


def test_where_is_and_composition(plan):
    for car in ("70 001", "70001"):
        hits = where_is(plan, car, "2026-10-01")
        assert [h.line for h in hits] == ["Prag", "Paris"]
    assert where_is(plan, "70 0 01x", "2026-10-01") == []
    comp = composition(plan, "Paris", "2026-10-01")
    assert [c.car for c in comp] == ["70 001", "70 002", "207"]     # x (reserve) left out, column order


def test_cache_per_path(plan, tmp_path):
    assert load_plan(tmp_path / "fleet.xlsx") is plan


def test_orders_best_effort(plan, tmp_path):
    o = load_orders(tmp_path / "fleet.xlsx", known_cars=set(plan.cars))
    assert len(o["items"]) == 1
    it = o["items"][0]
    assert (it["car"], it["right_value"], it["date_above"], it["src"].label()) == \
        ("70 001", "32", "2026-10-01", "Order Prs!A2")
    assert o["unread"]["Order Prs"]["uic_like_not_exact"] == 1


def make_weekinfo(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Weekinfo Prs"
    ws.append(["Week", "Date", "Departure", "Arrival", "Pax", "%>1 hour", "Downgrades",
               "Cancelations", "Bikes", "extra info"])
    ws.append([None, dt.datetime(2026, 10, 1), 5, "?", 250, 12, 1, 0, None, "1 sleeper"])      # Thu
    ws.append([None, dt.datetime(2026, 10, 2), "canceled", None, 0, None, None, 3, None, None])  # Fri
    ws.append([None, dt.datetime(2026, 10, 3), None, None, "=AVERAGE(E2:E3)", None,
               "=SUM(G2:G3)/SUM(E2:E3)", None, None, None])                                  # Sat avg
    ws.append([None, dt.datetime(2026, 10, 6), None, None, "=AVERAGE(E2:E3)", None, None, None])  # Tue avg
    ws.append([None, dt.datetime(2026, 10, 10), 7, 8, 200, 2.5, None, None, None, None])     # Sat run
    wb.save(path)
    return path


def test_ops(tmp_path):
    nights = load_ops(make_weekinfo(tmp_path / "w.xlsx"))
    assert [n.date for n in nights] == ["2026-10-01", "2026-10-02", "2026-10-10"]
    a, b, sat = nights
    assert (a.line, a.dep_delay, a.arr_delay, a.arr_raw, a.pax, a.pax_over_1h) == ("Paris", 5, None, "?", 250, 12)
    assert (b.dep_delay, b.dep_raw, b.cancellations) == (None, "canceled", 3)
    assert a.note == "1 sleeper" and a.src.label() == "Weekinfo Prs!B2"
    assert sat.pax_over_1h is None and LAST_DROPPED["unreadable"][0]["cell"] == "F6"
    assert LAST_DROPPED["Weekinfo Prs"]["summary_rows"] == 2


real = pytest.mark.skipif(not FLEET_XLSX.exists(), reason="real fleet workbook absent")


@real
def test_real_plan():
    p = load_plan()
    assert len(p.cars) == 70
    assert p.rejects and all(r["sheet"] and r["cell"] for r in p.rejects)
    c = where_is(p, "71455", "2026-10-02")
    assert c and c[0].src.cell and c[0].kind in ("in_train", "reserve", "parked", "colour_only",
                                                  "shunt_in", "shunt_out", "note", "revision")
    assert any(r["raw"] == "=-1" for r in p.rejects)
    assert len(p.legend) == 11


@real
def test_real_known_cell_round_trips():
    """Pick a typed cell and read it back from the workbook directly."""
    p = load_plan()
    c = next(c for c in p.cells if c.kind == "in_train")
    ws = openpyxl.load_workbook(FLEET_XLSX)[c.line]
    v = ws[c.src.cell].value
    assert str(int(v)) == c.raw == c.product


@pytest.mark.skipif(not WEEKINFO_XLSX.exists(), reason="real weekinfo absent")
def test_real_ops():
    nights = load_ops()
    assert nights and all(n.src and n.src.cell for n in nights)
    assert {n.line for n in nights} == {"Prag", "Paris", "Milan"}
