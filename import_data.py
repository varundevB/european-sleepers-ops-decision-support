"""Read the supplied European Sleeper workbooks into the local application DB.

Source workbooks are opened for extraction only and are never saved or modified.
Planning codes, status fills, comments and their original cell addresses remain
separate evidence. Excel's cached capacity totals are deliberately not imported.
"""
from __future__ import annotations

import ast
from collections import Counter
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import openpyxl

LINES = ("Prag", "Paris", "Milan")
RUNNING_SHEETS = {"Prag": "Weekinfo Prg", "Paris": "Weekinfo Prs", "Milan": "Weekinfo Mln"}
FIRST_CAR, LAST_CAR = 7, 76


def ensure_data_schema(conn: sqlite3.Connection) -> None:
    """Create only importer-owned tables; do not touch cases or communications."""
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS cars(id TEXT PRIMARY KEY,owner TEXT,type TEXT);
      CREATE TABLE IF NOT EXISTS fleet(
        line TEXT,date TEXT,car_id TEXT,code TEXT NULL,colour_meaning TEXT,
        colour TEXT,location TEXT NULL,notes TEXT,source_sheet TEXT,source_cell TEXT,
        PRIMARY KEY(line,date,car_id));
      CREATE TABLE IF NOT EXISTS line_days(
        line TEXT,date TEXT,pax REAL NULL,notes TEXT,source_sheet TEXT,source_row INTEGER,
        PRIMARY KEY(line,date));
      CREATE TABLE IF NOT EXISTS running(
        line TEXT,date TEXT,departure_delay TEXT NULL,arrival_delay TEXT NULL,pax REAL NULL,
        notes TEXT,late_passengers REAL NULL,downgrades REAL NULL,cancellations REAL NULL,
        bikes REAL NULL,source_sheet TEXT,source_row INTEGER,PRIMARY KEY(line,date));
      CREATE INDEX IF NOT EXISTS fleet_date_line ON fleet(date,line);
      CREATE INDEX IF NOT EXISTS fleet_car_date ON fleet(car_id,date);
      CREATE INDEX IF NOT EXISTS running_date_line ON running(date,line);
    """)


def fill_key(cell: Any) -> tuple | None:
    """Compare solid fills by colour representation *and* tint, never RGB alone."""
    fill = cell.fill
    if fill is None or fill.fill_type != "solid":
        return None
    colour = fill.fgColor
    if colour.type == "rgb":
        value = colour.rgb
    elif colour.type == "theme":
        value = colour.theme
    elif colour.type == "indexed":
        value = colour.indexed
    elif colour.type == "auto":
        value = colour.auto
    else:
        value = str(colour.value)
    return (colour.type, value, round(float(colour.tint or 0), 6))


def normalize_code(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        return str(int(value)) if float(value).is_integer() else str(value)
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip() or None
    return str(value)


def _formula(cell: Any) -> str:
    if getattr(cell, "data_type", None) != "f":
        return ""
    value = cell.value
    return value if isinstance(value, str) else str(getattr(value, "text", ""))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
            return float(text)
    return None


def _iso_day(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10]).isoformat()
        except ValueError:
            pass
    return None


def _comment(cell: Any) -> str:
    """Remove Office/export wrapper text while retaining the source comment body."""
    comment = getattr(cell, "comment", None)
    if not comment or not comment.text:
        return ""
    text = comment.text
    if "Comment:" in text:
        text = text.split("Comment:", 1)[1]
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("======", "ID#doco.", "tc={", "[Threaded comment]")):
            continue
        if "Your version of Excel allows you" in stripped or "Learn more: https://go.microsoft.com" in stripped:
            continue
        # Exported author/timestamp lines are metadata, not operational instructions.
        if re.search(r"\(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\)\s*$", stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def _note(cell: Any, text: str | None = None) -> str:
    value = _comment(cell) if text is None else text
    return f"[{cell.coordinate}] {value}" if value else ""


def _join_notes(*items: str) -> str:
    return "\n".join(dict.fromkeys(item for item in items if item))


def _location(code: str | None) -> str | None:
    if not code:
        return None
    aliases = {
        "ddl": "Denderleeuw", "denderleeuw": "Denderleeuw",
        "zot": "Zot",  # Preserve the source abbreviation; no city expansion supplied.
        "wusti": "Wustermark", "wustermark": "Wustermark",
        "elen": "Lengerich", "lengerich": "Lengerich",
        "emstg": "Münster", "münster": "Münster", "munster": "Münster",
        "crails": "Crailsheim", "crailsheim": "Crailsheim",
        "prg": "Prague", "prag": "Prague", "praag": "Prague", "prague": "Prague",
        "prs": "Paris", "paris": "Paris", "milaan": "Milan", "milan": "Milan",
        "chiasso": "Chiasso", "the hague": "The Hague",
    }
    return aliases.get(code.casefold())


def is_weekly_summary(row: Any, line: str | None = None) -> bool:
    """Identify aggregation formulas, including early hardcoded Pax summaries.

    A date's weekday alone is not sufficient: Prague includes actual Saturday
    services. Old summary rows with hardcoded mean Pax have AVERAGE in Bikes.
    """
    cells = list(row)
    if len(cells) < 9:
        return False
    # Nonblank departure/arrival observations distinguish special Saturday runs.
    if any(cells[c].value is not None for c in (2, 3)):
        return False
    for index in range(4, 9):
        expression = _formula(cells[index]).upper()
        # Range aggregation of rows in the main daily dataset, never same-row =E8.
        if re.search(r"\b(?:AVERAGE|SUM|SUMIF|COUNT|COUNTIF)\s*\([^)]*[EFGHI]\$?\d+\s*:", expression):
            return True
    return False


def _formula_diagnostics(path: Path) -> dict:
    """Read saved error/formula evidence without recalculating or modifying Excel."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    counts: Counter = Counter()
    samples = []
    with ZipFile(path) as archive:
        for name in archive.namelist():
            if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                continue
            root = ET.fromstring(archive.read(name))
            for cell in root.findall(".//s:sheetData/s:row/s:c", ns):
                f = cell.find("s:f", ns)
                v = cell.find("s:v", ns)
                error = v.text if cell.attrib.get("t") == "e" and v is not None else None
                if not error and f is not None and f.text and "#REF!" in f.text:
                    error = "#REF!"
                if error:
                    counts[error] += 1
                    if len(samples) < 8:
                        samples.append({"worksheet_xml": Path(name).name, "cell": cell.attrib.get("r"), "error": error})
    return {"counts": dict(counts), "samples": samples}


def _safe_arithmetic(expression: str) -> float | None:
    """Only numeric literals and arithmetic; never execute supplied formulas."""
    try:
        tree = ast.parse(expression, mode="eval")
        def walk(node: Any) -> float:
            if isinstance(node, ast.Expression):
                return walk(node.body)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                return float(node.value)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return walk(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            if isinstance(node, ast.BinOp):
                left, right = walk(node.left), walk(node.right)
                if isinstance(node.op, ast.Add): return left + right
                if isinstance(node.op, ast.Sub): return left - right
                if isinstance(node.op, ast.Mult): return left * right
                if isinstance(node.op, ast.Div): return left / right
            raise ValueError("Unsupported expression")
        value = walk(tree)
        return value if math.isfinite(value) else None
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
        return None


def _running_value(cell: Any, cached: Any, resolved: dict[str, float | None], row_number: int) -> float | None:
    if not _formula(cell):
        return _number(cell.value)
    expression = _formula(cell).lstrip("=").strip()
    # Resolve simple same-row references against the corrected Pax value. Do not
    # allow cached zero to leak back into =E8 cancellations or late-passenger fields.
    references = re.findall(r"\$?([C-I])\$?(\d+)", expression)
    if references and all(int(r) == row_number for _, r in references) and not re.search(r"[!:\[\]]", expression):
        if any(resolved.get(col) is None for col, _ in references):
            return None
        expression = re.sub(r"\$?([C-I])\$?\d+", lambda m: str(resolved[m[1]]), expression)
        return _safe_arithmetic(expression)
    literal = _safe_arithmetic(expression)
    return literal if literal is not None else _number(cached.value)


def import_workbooks(conn: sqlite3.Connection, fleet_path: str | Path,
                     running_path: str | Path | None = None) -> dict:
    """Replace imported reference data atomically, preserving app-owned records."""
    fleet_path = Path(fleet_path).expanduser()
    if not fleet_path.is_file():
        raise FileNotFoundError(f"Fleet workbook not found: {fleet_path.name}")
    running_path = Path(running_path).expanduser() if running_path else None
    if running_path and not running_path.is_file():
        raise FileNotFoundError(f"Running-data workbook not found: {running_path.name}")

    warnings: list[str] = []
    workbook = openpyxl.load_workbook(fleet_path, data_only=False, read_only=False)
    missing = [name for name in ("Legend", *LINES) if name not in workbook.sheetnames]
    if missing:
        workbook.close()
        raise ValueError("Fleet workbook is missing required sheets: " + ", ".join(missing))
    legend = workbook["Legend"]
    colour_map = {fill_key(legend.cell(r, 1)): str(legend.cell(r, 1).value)
                  for r in range(2, 13) if fill_key(legend.cell(r, 1)) is not None}
    code_legend = [str(legend.cell(r, 2).value) for r in range(2, 13) if legend.cell(r, 2).value]
    nominal_capacity_rules = [{"type": legend.cell(r, 1).value, "definition": legend.cell(r, 2).value,
                               "source_sheet": "Legend", "source_range": f"A{r}:B{r}"}
                              for r in range(18, 29) if legend.cell(r, 1).value]
    cars: dict[str, tuple] = {}
    fleet_records, line_records = [], []
    pax_by_day: dict[tuple[str, str], float | None] = {}
    fleet_dates, assigned_dates, observed_dates = set(), set(), set()
    unknown_colours: Counter = Counter()
    unknown_codes: Counter = Counter()
    comments_count = 0
    for line in LINES:
        sheet = workbook[line]
        columns = []
        for col in range(FIRST_CAR, LAST_CAR + 1):
            car_id = normalize_code(sheet.cell(3, col).value)
            if not car_id:
                continue
            owner, car_type = normalize_code(sheet.cell(1, col).value), normalize_code(sheet.cell(2, col).value)
            record = (car_id, owner, car_type)
            if car_id in cars and cars[car_id] != record:
                raise ValueError(f"Conflicting owner/type for car {car_id} in {line} header")
            cars[car_id] = record
            columns.append((col, car_id))
        for row in sheet.iter_rows(min_row=4, max_col=85):
            day = _iso_day(row[1].value)
            if not day:
                continue
            fleet_dates.add(day)
            # Raw C is authoritative: a source blank remains NULL, never cached 0.
            pax = _number(row[2].value) if not _formula(row[2]) else None
            pax_by_day[(line, day)] = pax
            if pax is not None:
                observed_dates.add(day)
            day_notes = _join_notes(
                *(_note(c) for c in row[:6]),
                _note(row[84], normalize_code(row[84].value)) if row[84].value is not None and not _formula(row[84]) else "",
                _note(row[84]))
            line_records.append((line, day, pax, day_notes, line, row[1].row))
            for col, car_id in columns:
                cell = row[col - 1]
                colour = fill_key(cell)
                comment = _comment(cell)
                if cell.value is None and colour is None and not comment:
                    continue
                code = normalize_code(cell.value)
                if _formula(cell):
                    literal = _safe_arithmetic(_formula(cell).lstrip("="))
                    code = normalize_code(literal) if literal is not None else _formula(cell)
                colour_meaning = colour_map.get(colour, "UNKNOWN" if colour is not None else "")
                if colour_meaning == "UNKNOWN":
                    unknown_colours[json.dumps(colour)] += 1
                if code in {"1", "2", "3", "4", "9", "0", "-1", "-2"}:
                    assigned_dates.add(day)
                elif code and code.casefold() not in {"x", "rev"} and _location(code) is None:
                    unknown_codes[code] += 1
                if comment:
                    comments_count += 1
                notes = _join_notes(day_notes, _note(cell, comment),
                                    _note(cell, "Source formula: " + _formula(cell)) if _formula(cell) else "")
                fleet_records.append((line, day, car_id, code, colour_meaning,
                                      json.dumps(colour, ensure_ascii=False, separators=(",", ":")) if colour else "",
                                      _location(code), notes, line, cell.coordinate))
    workbook.close()

    running_records, running_dates = [], set()
    weekly_counts: Counter = Counter()
    daily_counts: Counter = Counter()
    template_counts: Counter = Counter()
    linked_counts: Counter = Counter()
    row_mismatches: list[dict] = []
    cache_mismatches = 0
    if running_path:
        raw = openpyxl.load_workbook(running_path, data_only=False, read_only=True)
        cached_book = openpyxl.load_workbook(running_path, data_only=True, read_only=True)
        # Source row dates are used solely to flag fragile external row references.
        fleet_row_dates = {(line, record[5]): record[1] for line in LINES for record in line_records if record[0] == line}
        for line, sheet_name in RUNNING_SHEETS.items():
            if sheet_name not in raw.sheetnames:
                warnings.append(f"Missing running-data sheet: {sheet_name}.")
                continue
            for row, cached_row in zip(raw[sheet_name].iter_rows(min_row=2, max_col=10),
                                       cached_book[sheet_name].iter_rows(min_row=2, max_col=10)):
                day = _iso_day(row[1].value)
                if not day:
                    continue
                if is_weekly_summary(row, line):
                    weekly_counts[line] += 1
                    continue
                expression = _formula(row[4])
                if expression and "[" in expression:
                    linked_counts["external_pax_rows"] += 1
                    if (line, day) in pax_by_day:
                        pax = pax_by_day[(line, day)]
                        linked_counts["joined_by_date"] += 1
                        if pax is None:
                            linked_counts["blank_source_pax"] += 1
                        saved = _number(cached_row[4].value)
                        if pax is not None and saved is not None and saved != pax:
                            cache_mismatches += 1
                        match = re.search(r"!\$?C\$?(\d+)", expression, re.I)
                        referenced_date = fleet_row_dates.get((line, int(match[1]))) if match else None
                        if referenced_date is not None and referenced_date != day:
                            row_mismatches.append({"line": line, "date": day, "source_cell": row[4].coordinate,
                                                   "external_formula": expression, "sandbox_row_date": referenced_date})
                    else:
                        # Older operating history predates the supplied fleet grid.
                        pax = _number(cached_row[4].value)
                        linked_counts["external_cache_only"] += 1
                else:
                    pax = _running_value(row[4], cached_row[4], {}, row[4].row)
                source_row = row[1].row
                resolved = {"E": pax}
                for index in (2, 3, 5, 6, 7, 8):
                    col = openpyxl.utils.get_column_letter(index + 1)
                    resolved[col] = _running_value(row[index], cached_row[index], resolved, source_row)
                notes = _join_notes(normalize_code(row[9].value) or "", *(_note(c) for c in row))
                # Calendar/template rows with no observations are not operational runs.
                if pax is None and not notes and all(resolved[col] is None for col in ("C", "D", "F", "G", "H", "I")):
                    template_counts[line] += 1
                    continue
                running_dates.add(day)
                daily_counts[line] += 1
                running_records.append((line, day, normalize_code(resolved["C"]), normalize_code(resolved["D"]), pax,
                                        notes, resolved["F"], resolved["G"], resolved["H"], resolved["I"], sheet_name, row[1].row))
        raw.close()
        cached_book.close()

    diagnostics = {fleet_path.name: _formula_diagnostics(fleet_path)}
    if running_path:
        diagnostics[running_path.name] = _formula_diagnostics(running_path)
    for name, evidence in diagnostics.items():
        if evidence["counts"]:
            summary = ", ".join(f"{n} {error}" for error, n in evidence["counts"].items())
            warnings.append(f"{name}: existing saved formula errors ({summary}); no recalculation or repairs performed.")
    if linked_counts:
        warnings.append("Running Pax contains external workbook references; matched by line/date to raw fleet Pax. Source blanks remain missing, not zero.")
    if linked_counts["external_cache_only"]:
        warnings.append(f"{linked_counts['external_cache_only']} older Pax rows use saved external values because their dates predate the supplied fleet grid.")
    if row_mismatches:
        warnings.append(f"{len(row_mismatches)} external Pax row/date references differ from the sandbox; the importer uses date-based joins.")
    if cache_mismatches:
        warnings.append(f"{cache_mismatches} saved running Pax values differ from raw fleet Pax; raw fleet values were retained.")
    if unknown_colours:
        warnings.append(f"{sum(unknown_colours.values())} cells have solid fills absent from the colour legend; fills are preserved without inferred status.")
    if unknown_codes:
        warnings.append("Free-text and unrecognised fleet codes are retained as source data, without assumed availability or location.")
    warnings.extend([
        "Fleet rows include historical plans and future templates; a dated allocation is not proof a train actually ran.",
        "Capacity is nominal by car type/product code; cached capacity totals are not used. Exact booking, compartment blocking and orientation data are incomplete.",
        "Source comments are retained at their original cells; copied or repeated comments are not independently verified.",
        "Car identifiers are short planning IDs, not full UIC numbers; one header combines '100-7 / 038-5'.",
        "Running late-passenger, downgrade and cancellation fields are daily counts; weekly summary rows are excluded.",
    ])
    counts = {"cars": len(cars), "fleet": len(fleet_records), "line_days": len(line_records),
              "running": len(running_records), "running_daily_by_line": dict(daily_counts),
              "weekly_rows_excluded": dict(weekly_counts), "empty_running_rows_excluded": dict(template_counts),
              "car_comments": comments_count}
    metadata = {
        "imported": True, "imported_at": datetime.now(timezone.utc).isoformat(),
        "source_files": [fleet_path.name] + ([running_path.name] if running_path else []),
        "date_min": min(fleet_dates) if fleet_dates else None, "date_max": max(fleet_dates) if fleet_dates else None,
        "suggested_date": "2026-10-19" if "2026-10-19" in fleet_dates else max(observed_dates or assigned_dates or fleet_dates, default=None),
        "last_fleet_pax_date": max(observed_dates, default=None),
        "running_date_min": min(running_dates, default=None), "running_date_max": max(running_dates, default=None),
        "warnings": warnings, "counts": counts, "code_legend": code_legend,
        "colour_legend": [{"signature": list(k), "meaning": v} for k, v in colour_map.items()],
        "nominal_capacity_rules": nominal_capacity_rules, "source_formula_errors": diagnostics,
        "unknown_colour_signatures": dict(unknown_colours), "unknown_codes": dict(unknown_codes),
        "external_pax": dict(linked_counts), "external_row_date_mismatches": row_mismatches,
        "cell_provenance": {"fleet": "source_sheet/source_cell; car metadata from rows 1:3 of that column",
                             "line_days": "source_sheet/source_row; Pax column C; notes include their cell addresses",
                             "running": "source_sheet/source_row; delays C:D, Pax E, counts F:I, notes J"},
    }
    # Schema setup is idempotent. Reference replacement is atomic; any invalid
    # primary key rolls back the entire replacement, preserving the old dataset.
    ensure_data_schema(conn)
    with conn:
        for table in ("fleet", "line_days", "running", "cars", "metadata"):
            conn.execute(f"DELETE FROM {table}")
        conn.executemany("INSERT INTO cars(id,owner,type) VALUES(?,?,?)", cars.values())
        conn.executemany("INSERT INTO fleet VALUES(?,?,?,?,?,?,?,?,?,?)", fleet_records)
        conn.executemany("INSERT INTO line_days VALUES(?,?,?,?,?,?)", line_records)
        conn.executemany("INSERT INTO running VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", running_records)
        conn.executemany("INSERT INTO metadata(key,value) VALUES(?,?)",
                         ((key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()))
    return metadata
