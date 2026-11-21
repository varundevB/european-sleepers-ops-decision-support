# Relay — submission notes

## Run it
    uv sync
    export RELAY_DATA=/path/to/Platform1/data        # the ES files; never committed
    uv run python scripts/validate.py                # scan the whole plan + operations log
    uv run python scripts/run_scenario.py friday     # the real Friday, rulebook §11
    uv run python scripts/run_scenario.py night Milan 2026-10-05 "70 004"   # a real night, replayed
    uv run python scripts/run_scenario.py night Milan 2026-10-05 "70 004" --strict
    open build/night_milan_2026-10-05.html           # arrow keys step through; works offline

The public timing file for that night is in the repo (reference/public/infrabel_2026-10-05.json,
Infrabel open data, CC0). To rebuild it from Infrabel's monthly punctuality file:

    python3 -I scripts/public_night.py /path/to/202610.csv 2026-10-05 401

## The screen
One console: clock and countdown to the planned departure, the incident, the six trains, a map,
six checks per action (Rules, Products, Partners, Infrastructure, Time, Money), and the planner's
call (choose, give a reason, arm, confirm). "More info" opens a page per action; there are also
pages for the case file, the drafted documents and the whole-plan scan.

It is a replay, and says so on screen: the ES workbook plus Infrabel's recorded times for
5 October 2026. Nothing on it is a live feed.

## 30-day plan (two lines)
Week 1-2: run Relay beside the planner on every real incident, confirm with ES which rules are hard and fill in the missing prices (shunting moves, Belgian transports), so each option carries a full euro figure.
Week 3-4: write the chosen option back to the planning grid and send the German order to TCS from the tool, with the audit log as the incident record nobody has time to write today.

## What is stated, not measured
- Average fare EUR 100 (EUR 41m / 410,000 passengers, ES briefing slide). No ticket prices in the data.
- A shunting move is priced at €300 and a car taken out at 35 passengers × €100: both are our placeholders (reference/costs.json). Refund exposure uses the log's own late-passenger count where it is filled in. Which rules are hard is not confirmed by ES.
- Options marked PROPOSAL are ours, not ES policy.
- 5 October 2026: the operations log reports a broken sleeper without a full car number. We use 70 004 because the plan cell Milan!I38 marks it "in train, not commercial" that night. The plan shows ES kept the car in the train unsold; Relay does not propose that action, and prices its actions as if the car were still sold.
- The decision is sealed in the browser (sha256, chained after the build's last audit entry) and is not written back to the log file. Documents are drafted only; the tool does not send or receive messages. The planner can mark a message confirmed by hand; that mark lives in the browser session and is not sealed.
- The audit log is a hash-chained file, not a database. A hash chain proves order and integrity, not truth.
- Case types: car defect and the Friday pre-positioning case are built; the rest are marked partly built or not built on the case-file page.
