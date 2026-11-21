# Relay — TODO (one phase at a time, in order)

Legend: [ ] open · [~] in progress · [x] done and checked by the main agent

## Phase 1 — Contracts (main agent)
- [x] 1.1 Repo scaffold, .gitignore (data never committed), config
- [x] 1.2 reference/rules.json — 14 rules, tier + citation
- [x] 1.3 reference/costs.json — cost table + stated assumptions
- [~] 1.4 reference/network.json — lines, stops, exchange points, countries
- [~] 1.5 reference/scenarios/friday.json — the real Friday, rulebook §11
- [~] 1.6 CONTRACTS.md — the data shapes every lane reads and writes

## Phase 2 — Build (five Opus 5.5 agents, one folder each)
- [ ] 2.A Read the files — relay/ingest/ (M1, M2)
- [ ] 2.B Rules — relay/rules/ (M3)
- [ ] 2.C Cascade + options — relay/cascade/, relay/options/ (M4)
- [ ] 2.D Paper trail — relay/docs/, relay/audit.py
- [ ] 2.E Map + replay — views/

## Phase 3 — Integration (main agent)
- [ ] 3.1 Wire lanes: scripts/run_scenario.py -> build/scenario.json -> build/relay_demo.html
- [ ] 3.2 Real Friday end to end on the real grid
- [ ] 3.3 Full test run, fix what breaks

## Phase 4 — Proof
- [ ] 4.1 Replay last year's nights -> the impact number
- [ ] 4.2 Rule toggle (hard <-> soft)

## Phase 5 — Submission
- [ ] 5.1 Screen recording
- [ ] 5.2 Two-line 30-day plan
- [ ] 5.3 Push to the ES org repo (needs the link from the user)

## Phase 6 — Rehearsal
- [ ] 6.1 Two timed run-throughs, 2:30
