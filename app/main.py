"""Stitch Doctor — embroidery analysis & optimisation web app.

Upload → deterministic analysis → visual report → selectable reversible
fixes (versioned pattern JSON) → live preview → export.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import pyembroidery
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pyembroidery import EmbPattern

from . import settings
from .analysis import findings as findings_mod
from .llm import explain as llm_explain
from .loader import load_pattern, pattern_to_bytes
from .render.geometry import geometry
from .store import (append_version, create_pattern, drop_versions_above,
                    latest_version, list_versions, load_version,
                    pattern_exists, prune_expired, read_meta,
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


def _summary(pattern: EmbPattern, ext: str) -> Dict[str, Any]:
    result = findings_mod.analyze(pattern, upload_ext=ext)
    return {
        "metrics": {k: v for k, v in result.items()
                    if k not in ("runs", "stitch_lengths", "jumps", "findings")},
        "findings": result["findings"],
        "jumps": result["jumps"],
    }


# ---------------------------------------------------------------------------
# API

@app.post("/api/upload")
def upload(file: UploadFile = File(...)) -> Dict[str, Any]:
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
    pid = create_pattern(file.filename, ext, data, pattern)
    return {
        "pattern_id": pid,
        "version": 1,
        "versions": [1],
        "filename": read_meta(pid)["filename"],
        **_summary(load_version(pid, 1), ext),
    }


@app.get("/api/pattern/{pid}")
def get_pattern(pid: str, version: Optional[int] = None) -> Dict[str, Any]:
    pattern = _get_pattern(pid, version)
    ext = read_meta(pid)["upload_ext"]
    return {
        "pattern_id": pid,
        "version": version if version is not None else latest_version(pid),
        "versions": list_versions(pid),
        "filename": read_meta(pid)["filename"],
        **_summary(pattern, ext),
    }


@app.get("/api/geometry/{pid}")
def get_geometry(pid: str, version: Optional[int] = None) -> Dict[str, Any]:
    pattern = _get_pattern(pid, version)
    return geometry(pattern)


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
        **_summary(load_version(pid, latest), ext),
    }


@app.get("/api/compare/{pid}")
def compare(pid: str) -> Dict[str, Any]:
    """Original (v1) vs latest working version — in-memory only, never re-read
    exported machine files (plan v2 §1.4)."""
    if not pattern_exists(pid):
        raise HTTPException(404, "Unknown pattern id")
    ext = read_meta(pid)["upload_ext"]
    original = load_version(pid, 1)
    latest = load_version(pid, latest_version(pid))
    return {
        "original": {"geometry": geometry(original),
                     **_summary(original, ext)},
        "proposed": {"geometry": geometry(latest),
                     **_summary(latest, ext)},
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
    result = _summary(pattern, read_meta(pid)["upload_ext"])
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
