# Relay — submission notes

## Run it
    uv sync
    export RELAY_DATA=/path/to/Platform1/data        # the ES files; never committed
    uv run python scripts/validate.py                # scan the whole plan + operations log
    uv run python scripts/run_scenario.py friday     # the real Friday, rulebook §11
    uv run python scripts/run_scenario.py night Milan 2027-01-12 "91 003"
    uv run python scripts/run_scenario.py night Milan 2027-01-12 "91 003" --strict
    open build/friday.html                           # arrow keys step through; works offline

## 30-day plan (two lines)
Week 1-2: run Relay beside the planner on every real incident, confirm with ES which rules are hard and fill in the missing prices (shunting moves, Belgian transports), so each option carries a full euro figure.
Week 3-4: write the chosen option back to the planning grid and send the German order to TCS from the tool, with the audit log as the incident record nobody has time to write today.

## What is stated, not measured
- Average fare EUR 100 (EUR 41m / 410,000 passengers, ES briefing slide). No ticket prices in the data.
- A shunting move is priced at €300 and a car taken out at 35 passengers × €100: both are our placeholders (reference/costs.json). Refund exposure uses the log's own late-passenger count where it is filled in. Which rules are hard is not confirmed by ES.
- Options marked PROPOSAL are ours, not ES policy.
