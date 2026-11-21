"""Best-effort read of the free-form order sheets. Only one pattern is read; the rest is counted, not guessed.

What is read: a cell that starts with a UIC car number written exactly as 'NN NN NN-NN NNN-N'.
For each such cell we keep the number, any text after it, the value in the cell to its right
(as written, not interpreted), the nearest date above it in the same column (as written), and the
planning id ('NN NNN', taken from the UIC digits) only when that id is a car in the grid.
"""
from __future__ import annotations

import datetime as dt
import re
import warnings
from pathlib import Path

import openpyxl

from relay.config import FLEET_XLSX
from relay.model import Src

SHEETS = ("Order Prag", "Order Prs", "Order Mln")
UIC = re.compile(r"^(\d\d) (\d\d) (\d\d)-(\d\d) (\d\d\d)-(\d)(?!\d)(.*)$", re.S)
LOOSE = re.compile(r"\d\d ?\d\d ?\d\d")


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dt.datetime, dt.date)):
        return v.date().isoformat() if isinstance(v, dt.datetime) else v.isoformat()
    return str(v)


def load_orders(xlsx=FLEET_XLSX, known_cars: set[str] | None = None) -> dict:
    """{'items': [...], 'unread': {sheet: {...counts}}, 'note': str}. Each item carries a Src."""
    path = Path(xlsx)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, data_only=True)
    if known_cars is None:
        from relay.ingest.plan import load_plan
        known_cars = set(load_plan(path).cars)
    items: list[dict] = []
    unread: dict = {}
    for sheet in SHEETS:
        if sheet not in wb.sheetnames:
            unread[sheet] = {"missing_sheet": 1}
            continue
        ws = wb[sheet]
        last_date: dict[int, tuple[str, str]] = {}   # column -> (date, cell)
        n_other = n_uic_like = 0
        for row in ws.iter_rows():
            for x in row:
                v = x.value
                if v is None:
                    continue
                if isinstance(v, (dt.datetime, dt.date)):
                    last_date[x.column] = (_text(v), x.coordinate)
                    continue
                m = UIC.match(v) if isinstance(v, str) else None
                if not m:
                    if isinstance(v, str) and LOOSE.search(v):
                        n_uic_like += 1
                    else:
                        n_other += 1
                    continue
                g = m.groups()
                planning = f"{g[3]} {g[4]}"
                date, date_cell = last_date.get(x.column, ("", ""))
                items.append({
                    "sheet": sheet,
                    "uic": f"{g[0]} {g[1]} {g[2]}-{g[3]} {g[4]}-{g[5]}",
                    "car": planning if planning in known_cars else "",
                    "text_after": g[6].strip(),
                    "right_value": _text(ws.cell(x.row, x.column + 1).value),
                    "date_above": date,
                    "date_cell": date_cell,
                    "src": Src(path.name, sheet, x.coordinate),  # as_of left empty: date meaning not established
                })
        unread[sheet] = {"uic_like_not_exact": n_uic_like, "other_cells": n_other}
    note = ("Only cells starting with an exactly formatted UIC number are read. 'date_above' is the "
            "nearest date above in the same column, as written; whether it is the train night is "
            "not established. 'right_value' is the neighbouring cell as written (often a number, "
            "meaning not established). Layouts differ per sheet; Order Prag's top block is a "
            "transposed type-by-date table that is not read, and some of its 'date_above' values come "
            "from far above (check 'date_cell'). Cells not read are counted in 'unread'.")
    return {"items": items, "unread": unread, "note": note}
