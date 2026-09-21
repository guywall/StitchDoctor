"""End-to-end tests: synthetic pattern through analysis, ops, store, export."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pyembroidery
from pyembroidery import (COLOR_CHANGE, END, JUMP, STITCH, TRIM, EmbPattern)

from app.analysis import findings as findings_mod
from app.analysis import metrics as metrics_mod
from app.transforms import ops as ops_mod


def make_pattern() -> EmbPattern:
    """2 blocks with micro-stitches, a long jump without trim, long stitches."""
    p = EmbPattern()
    # block 0: run with a micro stitch (0.2 mm = 2 units)
    p.add_stitch_absolute(STITCH, 0, 0)
    p.add_stitch_absolute(STITCH, 0, 100)     # 10 mm
    p.add_stitch_absolute(STITCH, 2, 100)     # 0.2 mm — micro
    p.add_stitch_absolute(STITCH, 200, 100)   # ~19.8 mm — long
    # long jump without trim (300 units = 30 mm)
    p.add_stitch_absolute(JUMP, 500, 100)
    # block 1
    p.add_stitch_absolute(STITCH, 600, 100)
    p.add_stitch_absolute(STITCH, 600, 200)   # 10 mm
    p.add_command(COLOR_CHANGE)  # hmm, color change after stitching done
    p.add_stitch_absolute(STITCH, 700, 200)
    p.add_command(END)
    return p


@pytest.fixture()
def pattern():
    return make_pattern()


def test_metrics_counts(pattern):
    m = metrics_mod.analyze(pattern)
    assert m["total_stitches"] == 7
    assert m["micro_stitch_count"] >= 1
    assert m["long_stitch_count"] >= 1
    assert m["jump_count"] == 1
    assert m["jump_travel_mm"] == pytest.approx(30.0, abs=0.2)


def test_findings_flag_micro_and_jumps(pattern):
    result = findings_mod.analyze(pattern, upload_ext=".dst")
    ids = [f["id"] for f in result["findings"]]
    assert "remove_micro_stitches" in ids
    assert "add_trims" in ids
    # explicit trim format → no inference caveat on the trims finding
    trims = next(f for f in result["findings"] if f["id"] == "add_trims")
    assert trims["caveats"] == []


def test_findings_add_trim_caveat_for_pes(pattern):
    result = findings_mod.analyze(pattern, upload_ext=".pes")
    trims = next(f for f in result["findings"] if f["id"] == "add_trims")
    assert len(trims["caveats"]) > 0


def test_remove_micro_stitches(pattern):
    out = ops_mod.remove_micro_stitches(pattern)
    m = metrics_mod.analyze(out)
    assert m["micro_stitch_count"] == 0


def test_add_trims(pattern):
    out = ops_mod.add_trims(pattern)
    cmds = [s[2] & 0xFF for s in out.stitches]
    assert cmds.count(TRIM) >= 1


def test_split_long_stitches(pattern):
    out = ops_mod.split_long_stitches(pattern, max_mm=8.0)
    m = metrics_mod.analyze(out)
    assert m["max_stitch_mm"] <= 8.0 + 0.05


def test_geometry_serializes_with_threads(pattern):
    """Regression: EmbThread.hex_color is a METHOD; geometry must not leak a
    bound method into the JSON payload (pydantic 500)."""
    import json

    from app.render.geometry import geometry

    pattern.add_thread("#ff0000")
    geo = geometry(pattern)
    assert all(isinstance(c, str) for c in geo["threads"])
    json.dumps(geo)  # must be JSON-serializable


def test_export_roundtrip(pattern, tmp_path):
    from app.loader import pattern_to_bytes
    dst_bytes = pattern_to_bytes(pattern, "dst")
    path = tmp_path / "t.dst"
    path.write_bytes(dst_bytes)
    reloaded = pyembroidery.read(str(path))
    assert reloaded is not None
    assert len(reloaded.stitches) > 0


def test_store_versions(tmp_path, monkeypatch):
    monkeypatch.setattr("app.settings.PATTERN_ROOT", str(tmp_path))
    from app import store
    p = make_pattern()
    pid = store.create_pattern("test.dst", ".dst", b"xx", p)
    v1 = store.load_version(pid, 1)
    assert len(v1.stitches) == len(p.stitches)
    v2_pattern = ops_mod.add_trims(v1)
    v2 = store.append_version(pid, v2_pattern)
    assert v2 == 2
    assert store.list_versions(pid) == [1, 2]
    store.drop_versions_above(pid, 1)
    assert store.list_versions(pid) == [1]
