"""Stitch Doctor — embroidery analysis & optimisation web app.

Upload → deterministic analysis → visual report → selectable reversible
fixes (versioned pattern JSON) → live preview → export.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import pyembroidery
from pyembroidery import COMMAND_MASK, JUMP, STITCH
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pyembroidery import EmbPattern

from . import settings
from .analysis import findings as findings_mod
from .llm import explain as llm_explain
from .loader import load_pattern, pattern_to_bytes
from .render.geometry import DEFAULT_PALETTE, geometry
from .store import (append_version, create_pattern, drop_versions_above,
                    latest_version, list_versions, load_version,
                    pattern_exists, prune_expired, read_meta, update_meta,
                    start_prune_thread)
from .transforms import ops as ops_mod

APP_NAME = settings.APP_NAME
EXPORT_FORMATS = settings.EXPORT_FORMATS

@asynccontextmanager
async def _lifespan(app: FastAPI):
    os.makedirs(settings.PATTERN_ROOT, exist_ok=True)
    prune_expired()
    start_prune_thread()
    yield


app = FastAPI(title=APP_NAME, version="1.0.0", lifespan=_lifespan)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# helpers

def _get_pattern(pattern_id: str, version: Optional[int] = None) -> EmbPattern:
    if not pattern_exists(pattern_id):
        raise HTTPException(404, "Unknown pattern id")
    v = version if version is not None else latest_version(pattern_id)
    try:
        return load_version(pattern_id, v)
    except FileNotFoundError:
        raise HTTPException(404, f"Version v{v} not found")


def _require_upload_ext(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            400, f"Unsupported file type '{ext}'. Allowed: "
                 f"{', '.join(sorted(settings.ALLOWED_EXTENSIONS))}")
    return ext


def _apply_op(pattern: EmbPattern, op_id: str, params: Dict[str, Any]) -> EmbPattern:
    if op_id not in ops_mod.OPS:
        raise HTTPException(400, f"Unknown operation '{op_id}'")
    try:
        return ops_mod.OPS[op_id](pattern, **params)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _cfg_for(pid: str, overrides: Optional[dict] = None) -> dict:
    """Resolve analysis config: request overrides > stored meta > defaults."""
    try:
        stored = read_meta(pid).get("cfg") or {}
    except FileNotFoundError:
        stored = {}
    return settings.effective_cfg(overrides or stored or None)


def _summary(pattern: EmbPattern, ext: str, cfg: dict | None = None) -> Dict[str, Any]:
    result = findings_mod.analyze(pattern, upload_ext=ext, cfg=cfg)
    return {
        "metrics": {k: v for k, v in result.items()
                    if k not in ("runs", "stitch_lengths", "jumps", "findings")},
        "findings": result["findings"],
        "jumps": result["jumps"],
    }


# ---------------------------------------------------------------------------
# API

@app.post("/api/upload")
def upload(file: UploadFile = File(...), cfg: Optional[str] = None) -> Dict[str, Any]:
    ext = _require_upload_ext(file.filename or "")
    data = file.file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (20 MB limit)")
    try:
        pattern = load_pattern(data, file.filename or "")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Could not parse embroidery file: {exc}") from exc
    analysis_cfg = settings.effective_cfg(json.loads(cfg) if cfg else None)
    pid = create_pattern(file.filename, ext, data, pattern)
    update_meta(pid, cfg=analysis_cfg)
    return {
        "pattern_id": pid,
        "version": 1,
        "versions": [1],
        "filename": read_meta(pid)["filename"],
        "cfg": analysis_cfg,
        **_summary(load_version(pid, 1), ext, analysis_cfg),
    }


@app.get("/api/pattern/{pid}")
def get_pattern(pid: str, version: Optional[int] = None,
                cfg: Optional[str] = None) -> Dict[str, Any]:
    pattern = _get_pattern(pid, version)
    ext = read_meta(pid)["upload_ext"]
    analysis_cfg = _cfg_for(pid, json.loads(cfg) if cfg else None)
    return {
        "pattern_id": pid,
        "version": version if version is not None else latest_version(pid),
        "versions": list_versions(pid),
        "filename": read_meta(pid)["filename"],
        "cfg": analysis_cfg,
        **_summary(pattern, ext, analysis_cfg),
    }


@app.get("/api/geometry/{pid}")
def get_geometry(pid: str, version: Optional[int] = None) -> Dict[str, Any]:
    pattern = _get_pattern(pid, version)
    return geometry(pattern)


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    """Analysis setting schema + sew-time model constants for the UI."""
    return {
        "defaults": settings.default_cfg(),
        "bounds": settings.ANALYSIS_SETTING_BOUNDS,
        "machine": {
            "spm": settings.MACHINE_SPM,
            "trim_s": settings.TRIM_TIME_S,
            "stop_s": settings.STOP_TIME_S,
            "extra_stitch_s": settings.EXTRA_STITCH_TIME_S,
        },
    }


@app.get("/api/blocks/{pid}")
def get_blocks(pid: str, version: Optional[int] = None) -> Dict[str, Any]:
    """Per-colour-block summary for the sew-order editor."""
    pattern = _get_pattern(pid, version)
    blocks, _seps = ops_mod._blocks_with_separators(pattern)
    scale = settings.UNITS_PER_MM
    out = []
    pos = (0.0, 0.0)  # mm, in pyembroidery's Y-up space
    seen_first = False
    last_cmd = None
    for bi, block in enumerate(blocks):
        stitches = 0
        travel_in = 0.0
        xs: list = []
        ys: list = []
        first_start = None
        for s in block:
            cmd = s[2] & COMMAND_MASK
            pt = (s[0] / scale, s[1] / scale)
            if cmd == STITCH:
                stitches += 1
                if first_start is None:
                    first_start = pt
                if seen_first:
                    travel_in += ((pt[0] - pos[0]) ** 2 + (pt[1] - pos[1]) ** 2) ** 0.5
                xs.append(pt[0])
                ys.append(pt[1])
                pos = pt
            elif cmd == JUMP:
                if seen_first:
                    travel_in += ((pt[0] - pos[0]) ** 2 + (pt[1] - pos[1]) ** 2) ** 0.5
                pos = pt
            if s[0] or s[1] or cmd in (STITCH, JUMP):
                seen_first = True
            last_cmd = cmd
        bbox = None
        if xs and ys:
            bbox = {"min_x": round(min(xs), 1), "max_x": round(max(xs), 1),
                    "min_y": round(min(ys), 1), "max_y": round(max(ys), 1)}
        out.append({
            "index": bi,
            "stitches": stitches,
            "travel_in_mm": round(travel_in, 1),
            "bbox": bbox,
            "start": list(first_start) if first_start else None,
        })
    threads = []
    for t in pattern.threadlist:
        color = getattr(t, "hex_color", None)
        threads.append(color() if callable(color) else (color or "#808080"))
    return {
        "blocks": out,
        "threads": threads,
        "palette": DEFAULT_PALETTE,
    }


@app.post("/api/verify/{pid}")
def verify(pid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Round-trip check: apply the given ops to v1, export to the requested
    format, re-import the exported bytes, and re-analyse the re-imported
    design. Never touches stored versions — this is pure what-if.

    Body: {"ops": [...], "cfg": {...}, "format": "pes"}
    """
    ops_in = body.get("ops") or []
    fmt = str(body.get("format") or "pes").lower().lstrip(".")
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(400, f"Unsupported format '{fmt}'")
    cfg = _cfg_for(pid, body.get("cfg"))
    pattern = _get_pattern(pid, version=1)
    applied = []
    for item in ops_in:
        if not isinstance(item, dict) or "op" not in item:
            raise HTTPException(400, "each op must be an object with 'op'")
        pattern = _apply_op(pattern, item["op"], item.get("params") or {})
        applied.append(item["op"])
    try:
        exported = pattern_to_bytes(pattern, fmt)
        reimported = load_pattern(exported, f"verify.{fmt}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Round-trip failed: {exc}") from exc
    ext = read_meta(pid)["upload_ext"]
    return {
        "format": fmt,
        "applied_ops": applied,
        "working": _summary(pattern, ext, cfg),
        "reimported": _summary(reimported, f".{fmt}", cfg),
        "stitch_delta": (findings_mod._metrics.analyze(reimported, cfg)["total_stitches"]
                         - findings_mod._metrics.analyze(pattern, cfg)["total_stitches"]),
    }


@app.post("/api/fix/{pid}")
def apply_fix(pid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one op to the latest version and persist as a new version.

    Body: {"op": "add_trims", "params": {...}}
    """
    pattern = _get_pattern(pid)
    try:
        new_pattern = _apply_op(pattern, body["op"], body.get("params") or {})
    except KeyError:
        raise HTTPException(400, "Body must include 'op'")
    new_v = append_version(pid, new_pattern)
    ext = read_meta(pid)["upload_ext"]
    return {
        "pattern_id": pid,
        "version": new_v,
        "versions": list_versions(pid),
        "applied_op": body["op"],
        **_summary(load_version(pid, new_v), ext),
    }


@app.post("/api/rebuild/{pid}")
def rebuild(pid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild the working pattern from v1 by applying an ordered op list.

    Body: {"ops": [{"op": ..., "params": {...}}, ...]}
    This is the source of truth for the UI's per-fix toggles: the result is
    always deterministic from the original + the current selection, so
    unticking one fix can never revert a different one.
    """
    ops_in = body.get("ops") or []
    if not isinstance(ops_in, list) or len(ops_in) > 20:
        raise HTTPException(400, "'ops' must be a list of at most 20 items")
    pattern = _get_pattern(pid, version=1)
    applied = []
    for item in ops_in:
        if not isinstance(item, dict) or "op" not in item:
            raise HTTPException(400, "each op must be an object with 'op'")
        pattern = _apply_op(pattern, item["op"], item.get("params") or {})
        applied.append(item["op"])
    new_v = append_version(pid, pattern)
    ext = read_meta(pid)["upload_ext"]
    if "cfg" in body:
        update_meta(pid, cfg=settings.effective_cfg(body.get("cfg")))
    return {
        "pattern_id": pid,
        "version": new_v,
        "versions": list_versions(pid),
        "applied_ops": applied,
        "cfg": read_meta(pid).get("cfg"),
        **_summary(load_version(pid, new_v), ext, read_meta(pid).get("cfg")),
    }


@app.post("/api/undo/{pid}")
def undo(pid: str) -> Dict[str, Any]:
    """Drop the latest version, restoring the previous one."""
    versions = list_versions(pid)
    if not pattern_exists(pid):
        raise HTTPException(404, "Unknown pattern id")
    if len(versions) < 2:
        raise HTTPException(400, "Nothing to undo")
    drop_versions_above(pid, versions[-2])
    return _pattern_state(pid)


@app.post("/api/reset/{pid}")
def reset(pid: str) -> Dict[str, Any]:
    """Drop everything above v1."""
    if not pattern_exists(pid):
        raise HTTPException(404, "Unknown pattern id")
    if list_versions(pid) <= [1]:
        raise HTTPException(400, "Already at the original version")
    drop_versions_above(pid, 1)
    return _pattern_state(pid)


def _pattern_state(pid: str) -> Dict[str, Any]:
    ext = read_meta(pid)["upload_ext"]
    latest = latest_version(pid)
    return {
        "pattern_id": pid,
        "version": latest,
        "versions": list_versions(pid),
        "cfg": read_meta(pid).get("cfg"),
        **_summary(load_version(pid, latest), ext, read_meta(pid).get("cfg")),
    }


@app.get("/api/compare/{pid}")
def compare(pid: str, version: Optional[int] = None) -> Dict[str, Any]:
    """Original (v1) vs a working version — in-memory only, never re-read
    exported machine files (plan v2 §1.4)."""
    if not pattern_exists(pid):
        raise HTTPException(404, "Unknown pattern id")
    ext = read_meta(pid)["upload_ext"]
    original = load_version(pid, 1)
    latest = load_version(pid, version if version is not None
                          else latest_version(pid))
    cfg = read_meta(pid).get("cfg")
    return {
        "original": {"geometry": geometry(original),
                     **_summary(original, ext, cfg)},
        "proposed": {"geometry": geometry(latest),
                     **_summary(latest, ext, cfg)},
    }


@app.get("/api/export/{pid}")
def export_pattern(pid: str, format: str = "pes") -> Response:
    fmt = format.lower().strip().lstrip(".")
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(400, f"Unsupported format '{format}'. "
                                 f"Allowed: {', '.join(EXPORT_FORMATS)}")
    pattern = _get_pattern(pid)
    try:
        data = pattern_to_bytes(pattern, fmt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Could not write {fmt.upper()}: {exc}") from exc
    stem = Path(read_meta(pid)["filename"]).stem
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{stem}-sd.{fmt}"'},
    )


@app.post("/api/explain/{pid}")
def explain(pid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Optional LLM interpretation. Degrades gracefully when unconfigured."""
    if not llm_explain.available():
        return {"available": False,
                "text": "No LLM provider configured; interpretation disabled."}
    pattern = _get_pattern(pid)
    result = _summary(pattern, read_meta(pid)["upload_ext"], read_meta(pid).get("cfg"))
    finding_id = body.get("finding_id", "")
    finding = next((f for f in result["findings"] if f["id"] == finding_id), None)
    if finding is None:
        raise HTTPException(404, f"Unknown finding '{finding_id}'")
    explained = llm_explain.explain(finding, result["metrics"])
    if explained is None:
        return {"available": False, "text": llm_explain._FALLBACK}
    return {"available": True, **explained}


@app.get("/api/health")
def health() -> Dict[str, Any]:
    try:
        from importlib.metadata import version
        pye_version = version("pyembroidery")
    except Exception:  # noqa: BLE001
        pye_version = "unknown"
    return {
        "status": "ok",
        "app": APP_NAME,
        "pyembroidery": pye_version,
        "llm": settings.LLM_PROVIDER if settings.llm_available() else "off",
    }


# ---------------------------------------------------------------------------
# Web UI (single page, served inline; no build tooling)

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _render_index()


def _render_index() -> str:
    from .web import page
    return page(APP_NAME, EXPORT_FORMATS)


# keep old endpoints working (converted designs, quick previews)
@app.post("/api/convert")
def convert(file: UploadFile = File(...), format: str = "dst") -> Response:
    fmt = format.lower().strip()
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(400, f"Unsupported target format: {format}")
    data = file.file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    try:
        pattern = load_pattern(data, file.filename or "input.dst")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Could not read embroidery file: {exc}") from exc
    try:
        out = pattern_to_bytes(pattern, fmt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Could not write {fmt.upper()}: {exc}") from exc
    stem = Path(file.filename or "design").stem
    return Response(
        content=out,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'},
    )
