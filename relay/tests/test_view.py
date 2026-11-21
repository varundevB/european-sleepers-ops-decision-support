"""Lane E: the walkthrough builds into one offline HTML file and refuses a malformed scenario."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from views.build import ScenarioError, build

SAMPLE = Path(__file__).resolve().parents[1] / "views" / "sample_scenario.json"
SVG_NS = "http://www.w3.org/2000/svg"


@pytest.fixture()
def built(tmp_path):
    out = build(SAMPLE, tmp_path / "relay_demo.html")
    return out, out.read_text(encoding="utf-8")


def test_builds_one_file(built, tmp_path):
    out, html = built
    assert out.is_file()
    assert [p.name for p in tmp_path.iterdir()] == ["relay_demo.html"]
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "/*__RELAY_DATA__*/" not in html


def test_no_remote_resources(built):
    _, html = built
    patterns = [
        r"""\b(?:src|href|action|poster)\s*=\s*["']?\s*(?:https?:)?//""",
        r"""url\(\s*["']?\s*(?:https?:)?//""",
        r"""@import\s+(?:url\()?\s*["']?\s*(?:https?:)?//""",
        r"""\bfetch\s*\(""",
        r"""XMLHttpRequest|WebSocket|EventSource|sendBeacon""",
        r"""<link\b[^>]*\brel\s*=\s*["']?(?:stylesheet|preload|prefetch|dns-prefetch|preconnect)""",
    ]
    for pat in patterns:
        assert not re.search(pat, html, re.I), f"remote resource pattern found: {pat}"
    # The only http(s) text allowed is the SVG namespace identifier (never fetched).
    leftovers = set(re.findall(r"https?://[^\s\"'<>)]+", html)) - {SVG_NS}
    assert not leftovers, f"unexpected URLs in output: {leftovers}"


def test_contains_every_option_id(built):
    _, html = built
    ids = [o["id"] for o in json.loads(SAMPLE.read_text(encoding="utf-8"))["options"]]
    assert len(ids) >= 2
    for oid in ids:
        assert oid in html, oid


def _write_variant(tmp_path, mutate):
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    mutate(data)
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.mark.parametrize("key", ["options", "chosen", "network", "impact", "title"])
def test_missing_required_key_fails_loudly(tmp_path, key):
    bad = _write_variant(tmp_path, lambda d: d.pop(key))
    out = tmp_path / "out.html"
    with pytest.raises(ScenarioError, match=key):
        build(bad, out)
    assert not out.exists()


def test_chosen_must_be_an_option(tmp_path):
    bad = _write_variant(tmp_path, lambda d: d.update(chosen="no_such_option"))
    with pytest.raises(ScenarioError, match="no_such_option"):
        build(bad, tmp_path / "out.html")


def test_cli_exit_code_on_bad_json(tmp_path, capsys):
    from views.build import main

    bad = _write_variant(tmp_path, lambda d: d.pop("options"))
    assert main([str(bad), str(tmp_path / "out.html")]) == 1
    assert "build failed" in capsys.readouterr().err
