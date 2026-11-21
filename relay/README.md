# Relay — disruption console for the fleet planner

One screen for the moment a car breaks: the incident, what it touches, the possible actions scored on
six checks (Rules, Products, Partners, Infrastructure, Time, Money), the planner's decision, and a
sealed record of it. Every figure on screen carries the workbook cell or rulebook section it came from.

It is a replay, and says so on screen: the ES workbook plus Infrabel's recorded times for
5 October 2026. Nothing on it is a live feed. No language model and no network call is used.

## Run it

    uv sync
    export RELAY_DATA=/path/to/Platform1/data        # the ES files; never committed
    uv run python scripts/validate.py                # scan the whole plan and the operations log
    uv run python scripts/run_scenario.py friday     # the real Friday, rulebook §11
    uv run python scripts/run_scenario.py night Milan 2026-10-05 "70 004"   # a real night, replayed
    open build/night_milan_2026-10-05.html           # arrow keys step through; works offline
    uv run pytest -q                                 # synthetic data only

`build/` holds pages that embed ES workbook content. It is git-ignored and must stay local.

## Layout

| Path | What it holds |
|---|---|
| `relay/ingest/` | Reads the plan grid (codes and fill colours), the order sheets and the operations log |
| `relay/rules/` | Rule checker; rules are data in `reference/rules.json` |
| `relay/cascade/` | Which trains and cars an incident touches |
| `relay/options/` | Builds the candidate actions and scores them |
| `relay/co2.py` | Rough CO2 estimate per action |
| `relay/docs/` | Drafts the German task order and partner messages |
| `relay/audit.py` | Hash-chained decision log |
| `views/` | The single-page console (`template.html`) and its builder |
| `scripts/` | Command-line entry points |
| `reference/` | Rules, prices, CO2 factors, network, the Friday scenario, Infrabel public timings (CC0) |
| `tests/` | Tests on synthetic data |
| `docs/` | Data contracts, build plan and task list |

## Read before trusting a number

[SUBMISSION.md](SUBMISSION.md) lists what is estimated, assumed or not built, and the 30-day plan.
