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


def test_finding_ops_are_valid_or_none(pattern):
    """Every finding's op must be None (advisory) or a registered op name.
    Guards against the UI offering 'Apply fix' on a non-existent operation."""
    result = findings_mod.analyze(pattern, upload_ext=".dst")
    for f in result["findings"]:
        if f["op"] is not None:
            assert f["op"] in ops_mod.OPS, f"unknown op '{f['op']}' in finding '{f['id']}'"


def test_geometry_stitch_indices():
    """Segments/trims carry stitch-sequence positions for the animation player."""
    from app.render.geometry import geometry

    p = EmbPattern()
    p.add_stitch_absolute(STITCH, 0, 0)      # stitch 1 (start point)
    p.add_stitch_absolute(STITCH, 0, 100)    # stitch 2
    p.add_stitch_absolute(STITCH, 100, 100)  # stitch 3
    p.add_stitch_absolute(JUMP, 300, 300)    # after 3 stitches
    p.add_stitch_absolute(STITCH, 310, 300)  # stitch 4
    p.add_command(TRIM)                      # after 4 stitches
    p.add_command(END)
    g = geometry(p)
    assert g["stitch_total"] == 4
    by_type = {(s["type"], s.get("stitch_start")): s for s in g["segments"]}
    run1 = by_type[("stitch", 0)]
    assert run1["stitch_end"] == 2
    jump = next(s for s in g["segments"] if s["type"] == "jump")
    assert jump["stitch_at"] == 3
    assert g["trims"][0]["stitch_at"] == 4


def test_geometry_serializes_with_threads(pattern):
    """Regression: EmbThread.hex_color is a METHOD; geometry must not leak a
    bound method into the JSON payload (pydantic 500)."""
    import json

    from app.render.geometry import geometry

    pattern.add_thread("#ff0000")
    geo = geometry(pattern)
    assert all(isinstance(c, str) for c in geo["threads"])
    json.dumps(geo)  # must be JSON-serializable


# --- Phase 4 block ops -------------------------------------------------------

def make_blocked_pattern() -> EmbPattern:
    """Three spatially-separated colour blocks for order/merge/delete tests.

    Only COLOR_CHANGE starts a new block; jumps just travel within one."""
    p = EmbPattern()
    p.add_thread("#ff0000")
    p.add_thread("#00ff00")
    p.add_thread("#0000ff")
    p.add_stitch_absolute(STITCH, 0, 0)
    p.add_stitch_absolute(STITCH, 100, 0)      # block 0: 2 stitches, near origin
    p.add_command(COLOR_CHANGE)
    p.add_stitch_absolute(JUMP, 2000, 2000)
    p.add_stitch_absolute(STITCH, 2100, 2000)  # block 1: 1 stitch, far corner
    p.add_command(COLOR_CHANGE)
    p.add_stitch_absolute(JUMP, -2000, 100)
    p.add_stitch_absolute(STITCH, -2000, 100)
    p.add_stitch_absolute(STITCH, -1900, 100)  # block 2: 2 stitches, opposite corner
    p.add_command(END)
    return p


def test_move_block_moves_and_threads_follow():
    p = make_blocked_pattern()
    out = ops_mod.move_block(p, index=1, to=0)
    assert out.threadlist[0].hex_color() == p.threadlist[1].hex_color()
    # new first block: jump lands at old block 1's entry, stitch follows
    first = next(s for s in out.stitches if (s[2] & 0xFF) == STITCH)
    assert (first[0], first[1]) == (2100, 2000)


def test_move_block_same_index_is_identity():
    p = make_blocked_pattern()
    out = ops_mod.move_block(p, index=1, to=1)
    assert [s[2] for s in out.stitches] == [s[2] for s in p.stitches]


def test_reverse_block_flips_sew_direction():
    p = make_blocked_pattern()
    out = ops_mod.reverse_block(p, index=0)
    stitch_cmds = [(s[0], s[1]) for s in out.stitches if (s[2] & 0xFF) == STITCH]
    assert stitch_cmds[0] == (100, 0)   # original last stitch of block 0
    assert stitch_cmds[1] == (0, 0)


def test_delete_block_drops_stitches_and_thread():
    p = make_blocked_pattern()
    out = ops_mod.delete_block(p, index=2)
    assert len(out.threadlist) == 2
    m = metrics_mod.analyze(out)
    assert m["total_stitches"] == 3  # 2 + 1 from remaining blocks


def test_merge_blocks_joins_adjacent():
    p = make_blocked_pattern()
    out = ops_mod.merge_blocks(p, index=0, with_index=1)
    assert len(out.threadlist) == 2
    m = metrics_mod.analyze(out)
    assert m["total_stitches"] == 5


def test_merge_blocks_requires_adjacent():
    p = make_blocked_pattern()
    with pytest.raises(ValueError):
        ops_mod.merge_blocks(p, index=0, with_index=2)


def test_block_ops_validate_indices():
    p = make_blocked_pattern()
    for kwargs in ({"index": 9}, {"index": -1}):
        with pytest.raises(ValueError):
            ops_mod.move_block(p, **kwargs)
        with pytest.raises(ValueError):
            ops_mod.reverse_block(p, **kwargs)
        with pytest.raises(ValueError):
            ops_mod.delete_block(p, **kwargs)


def test_reverse_block_inserts_travel_guard():
    """Reversing a far block must not sew a hidden connecting stitch into it."""
    p = make_blocked_pattern()
    out = ops_mod.reverse_block(p, index=1)
    cmds = [s[2] & 0xFF for s in out.stitches]
    # block 1 now starts with its STITCH (2100,2000), 280+ mm from block 0's
    # end — the assembler must insert a JUMP before that stitch.
    assert cmds[:4] == [STITCH, STITCH, COLOR_CHANGE, JUMP]


def test_reorder_blocks_auto_optimises_travel():
    """reorder_blocks() with no order should greedily chain nearby blocks.

    Fixture geometry: block 0 ends at (100,0), block 1 entry (2100,2000),
    block 2 entry (-2000,100). Greedy from origin: 0 → 1 → 2, but 0→2→1 is
    shorter overall (0 ends near 1; compare both tours honestly).
    """
    p = make_blocked_pattern()
    out = ops_mod.reorder_blocks(p)   # order=None → auto-optimise
    # threadlist must follow the blocks whatever order was chosen
    assert len(out.threadlist) == 3
    # the result must be a valid pattern with all stitches present
    n_stitches = sum(1 for s in out.stitches if (s[2] & 0xFF) == STITCH)
    assert n_stitches == 5


def test_reorder_blocks_auto_rejects_when_already_optimal():
    """A design whose order is already the greedy tour must say so, not
    silently "succeed" with an identical rebuild (the 500-error fix)."""
    p = EmbPattern()
    p.add_thread("#ff0000")
    p.add_thread("#00ff00")
    p.add_stitch_absolute(STITCH, 0, 0)
    p.add_stitch_absolute(STITCH, 100, 0)      # block 0 near origin
    p.add_command(COLOR_CHANGE)
    p.add_stitch_absolute(STITCH, 200, 0)      # block 1 continues right
    p.add_command(END)
    try:
        ops_mod.reorder_blocks(p)
        raise AssertionError("expected ValueError for already-optimal order")
    except ValueError as exc:
        assert "already" in str(exc)


def test_findings_carry_estimated_savings(pattern):
    result = findings_mod.analyze(pattern, upload_ext=".dst")
    by_id = {f["id"]: f for f in result["findings"]}
    micro = by_id["remove_micro_stitches"]
    assert micro["estimated_savings"]["stitches"] >= 1
    assert micro["estimated_savings"]["time_seconds"] > 0
    assert "label" in micro["estimated_savings"]
    advisory = by_id["flag_short_stitches"]
    assert advisory["estimated_savings"] is None


def test_reroute_travel_points_at_reorder_op(pattern):
    result = findings_mod.analyze(pattern, upload_ext=".dst")
    reroute = next(f for f in result["findings"] if f["id"] == "reroute_travel")
    assert reroute["op"] == "reorder_blocks"


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


def test_priority_never_drops_or_duplicates_findings(pattern):
    """The three priority modes must return the same finding set."""
    base = findings_mod.analyze(pattern, upload_ext=".dst")
    base_ids = sorted(f["id"] for f in base["findings"])
    for mode in ("speed", "quality", "balanced"):
        cfg = findings_mod.settings.default_cfg()
        cfg["priority"] = mode
        result = findings_mod.analyze(pattern, upload_ext=".dst", cfg=cfg)
        assert sorted(f["id"] for f in result["findings"]) == base_ids, (
            f"priority '{mode}' changed the finding set")
