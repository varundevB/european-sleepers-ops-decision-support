# Relay — TODO (one phase at a time, in order)

Legend: [ ] open · [~] in progress · [x] done and checked by the main agent

## Phase 1 — Contracts (main agent)
- [x] 1.1 Repo scaffold, .gitignore (data never committed), config
- [x] 1.2 reference/rules.json — 14 rules, tier + citation
- [x] 1.3 reference/costs.json — cost table + stated assumptions
- [x] 1.4 reference/network.json — lines, stops, exchange points, countries
- [x] 1.5 reference/scenarios/friday.json — the real Friday, rulebook §11
- [x] 1.6 CONTRACTS.md — the data shapes every lane reads and writes

## Phase 2 — Build (five Opus 5.5 agents, one folder each)
- [x] 2.A Read the files — relay/ingest/ (M1, M2)
- [x] 2.B Rules — relay/rules/ (M3)
- [x] 2.C Cascade + options — relay/cascade/, relay/options/ (M4)
- [x] 2.D Paper trail — relay/docs/, relay/audit.py
- [x] 2.E Map + replay — views/

## Phase 3 — Integration (main agent)
- [x] 3.1 Wire lanes: scripts/run_scenario.py -> build/scenario.json -> build/relay_demo.html
- [x] 3.2 Real Friday end to end on the real grid
- [x] 3.3 Full test run, fix what breaks

## Phase 4 — Proof
- [x] 4.1 Replay last year's nights -> the impact number
- [x] 4.2 Rule toggle (hard <-> soft) — `run_scenario.py night ... --relax <rule>` builds a second "what if" page

## Phase 5 — Submission
- [x] 5.1 Screen recording — build/relay_demo.mp4 (silent backup, not committed)
- [x] 5.2 Two-line 30-day plan — SUBMISSION.md
- [ ] 5.3 Push to the ES org repo (needs the link from the user)

## Phase 6 — Rehearsal
- [ ] 6.1 Two timed run-throughs, 2:30

## Phase 7 — after the user's review (15:05)
- [x] 7.1 Every option has a euro total, split "ES cost table" + "our assumptions" (shunting move €300, car out = 35 pax x €100)
- [x] 7.2 Rulebook as baseline: bending a rule = "Needs sign-off", not a refusal; only the impossible is refused; `--strict` shows the old behaviour
- [x] 7.3 Independent data check by GPT 6.2 Sol (Codex) — done; refund figure and product-flag wording fixed
- [ ] 7.4 Shaun's full design system — waiting for the user's upload

## Phase 8 — one console screen, replayed as live (agreed 15:45)
- [x] 8.1 Pick a real October 2026 night that is in the ES plan, the ops log and the Infrabel files
- [x] 8.2 Public-data file for that night: planned + actual times per Belgian point (Infrabel, CC0)
- [x] 8.3 Scenario for that night: incident, options, prices, documents
- [x] 8.4 Console screen in Shaun's look: header + clock + decide-by countdown, incident bar, six-train strip
- [x] 8.5 Map panel, six-check board, planner's call (choose, arm, confirm, recorded), event log, audit footer
- [x] 8.6 Detail pages behind "More info": action, case file, documents, whole-plan scan
- [x] 8.7 Friday case on the same screen (no countdown: the rulebook gives no date)
- [x] 8.8 View every screen and detail page; fix
- [x] 8.9 Re-record the backup video; update SUBMISSION.md
- [ ] 8.10 Two timed run-throughs; push when the ES repo link is known
