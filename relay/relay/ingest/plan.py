"""Read the fleet grid (sheets Prag, Paris, Milan) into typed Cells. Never guesses."""
from __future__ import annotations

import datetime as dt
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from relay.config import FLEET_XLSX
from relay.model import Car, Cell, Src

LINES = ("Prag", "Paris", "Milan")
FIRST_CAR, LAST_CAR = 7, 76          # columns G..BX
FIRST_ROW = 4
IN_TRAIN = {"1", "2", "3", "4", "9"}
NUMERIC_KIND = {"0": "shunt_in", "-1": "shunt_out", "-2": "transfer"}
RESERVE = {"X", "x"}
REVISION = {"rev", "Rev"}
# Exact strings only. Anything not here is a note; never matched by similarity.
PLACE_ALIASES = {
    "Ddl": "DDL", "ddl": "DDL",
    "milaan": "MIL",
    "Praag": "PRG", "prg": "PRG", "Prag": "PRG",
    "Wusti": "WUS",
    "Chiasso": "CHI",
    "ELEN": "ELEN",
    "Crails": "CRA",
    "Paris": "PAR",
}
TRAIN_KINDS = ("in_train", "shunt_in", "shunt_out")


@dataclass
class Plan:
    cells: list[Cell]
    cars: dict[str, Car]
    rejects: list[dict]
    legend: dict[str, str]
    _index: dict = field(default_factory=dict, repr=False)


def fill_key(cell) -> str:
    """Whole colour key: 'rgb/FFFF0000', 'theme/7/0.0', 'indexed/5' or '' (no solid fill)."""
    f = cell.fill
    if f is None or f.fill_type != "solid":
        return ""
    c = f.fgColor
    if c.type == "rgb":
        return f"rgb/{c.rgb}"
    if c.type == "theme":
        return f"theme/{c.theme}/{round(c.tint, 3)}"
    return f"{c.type}/{c.indexed}"


def norm_value(v) -> str:
    """Cell value as text; whole floats lose their '.0'."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def type_value(v) -> tuple[str, str, str, str]:
    """(kind, product, place, reject_reason). reject_reason is '' when the value was typed."""
    if v is None:
        return "colour_only", "", "", ""
    if isinstance(v, bool) or isinstance(v, (dt.datetime, dt.date, dt.time)):
        return "unreadable", "", "", f"unexpected {type(v).__name__} value"
    if isinstance(v, float) and not v.is_integer():
        return "unreadable", "", "", "number that is not a whole code"
    if isinstance(v, (int, float)):
        s = str(int(v))
        if s in IN_TRAIN:
            return "in_train", s, "", ""
        if s in NUMERIC_KIND:
            return NUMERIC_KIND[s], "", "", ""
        return "note", "", "", ""
    if not isinstance(v, str):
        return "unreadable", "", "", f"unexpected {type(v).__name__} value"
    if v.startswith("="):
        return "unreadable", "", "", "formula text, not a code; not evaluated"
    if v in RESERVE:
        return "reserve", "", "", ""
    if v in REVISION:
        return "revision", "", "", ""
    if v in PLACE_ALIASES:
        return "parked", "", PLACE_ALIASES[v], ""
    return "note", "", "", ""


def _norm_car(car: str) -> str:
    return car.replace(" ", "")


def read_plan(xlsx) -> Plan:
    """Read the workbook without caching."""
    path = Path(xlsx)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path)  # not read_only: fills are needed
    fname = path.name
    legend: dict[str, str] = {}
    if "Legend" in wb.sheetnames:
        lg = wb["Legend"]
        for r in range(2, 13):
            key = fill_key(lg.cell(r, 1))
            if key and lg.cell(r, 1).value is not None:
                legend[key] = str(lg.cell(r, 1).value)
    cells: list[Cell] = []
    cars: dict[str, Car] = {}
    rejects: list[dict] = []
    for line in LINES:
        if line not in wb.sheetnames:
            continue
        ws = wb[line]
        heads = []
        for c in range(FIRST_CAR, LAST_CAR + 1):
            car = norm_value(ws.cell(3, c).value)
            owner = norm_value(ws.cell(1, c).value)
            ctype = norm_value(ws.cell(2, c).value)
            heads.append((c, car, owner, ctype))
            if not car:
                continue
            if car not in cars:
                cars[car] = Car(car, owner, ctype, [], Src(fname, line, f"{get_column_letter(c)}3"))
            cars[car].lines.append(line)
        for row in ws.iter_rows(min_row=FIRST_ROW, min_col=1, max_col=LAST_CAR):
            day = row[1].value
            for c, car, owner, ctype in heads:
                x = row[c - 1]
                key = fill_key(x)
                if x.value is None and not key:
                    continue
                addr = x.coordinate
                if not isinstance(day, (dt.datetime, dt.date)):
                    if x.value is not None:
                        rejects.append({"sheet": line, "cell": addr, "raw": norm_value(x.value),
                                        "reason": "value in a row without a date in column B"})
                    continue
                if not car:
                    if x.value is not None:
                        rejects.append({"sheet": line, "cell": addr, "raw": norm_value(x.value),
                                        "reason": "value in a column without a car id in row 3"})
                    continue
                date = (day.date() if isinstance(day, dt.datetime) else day).isoformat()
                kind, product, place, why = type_value(x.value)
                raw = norm_value(x.value)
                if why:
                    rejects.append({"sheet": line, "cell": addr, "raw": raw, "reason": why})
                cells.append(Cell(line, date, car, owner, ctype, raw, kind, product, place, key,
                                  legend.get(key, "UNKNOWN") if key else "",
                                  Src(fname, line, addr, date)))
    index = {(c.line, c.date, c.car): c for c in cells}
    return Plan(cells, cars, rejects, legend,
                {"cells": index, "cars": {_norm_car(k): k for k in cars}})


_CACHE: dict[str, Plan] = {}


def load_plan(xlsx=FLEET_XLSX) -> Plan:
    """Load the grid once per path."""
    key = str(Path(xlsx).resolve())
    if key not in _CACHE:
        _CACHE[key] = read_plan(xlsx)
    return _CACHE[key]


def car_id(plan: Plan, car: str) -> str:
    """Canonical id for '71455' or '71 455' (exact, spaces removed); '' when unknown."""
    return plan._index["cars"].get(_norm_car(str(car).strip()), "")


def where_is(plan: Plan, car: str, date: str) -> list[Cell]:
    """One Cell per sheet that has an entry for this car on this date."""
    cid = car_id(plan, car)
    if not cid:
        return []
    d = dt.date.fromisoformat(date).isoformat()
    idx = plan._index["cells"]
    return [idx[(ln, d, cid)] for ln in LINES if (ln, d, cid) in idx]


def composition(plan: Plan, line: str, date: str) -> list[Cell]:
    """Cells in that train that night (in_train, shunt_in, shunt_out), grid column order."""
    d = dt.date.fromisoformat(date).isoformat()
    return [c for c in plan.cells if c.line == line and c.date == d and c.kind in TRAIN_KINDS]
