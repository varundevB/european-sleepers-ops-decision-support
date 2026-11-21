import json

import pytest

from relay.audit import append, verify


def _log(tmp_path, n=3):
    p = tmp_path / "audit.jsonl"
    hashes = [append(p, {"n": i, "who": "planner"}) for i in range(n)]
    return p, hashes


def test_round_trip(tmp_path):
    p, hashes = _log(tmp_path)
    ok, msg = verify(p)
    assert ok, msg
    assert hashes[-1] in msg
    lines = [json.loads(x) for x in p.read_text().splitlines()]
    assert [r["hash"] for r in lines] == hashes
    assert lines[1]["prev"] == hashes[0] and lines[0]["prev"] == "0" * 64
    assert verify(tmp_path / "none.jsonl")[0]


def test_edited_line(tmp_path):
    p, _ = _log(tmp_path)
    p.write_text(p.read_text().replace('"n":1', '"n":9'))
    ok, msg = verify(p)
    assert not ok and "line 2" in msg and "edited" in msg
    with pytest.raises(ValueError):
        append(p, {"n": 3})


def test_removed_line(tmp_path):
    p, _ = _log(tmp_path)
    lines = p.read_text().splitlines(keepends=True)
    p.write_text(lines[0] + lines[2])
    ok, msg = verify(p)
    assert not ok and "line 2" in msg


def test_reordered_line(tmp_path):
    p, _ = _log(tmp_path)
    lines = p.read_text().splitlines(keepends=True)
    p.write_text(lines[0] + lines[2] + lines[1])
    ok, msg = verify(p)
    assert not ok and "line 2" in msg
