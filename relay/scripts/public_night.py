"""Cut one night out of Infrabel's monthly punctuality file (open data, CC0) for the replay screen.

  python3 -I scripts/public_night.py <path/to/YYYYMM.csv> 2026-10-05 401

Writes reference/public/infrabel_<date>.json: planned and actual times of that train at each Belgian
point, plus the first point of every other European Sleeper train that ran that day.
Source: https://opendata.infrabel.be/explore/dataset/stiptheid-gegevens-maandelijksebestanden/
"""
import csv
import datetime
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "reference" / "public"
ES_TRAINS = ("453", "452", "475", "474", "401", "400")


def main(argv):
    src, date, train = Path(argv[0]), argv[1], argv[2]
    datdep = datetime.date.fromisoformat(date).strftime("%d%b%Y").upper()
    rows = [r for r in csv.DictReader(src.open(encoding="utf-8-sig")) if r["DATDEP"] == datdep]

    def point(r):
        return {"name": r["PTCAR_LG_NM_NL"].title(),
                "plan_arr": r["PLANNED_TIME_ARR"], "plan_dep": r["PLANNED_TIME_DEP"],
                "real_arr": r["REAL_TIME_ARR"], "real_dep": r["REAL_TIME_DEP"],
                "delay_arr_s": int(r["DELAY_ARR"]) if r["DELAY_ARR"] else None,
                "delay_dep_s": int(r["DELAY_DEP"]) if r["DELAY_DEP"] else None}

    mine = [point(r) for r in rows if r["TRAIN_NO"] == train]
    if not mine:
        raise SystemExit(f"train {train} has no rows on {datdep} in {src.name}")
    fleet = {}
    for t in ES_TRAINS:
        tr = [point(r) for r in rows if r["TRAIN_NO"] == t]
        if tr:
            fleet[t] = {"first": tr[0], "last": tr[-1], "points": len(tr)}
    out = {"source": "Infrabel open data, monthly punctuality file " + src.name,
           "licence": "CC0", "url": "https://opendata.infrabel.be/explore/dataset/stiptheid-gegevens-maandelijksebestanden/",
           "date": date, "train": train, "points": mine, "fleet": fleet}
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"infrabel_{date}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(p, len(mine), "points;", {t: f["points"] for t, f in fleet.items()})


if __name__ == "__main__":
    main(sys.argv[1:])
