"""API smoke tests via FastAPI TestClient (in-memory, /data patched to tmp)."""
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


def _make_dst(tmp_path):
    """Build a small DST file with micro-stitches and a long jump."""
    from pyembroidery import EmbPattern, STITCH, JUMP, COLOR_CHANGE, END
    p = EmbPattern()
    p.add_stitch_absolute(STITCH, 0, 0)
    p.add_stitch_absolute(STITCH, 0, 100)
    p.add_stitch_absolute(STITCH, 2, 100)      # micro-stitch
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
    assert comp["original"]["metrics"]["total_stitches"] == 7  # v1 unchanged
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
