# Relay — build plan

Written 2026-11-21 14:20 CET. Status: **not started, waiting for go.**

## What it is
An incident desk for the European Sleeper fleet. One incident comes in; Relay finds everything it
touches across the three lines, proposes fixes, scores each on Rules / Products / Partners /
Infrastructure / Time / Money, and writes the chosen decision down: plan diff, German task order,
partner messages, audit entry.

Demo centre: the real Friday from rulebook §11 (the brief says the jury will ask for it).
Second beat: the night of 2026-01-09 from their own operations log.

## Where it lives
Local git at `~/relay`, no remote, until the European Sleeper org repo link is known.
The challenge data is never committed and never goes into the public nightdesk repo.

## Who builds what
Contracts and integration: the main agent. Five Opus 5.5 medium subagents in parallel, one folder each.

| Lane | Folder | Delivers |
|---|---|---|
| Main, first | `reference/` | stations, cars, rules with strictness tier, costs, shared JSON shapes |
| A Read the files | `relay/ingest/` | grid, Legend, Order sheets, Weekinfo as clean tables + messy-data tests (M1, M2) |
| B Rules | `relay/rules/` | hard / soft / proposed rules, each with citation and tests (M3) |
| C Cascade + options | `relay/cascade/`, `relay/options/` | impact set, fixes, six-point score in euros (M4) |
| D Paper trail | `relay/docs/`, `relay/audit.py` | plan diff, German task order, partner messages, audit log |
| E Map + replay | `views/` | one offline HTML page: three lines, cascade animation, option bars |

## Rules are data, not a cage
- Hard: cannot be waived on the night; the option is refused with its citation.
- Soft: allowed at a price; the tool shows the cost.
- Proposed: our own rules and levers, always labelled as ours.
Which rulebook rules are truly hard is still to be confirmed with European Sleeper.

## Design (every lane)
Shaun's design system.
- Map and replay page: `design-system/DESIGN.md` — aubergine `#40002c` stage, lavender `#cdc4e1`
  and light blue `#a6c1dd` routes, yellow `#fdd663` for the one key number, orange-red `#ff3600`
  once per screen on the choose action, off-white `#f5f7fb` panels, 12–16px radius, no gradients,
  glow or blurred shadows.
- Status: broken `#DC2626`, changed `#EA580C`, verified `#235b3e`.
- Fonts: Source Sans 3, Source Code Pro for car numbers, times and hashes. No ES logos or photos.
- Desk screens: `design-system/nightdesk/MASTER.md`, "What the desk uses today".

## Timeline
| Time | Step |
|---|---|
| 14:20–14:40 | Contracts and reference files |
| 14:40–15:50 | Five lanes build in parallel |
| 15:50–16:30 | Integrate; real Friday end to end |
| 16:30–16:50 | Replay last year's nights for the impact number; rule toggle |
| 16:50–17:15 | Screen recording; two-line 30-day plan for BuilderBase |
| 17:15–17:40 | Two timed rehearsals |
| 17:40 | Live demo, 2–3 minutes, no slides |

## Cut order if late
1. Rule toggle and invented levers
2. Partner messages (keep the German task order)
3. January night replay (keep the Friday)
4. Animated map falls back to a still map

Never cut: rule checker, Friday replay, cheapest-fix answer.

## Done means
- Every figure on screen carries file, sheet, cell and date.
- No car number is shown that is not in the grid for that date.
- Each rule has a passing and a failing test and its citation.
- At least one option on the demo night is refused, visibly, with the rule that refused it.
- "Broken" is never inferred from a clean cell.
- The impact number is the replay output, not a claim.

## Still needed
- Go.
- The European Sleeper org repo link.
- The BuilderBase deadline time.
