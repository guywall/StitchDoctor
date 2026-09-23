"""API smoke tests via FastAPI TestClient (in-memory, /data patched to tmp)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.testclient import TestClient

# Patch the store root BEFORE importing the app.
import app.settings as settings  # noqa: E402

_TMP_DATA = None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    global _TMP_DATA
    monkeypatch.setattr(settings, "PATTERN_ROOT", str(tmp_path / "patterns"))
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def test_blocks_and_compare_endpoints(client, tmp_path):
    """Sew-order data + versioned compare for the studio UI."""
    dst = _make_dst(tmp_path)
    with open(dst, "rb") as fh:
        res = client.post("/api/upload",
                          files={"file": ("test.dst", fh, "application/octet-stream")})
    pid = res.json()["pattern_id"]

    blocks = client.get(f"/api/blocks/{pid}").json()
    assert blocks["blocks"], "block summary missing"
    assert all("stitches" in b and "travel_in_mm" in b for b in blocks["blocks"])
    assert isinstance(blocks["threads"], list)  # DST may carry no thread records

    cmp1 = client.get(f"/api/compare/{pid}").json()
    assert "original" in cmp1 and "proposed" in cmp1
    assert cmp1["original"]["geometry"]["stitch_total"] > 0

    # apply an op, then compare against the explicit version
    # (add_trims is pointless here: the DST encoder caps jumps at 12.1 mm,
    # so no long jumps survive a round-trip — use the micro-stitch fix)
    res = client.post(f"/api/rebuild/{pid}",
                      json={"ops": [{"op": "remove_micro_stitches", "params": {}}]})
    assert res.status_code == 200, res.text
    cmp2 = client.get(f"/api/compare/{pid}?version=1").json()
    assert cmp2["proposed"]["metrics"]["total_stitches"] == \
        cmp1["proposed"]["metrics"]["total_stitches"]


def test_upload_findings_carry_savings(client, tmp_path):
    dst = _make_dst(tmp_path)
    with open(dst, "rb") as fh:
        data = client.post("/api/upload",
                           files={"file": ("test.dst", fh, "application/octet-stream")}).json()
    by_id = {f["id"]: f for f in data["findings"]}
    micro = by_id["remove_micro_stitches"]
    assert micro["estimated_savings"] is not None
    assert micro["estimated_savings"]["stitches"] >= 1
    assert by_id["flag_short_stitches"]["estimated_savings"] is None

    config = client.get("/api/config").json()
    assert config["machine"]["spm"] > 0


def _make_dst(tmp_path):
    """Build a small DST file with micro-stitches and a long jump."""
    from pyembroidery import EmbPattern, STITCH, JUMP, COLOR_CHANGE, END
    p = EmbPattern()
    p.add_stitch_absolute(STITCH, 0, 0)
    p.add_stitch_absolute(STITCH, 0, 100)      # 10 mm
    p.add_stitch_absolute(STITCH, 2, 100)      # 0.2 mm — micro
    p.add_stitch_absolute(STITCH, 17, 100)     # 1.5 mm — short
    p.add_stitch_absolute(STITCH, 200, 100)
    p.add_stitch_absolute(JUMP, 500, 100)      # 30 mm jump
    p.add_stitch_absolute(STITCH, 600, 100)
    p.add_stitch_absolute(STITCH, 600, 200)
    p.add_command(COLOR_CHANGE)
    p.add_stitch_absolute(STITCH, 700, 200)
    p.add_command(END)
    path = tmp_path / "test.dst"
    import pyembroidery
    pyembroidery.write(p, str(path))
    return path


def test_full_flow(client, tmp_path):
    dst = _make_dst(tmp_path)

    # upload + analyse
    with open(dst, "rb") as fh:
        res = client.post("/api/upload",
                          files={"file": ("test.dst", fh, "application/octet-stream")})
    assert res.status_code == 200, res.text
    data = res.json()
    pid = data["pattern_id"]
    assert data["version"] == 1
    finding_ids = [f["id"] for f in data["findings"]]
    assert "remove_micro_stitches" in finding_ids
    # NOTE: no add_trims assertion here — DST's encoder caps jumps at
    # 12.1 mm, so real DST files never contain long jumps. That finding
    # fires for PES/JEF/VP3 uploads (covered by the unit tests).

    # geometry renders
    res = client.get(f"/api/geometry/{pid}")
    assert res.status_code == 200
    geo = res.json()
    assert geo["segments"]
    assert geo["extents"]["width_mm"] > 0

    # apply a fix → new version
    res = client.post(f"/api/fix/{pid}", json={"op": "remove_micro_stitches", "params": {}})
    assert res.status_code == 200, res.text
    assert res.json()["version"] == 2
    assert res.json()["versions"] == [1, 2]
    assert res.json()["metrics"]["micro_stitch_count"] == 0

    # compare — the original side must equal the v1 upload analysis
    res = client.get(f"/api/compare/{pid}")
    assert res.status_code == 200
    comp = res.json()
    assert comp["original"]["metrics"]["total_stitches"] == 8  # v1 unchanged
    assert comp["proposed"]["metrics"]["micro_stitch_count"] == 0

    # undo → back to v1
    res = client.post(f"/api/undo/{pid}")
    assert res.status_code == 200
    assert res.json()["versions"] == [1]

    # apply again, then reset
    client.post(f"/api/fix/{pid}", json={"op": "remove_micro_stitches", "params": {}})
    res = client.post(f"/api/reset/{pid}")
    assert res.json()["versions"] == [1]

    # export
    res = client.get(f"/api/export/{pid}?format=dst")
    assert res.status_code == 200
    assert len(res.content) > 50

    # explain degrades gracefully with no LLM configured
    res = client.post(f"/api/explain/{pid}", json={"finding_id": "add_trims"})
    assert res.status_code == 200
    assert res.json()["available"] is False

    # index page renders
    res = client.get("/")
    assert res.status_code == 200
    assert "Stitch Doctor" in res.text


def test_upload_rejects_bad_ext(client):
    res = client.post("/api/upload",
                      files={"file": ("test.exe", b"MZ junk", "application/x-msdownload")})
    assert res.status_code == 400


def test_config_schema_endpoint(client):
    res = client.get("/api/config")
    assert res.status_code == 200
    body = res.json()
    assert "micro_mm" in body["defaults"]
    assert body["bounds"]["micro_mm"][0] < body["bounds"]["micro_mm"][1]


def test_settings_override_changes_counts(client, tmp_path):
    """Raising the micro threshold must raise the micro-stitch count."""
    dst = _make_dst(tmp_path)
    with open(dst, "rb") as fh:
        base = client.post("/api/upload",
                           files={"file": ("t.dst", fh, "application/octet-stream")}).json()
    with open(dst, "rb") as fh:
        loose = client.post(
            "/api/upload",
            files={"file": ("t.dst", fh, "application/octet-stream")},
            params={"cfg": '{"micro_mm": 1.6}'},
        ).json()
    assert loose["metrics"]["micro_stitch_count"] > base["metrics"]["micro_stitch_count"]
    assert loose["cfg"]["micro_mm"] == 1.6


def test_rebuild_toggle_off_restores(client, tmp_path):
    """rebuild(ops=[]) must produce metrics identical to v1 (true toggle-off)."""
    dst = _make_dst(tmp_path)
    with open(dst, "rb") as fh:
        up = client.post("/api/upload",
                         files={"file": ("t.dst", fh, "application/octet-stream")}).json()
    pid = up["pattern_id"]
    res = client.post(f"/api/rebuild/{pid}", json={"ops": [{"op": "remove_micro_stitches"}]})
    assert res.json()["metrics"]["micro_stitch_count"] == 0
    # untick everything: deterministic rebuild from v1
    res = client.post(f"/api/rebuild/{pid}", json={"ops": []})
    assert res.json()["metrics"]["micro_stitch_count"] == up["metrics"]["micro_stitch_count"]


def test_verify_roundtrip_matches(client, tmp_path):
    """Export → re-import → re-check: working and re-imported metrics must
    agree (or differ by an honest, reported delta)."""
    dst = _make_dst(tmp_path)
    with open(dst, "rb") as fh:
        up = client.post("/api/upload",
                         files={"file": ("t.dst", fh, "application/octet-stream")}).json()
    pid = up["pattern_id"]
    res = client.post(f"/api/verify/{pid}", json={
        "ops": [{"op": "remove_micro_stitches"}],
        "format": "dst",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["format"] == "dst"
    assert body["working"]["metrics"]["micro_stitch_count"] == 0
    # DST round-trips stitches reliably for this design: re-import must not
    # silently differ without the endpoint saying so
    assert "stitch_delta" in body
    w = body["working"]["metrics"]["total_stitches"]
    r = body["reimported"]["metrics"]["total_stitches"]
    assert body["stitch_delta"] == r - w


# ---------------------------------------------------------------------------
# relieve_density: overlap thinning
# relieve_density: overlap thinning with "first block keeps coverage"


def _overlap_pattern():
    """Two fully overlapping blocks (red then blue), rows every 1 mm."""
    from pyembroidery import EmbPattern, STITCH, JUMP, COLOR_CHANGE
    from pyembroidery.EmbThread import EmbThread
    p = EmbPattern()
    for row in range(40):
        x = row
        for k in range(21):
            p.add_stitch_absolute(STITCH, x * 10, k * 10)
        if row < 39:
            p.add_stitch_absolute(JUMP, (x + 1) * 10, 0)
    p.add_command(COLOR_CHANGE)
    for row in range(40):
        x = row
        for k in range(21):
            p.add_stitch_absolute(STITCH, x * 10 + 5, k * 10 + 5)
        if row < 39:
            p.add_stitch_absolute(JUMP, (x + 1) * 10 + 5, 5)
    t1, t2 = EmbThread(), EmbThread()
    t1.set_color(255, 0, 0)
    t2.set_color(0, 0, 255)
    p.threadlist = [t1, t2]
    return p


def test_relieve_density_overlap():
    """First block keeps full coverage; later block thinned to anchors."""
    from pyembroidery import STITCH, COLOR_CHANGE
    from app.transforms.ops import relieve_density
    out = relieve_density(_overlap_pattern(), target_per_mm2=1.0, cell_mm=5)
    blocks = {}
    blk = 0
    for s in out.stitches:
        c = s[2] & 0xFF
        if c == COLOR_CHANGE:
            blk += 1
        elif c == STITCH:
            blocks[blk] = blocks.get(blk, 0) + 1
    assert blocks[0] == 840, "first block must keep every stitch"
    assert 0 < blocks[1] < 840, "later block must be thinned, not erased"
    assert sum(1 for s in out.stitches
               if (s[2] & 0xFF) == COLOR_CHANGE) == 1


def test_relieve_density_single_block_refuses():
    """Single-block density is a digitising style, not overlap — refuse."""
    from pyembroidery import EmbPattern, STITCH, JUMP
    from app.transforms.ops import relieve_density
    p = EmbPattern()
    for row in range(40):
        x = row
        for k in range(21):
            p.add_stitch_absolute(STITCH, x * 10, k * 10)
        if row < 39:
            p.add_stitch_absolute(JUMP, (x + 1) * 10, 0)
    with pytest.raises(ValueError):
        relieve_density(p, target_per_mm2=1.0, cell_mm=5)


def _contrast_pattern():
    """Sparse field with a dense two-block overlap patch (local contrast)."""
    from pyembroidery import EmbPattern, STITCH, JUMP, COLOR_CHANGE
    p = EmbPattern()
    for y in range(0, 60, 8):            # sparse field, block A
        for x in range(0, 60):
            p.add_stitch_absolute(STITCH, x * 10, y * 10)
        if y + 8 < 60:
            p.add_stitch_absolute(JUMP, 0, (y + 8) * 10)
    for y2 in range(0, 40):              # dense patch, block A (0.5 mm grid)
        yy = y2 * 0.5
        for x in range(0, 40):
            p.add_stitch_absolute(STITCH, int(x * 5), int(yy * 10))
        p.add_stitch_absolute(JUMP, 0, int(yy * 10) + 5)
    p.add_command(COLOR_CHANGE)
    for y2 in range(0, 40):              # dense patch, block B (overlaps A)
        yy = y2 * 0.5
        for x in range(0, 40):
            p.add_stitch_absolute(STITCH, int(x * 5) + 3, int(yy * 10) + 3)
        p.add_stitch_absolute(JUMP, 3, int(yy * 10) + 8)
    return p


def test_density_finding_offers_relief_when_triggered():
    """flag_density must carry the relieve_density op (lowered threshold)."""
    from app.analysis import findings
    r = findings.analyze(_contrast_pattern(),
                         cfg={**findings.settings.default_cfg(),
                              "density_per_mm2": 0.5, "density_min_stitches": 100})
    dens = next((f for f in r["findings"] if f["id"] == "flag_density"), None)
    assert dens is not None, "contrast fixture should produce a hotspot"
    assert dens["op"] == "relieve_density"
    assert any("OVERLAP" in c for c in dens["caveats"])


def test_relieve_density_via_api(client, tmp_path):
    """The op applies through the API on a real overlap design."""
    import io
    from app.loader import pattern_to_bytes
    data = pattern_to_bytes(_overlap_pattern(), "pes")
    up = client.post("/api/upload", files={
        "file": ("ov.pes", io.BytesIO(data), "application/octet-stream")}).json()
    res = client.post(f"/api/fix/{up['pattern_id']}",
                      json={"op": "relieve_density",
                            "params": {"target_per_mm2": 1.0, "cell_mm": 5}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["metrics"]["total_stitches"] < up["metrics"]["total_stitches"]


def test_priority_via_api(client, tmp_path):
    """cfg priority flows through the API without 500s or finding drift."""
    dst = _make_dst(tmp_path)
    with open(dst, "rb") as fh:
        up = client.post("/api/upload",
                         files={"file": ("t.dst", fh, "application/octet-stream")}).json()
    pid = up["pattern_id"]
    base_ids = sorted(f["id"] for f in up["findings"])
    r = client.get(f"/api/pattern/{pid}",
                   params={"cfg": json.dumps({"priority": "speed"})})
    assert r.status_code == 200, r.text
    ids = sorted(f["id"] for f in r.json()["findings"])
    assert ids == base_ids
    assert r.json()["findings"][0]["id"] == "reroute_travel" or len(base_ids) <= 1
