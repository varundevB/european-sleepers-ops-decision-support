# European Sleepers Operations Decision Support

A decision-support tool for operations managers at European Sleepers. The goal is to make day-to-day and planning decisions easier by bringing the relevant variables, constraints, and trade-offs into one clear view.

The tool is at the concept stage. The specific workflows, data sources, and decision criteria are still being defined with the people who will use it.

## What it should help with

- Compare operational options against the factors that matter for a decision.
- Make constraints, assumptions, and trade-offs visible.
- Explore how a recommendation changes when inputs or priorities change.
- Give managers a clear rationale they can review and discuss.

Example factors may include demand, staff and equipment availability, capacity, cost, timing, service impact, and operational risk. The tool should support the factors relevant to each workflow rather than assume one fixed score fits every decision.

## Product principles

- Keep managers in control of the final decision.
- Make inputs and assumptions visible.
- Explain why an option is recommended and what trade-offs it carries.
- Handle missing or uncertain data transparently.
- Protect operational data and use only approved sources.

## Project status

Early planning. No operational decision logic or integrations have been implemented yet.

## Getting started

This repository currently contains the project brief and a minimal Python entry point. There is no runnable product yet.

```bash
python main.py
```

## Planned next steps

1. Identify the first operational decisions the tool should support.
2. Map the variables, hard constraints, and data sources for those decisions.
3. Agree how options should be compared and how uncertainty should be shown.
4. Build a small prototype with representative, non-sensitive sample data.
5. Review it with operations managers and refine the workflow.

## Contributing

Before adding decision logic, document the workflow it supports, its required inputs, and how a manager can understand or challenge its output.
