"""Read the three Weekinfo sheets into OpsNight rows. Weekly summary rows are dropped."""
from __future__ import annotations

import datetime as dt
import re
import warnings
from pathlib import Path

import openpyxl

from relay.config import WEEKINFO_XLSX
from relay.model import OpsNight, Src

SHEETS = {"Weekinfo Prg": "Prag", "Weekinfo Prs": "Paris", "Weekinfo Mln": "Milan"}
AGGREGATE = re.compile(r"\b(AVERAGE|SUM)\s*\(", re.I)
# Columns: B date, C departure, D arrival, E pax, F count >1h late, G downgrades, H cancellations, J extra info.
B, C, D, E, F, G, H, J = 2, 3, 4, 5, 6, 7, 8, 10

LAST_DROPPED: dict = {}   # filled by load_ops: summary rows dropped per sheet + 'unreadable' cells


def _int(v) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _delay(v) -> tuple[int | None, str]:
    n = _int(v)
    if n is not None or v is None:
        return n, ""
    return None, str(v)


def _is_summary(ws_formula, r: int) -> bool:
    """A row is a weekly summary when C..I hold an AVERAGE(..)/SUM(..) formula."""
    for c in range(C, 10):
        v = ws_formula.cell(r, c).value
        text = getattr(v, "text", v)  # ArrayFormula keeps its text in .text
        if isinstance(text, str) and text.startswith("=") and AGGREGATE.search(text):
            return True
    return False


def load_ops(xlsx=WEEKINFO_XLSX) -> list[OpsNight]:
    """One OpsNight per dated run row; rows with aggregate formulas (weekly averages) are dropped."""
    path = Path(xlsx)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wf = openpyxl.load_workbook(path)                  # formulas, to spot summary rows
        wv = openpyxl.load_workbook(path, data_only=True)  # cached values (pax is an external link)
    out: list[OpsNight] = []
    dropped: dict = {}
    unreadable: list[dict] = []
    for sheet, line in SHEETS.items():
        if sheet not in wv.sheetnames:
            continue
        vs, fs = wv[sheet], wf[sheet]
        drop = {"summary_rows": 0, "summary_saturdays": 0, "summary_other_weekdays": 0, "undated_rows": 0}
        for r in range(2, vs.max_row + 1):
            day = vs.cell(r, B).value
            if not isinstance(day, (dt.datetime, dt.date)):
                if any(vs.cell(r, c).value is not None for c in range(C, J + 1)):
                    drop["undated_rows"] += 1
                continue
            day = day.date() if isinstance(day, dt.datetime) else day
            if _is_summary(fs, r):
                drop["summary_rows"] += 1
                drop["summary_saturdays" if day.weekday() == 5 else "summary_other_weekdays"] += 1
                continue
            dep, dep_raw = _delay(vs.cell(r, C).value)
            arr, arr_raw = _delay(vs.cell(r, D).value)
            note = vs.cell(r, J).value
            for c in (E, F, G, H):
                v = vs.cell(r, c).value
                if v is not None and _int(v) is None:
                    unreadable.append({"sheet": sheet, "cell": vs.cell(r, c).coordinate,
                                       "raw": str(v), "reason": "not a whole number; set to None"})
            out.append(OpsNight(line, day.isoformat(), dep, arr,
                                _int(vs.cell(r, E).value), _int(vs.cell(r, F).value),
                                _int(vs.cell(r, G).value), _int(vs.cell(r, H).value),
                                "" if note is None else str(note), dep_raw, arr_raw,
                                Src(path.name, sheet, f"B{r}", day.isoformat())))
        dropped[sheet] = drop
    LAST_DROPPED.clear()
    LAST_DROPPED.update(dropped)
    LAST_DROPPED["unreadable"] = unreadable
    return out
