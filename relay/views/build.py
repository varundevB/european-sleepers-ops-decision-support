"""Inline a scenario JSON into views/template.html and write one self-contained HTML file.

CLI: uv run python -m views.build views/sample_scenario.json build/relay_demo.html
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")
TOKEN = "/*__RELAY_DATA__*/"

REQUIRED = ("title", "network", "incident", "cars", "impact", "options", "chosen", "documents")
OPTIONAL = {"subtitle": "", "generated_at": "", "findings": [], "validation": None, "assumptions": [],
            "live": None, "case_types": [], "user": ""}


class ScenarioError(ValueError):
    """The scenario JSON does not have the shape in docs/CONTRACTS.md, Lane E."""


def _need(obj: dict, keys, where: str) -> None:
    if not isinstance(obj, dict):
        raise ScenarioError(f"{where} must be an object, got {type(obj).__name__}")
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ScenarioError(f"{where} is missing required key(s): {', '.join(missing)}")


def check(data: dict) -> dict:
    """Validate the shape; fill optional keys with empty values. Raises ScenarioError."""
    _need(data, REQUIRED, "scenario")
    _need(data["network"], ("places", "lines"), "network")
    _need(data["incident"], ("place", "note"), "incident")
    _need(data["impact"], ("nodes", "edges"), "impact")
    if not isinstance(data["options"], list) or not data["options"]:
        raise ScenarioError("options must be a non-empty list")
    for i, opt in enumerate(data["options"]):
        _need(opt, ("id", "title"), f"options[{i}]")
    ids = [o["id"] for o in data["options"]]
    if len(set(ids)) != len(ids):
        raise ScenarioError(f"option ids are not unique: {ids}")
    if data["chosen"] not in ids:
        raise ScenarioError(f"chosen {data['chosen']!r} is not an option id ({ids})")
    for k, default in OPTIONAL.items():
        data.setdefault(k, default)
    return data


def render(data: dict) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(TOKEN) != 1:
        raise ScenarioError(f"template must contain {TOKEN} exactly once")
    blob = json.dumps(data, ensure_ascii=False)
    # Safe inside <script>: no "</script>", no HTML comment openers.
    blob = blob.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return template.replace(TOKEN, blob)


def build(scenario_json_path, out_html_path) -> Path:
    """Read the scenario, check it, write one HTML file. Returns the output path."""
    src = Path(scenario_json_path)
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ScenarioError(f"{src}: not valid JSON: {e}") from e
    html = render(check(data))
    out = Path(out_html_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m views.build <scenario.json> <out.html>", file=sys.stderr)
        return 2
    try:
        out = build(args[0], args[1])
    except ScenarioError as e:
        print(f"build failed: {e}", file=sys.stderr)
        return 1
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
