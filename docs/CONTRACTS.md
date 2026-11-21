# Contracts

Read `relay/model.py` first: those dataclasses are the only shapes that cross a lane boundary.
Reference data is in `reference/` (rules, costs, network, scenarios). Never edit another lane's folder,
`relay/model.py`, `relay/config.py` or `reference/` — if a contract is wrong, say so in your report.

## Ground rules (every lane)
- Python 3.11+, standard library + openpyxl only. Run with `uv run ...` from `~/relay`. Tests: `uv run pytest tests/<your file>`.
- The challenge data is confidential. Read it from `relay.config.FLEET_XLSX` / `WEEKINFO_XLSX`
  (`/tmp/nd-platform1/Platform1-main/data/`). Never copy it, a slice of it, or a real car-by-date
  table into this repo. Tests use small synthetic data; tests that need the real files `pytest.skip`
  when the file is absent.
- Never guess. A value that cannot be read becomes `unreadable` / `unknown` / `None` and is reported.
  `unknown` is not `ok`. A clean cell does not mean a car is fine (Legend: "Red = car is broken — not always marked").
- Never match place names by similarity. Use an explicit alias table; anything not in it is a `note`.
- Every fact that reaches a screen or a document carries a `Src` or a rulebook citation.
- No network calls. No LLM calls. No new dependencies.
- Plain code, short docstrings, no speculative abstractions.

## Lane A — `relay/ingest/` (+ `tests/test_ingest.py`)
```python
# relay/ingest/__init__.py re-exports:
@dataclass
class Plan:
    cells: list[Cell]
    cars: dict[str, Car]
    rejects: list[dict]        # {"sheet","cell","raw","reason"} — everything that could not be typed
    legend: dict[str, str]     # colour key -> meaning
def load_plan(xlsx=FLEET_XLSX) -> Plan                     # cached per path
def where_is(plan, car: str, date: str) -> list[Cell]      # M2; one Cell per sheet the car appears in
def composition(plan, line: str, date: str) -> list[Cell]  # M2; cells in that train that night:
                                                           # kind in in_train | shunt_in | shunt_out, grid column order
def load_ops(xlsx=WEEKINFO_XLSX) -> list[OpsNight]         # Saturday weekly-average rows dropped
def load_orders(xlsx=FLEET_XLSX) -> dict                   # best effort; see below
```
Grid facts: sheets `Prag`, `Paris`, `Milan`; row 1 owner, row 2 type, row 3 car id; dates in column B from
row 4; car columns G..BX (7..76). Colour is hex or theme+tint — compare the whole key
(see `/tmp/nd-platform1/Platform1-main/starter/grid_to_long.py`). Legend colours are `Legend!A2:A12`.
Codes seen in the data: `1 2 3 4 9 0 -1 -2 X x rev Ddl ddl Wusti Chiasso UHD XD milaan Zot URD Paris UFD
bedbug prg ELEN 452 , Praag wheels Crails grafitti HU =-1 1.30416666666667 ...`.
Formula columns (F, BY..CF) and the `Check` sheet are not trusted and not read.
Order sheets (`Order Prag`, `Order Prs`, `Order Mln`) are free-form: return what can be read with a
`Src` per item and leave the rest; do not force a schema onto them.
CLI: `uv run python -m relay.ingest where "71 455" 2026-10-02` and `... train Paris 2026-10-02`.

## Lane B — `relay/rules/` (+ `tests/test_rules.py`)
```python
def load_rules(path=REFERENCE/"rules.json", overrides: dict[str,str] | None = None) -> dict  # overrides: rule_id -> tier
def check_train(cells: list[Cell], line: str, date: str, *, rules=None, network=None,
                order: list[dict] | None = None) -> list[Finding]
    # cells = composition(plan, line, date). order = optional real car order with door ends / HV state;
    # door_orientation and high_voltage_rear return status "unknown" when it is absent.
def check_day(plan, date: str, *, rules=None, network=None) -> list[Finding]   # all three trains + double_planned
def check_steps(steps: list[Step], context: dict, *, rules=None, network=None) -> list[Finding]
    # judge a proposed option. context = {"cars": {car: {"owner","type"}}, "trains": {num: {"line"}},
    #                                     "has_pax": set[str] of cars with passengers on board (may be empty)}
    # e.g. beistellen an EE car onto a Milan train -> approval_country violation.
```
One function per rule, one `Finding` per broken thing, `ok` findings only when asked (`include_ok=True`).
A soft rule gives `warning`; a hard rule gives `violation`; a rule that lacks data gives `unknown`.
Length limit for a train = the smallest limit among the countries on its line; countries without a
limit in the rulebook (NL, CZ) do not constrain.
CLI: `uv run python -m relay.rules day 2027-01-01`.

## Lane C — `relay/cascade/`, `relay/options/` (+ `tests/test_cascade.py`, `tests/test_options.py`)
```python
# relay/cascade
def impact_of(incident: dict, scenario: dict | None = None, *, plan=None, network=None) -> Impact
    # incident kinds: car_available | car_broken | car_no_hv | train_late
    # edges only where a file or the rulebook proves the link; each edge carries why + cite.
# relay/options
def options_for(incident: dict, scenario: dict | None = None, *, plan=None, rules=None,
                network=None, costs=None, check=None) -> list[Option]
    # check = relay.rules.check_steps (injected so this lane tests without lane B).
    # Scores every option on the six AXES, sets eur / shunting_moves / extra_transports / delay_min,
    # sets refused + refused_by from blocking findings, ranks the rest (rank 1 = best).
```
The Friday (`reference/scenarios/friday.json`) must reproduce the rulebook: the option ES sent ranks 1;
rejected (a) and (b) rank below it because of extra transports and shunting moves; the count of
moves/transports per option is derived from its steps, not typed in.
Generic incident (`car_broken` on a real date) generates options from the levers ES has —
swap in an approved reserve (code X / parked), run short (downgrade or cancel the car), detour,
extra transport — plus levers of ours marked `proposed=True`. An EE or GFF-309 swap into the Milan
train must come out refused, with the citation.
Money: only `reference/costs.json`. Shunting has no euro price: count moves. Refund estimate =
pax affected × avg fare × refund share, and it must say it rests on the stated €100 assumption.

## Lane D — `relay/docs/`, `relay/audit.py` (+ `tests/test_docs.py`, `tests/test_audit.py`)
```python
def task_order_de(option: Option, scenario: dict, *, network=None) -> str      # Auftrag an TCS, German, per train number
def partner_messages(option: Option, scenario: dict, impact: Impact | None = None) -> list[dict]  # {"to","subject","body"}
def plan_diff(before: list[Cell], after: list[Cell]) -> list[dict]             # {"line","date","car","before","after","src"}
def decision_record(incident, option, options, user: str, at: str) -> dict     # what was chosen, what was refused and why
# relay/audit.py
def append(path, entry: dict) -> str     # returns the entry hash; append-only JSONL, each line chained to the previous hash
def verify(path) -> tuple[bool, str]     # (ok, message). A chain proves order and integrity, not truth.
```
Task order uses the rulebook's verbs: abholen, abstellen, beistellen. Grouped per train number.
Templates only. Every car number in a document must come from the option's steps.

## Lane E — `views/` (+ `tests/test_view.py`)
`views/build.py`: `build(scenario_json_path, out_html_path)` writes ONE self-contained HTML file
(inline CSS/JS/SVG, no network, no CDN, no web fonts loaded) from `views/template.html` + the data.
Input shape (`build/scenario.json`, written by the main agent's integration step):
```json
{"title": str, "subtitle": str, "generated_at": str,
 "network": <reference/network.json>,
 "incident": {"id","kind","car","place","note","cite"},
 "cars": [{"car","owner","type","start","note"}],
 "impact": {"nodes":[ImpactNode], "edges":[ImpactEdge]},
 "options": [Option],                      # to_dict form, sorted: ranked first, refused last
 "chosen": "<option id>",
 "documents": {"task_order_de": str, "messages": [{"to","subject","body"}], "audit": {"hash","prev","at","user"}},
 "findings": [Finding],                    # the rule-checker result for the plan
 "validation": {"headline": str, "facts": [str]} | null,
 "assumptions": [str]}
```
Lane E writes its own `views/sample_scenario.json` in this shape, built from
`reference/scenarios/friday.json`, to develop against. Sample numbers must be marked as sample.

## Design (Lane E; any HTML anywhere)
Map stage on aubergine `#40002c`; routes lavender `#cdc4e1` and light blue `#a6c1dd`; muted purple
`#866286` for secondary text on light; yellow `#fdd663` for the one key number; orange-red `#ff3600`
once per screen, on the choose action only; panels off-white `#f5f7fb` with aubergine text.
Status: broken `#DC2626`, changed `#EA580C`, verified `#235b3e` — never the brand orange-red for "broken".
12–16px radius, pill tags, flat "pressed" buttons (hard `0 4px 0` aubergine offset), no gradients,
no glow, no blurred shadows. Fonts: `"Source Sans 3", system-ui, sans-serif`; car numbers, times and
hashes in `"Source Code Pro", ui-monospace, monospace`. No ES logos or photos. Body text ≥ 16px on
the stage — it is shown on a projector.
