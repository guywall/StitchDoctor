"""Versioned pattern persistence.

Layout (under STITCHDR_DATA_DIR, mounted as a Docker volume):

    /data/patterns/<id>/upload.<ext>   original upload bytes, never modified
    /data/patterns/<id>/meta.json      {filename, created_at, comment}
    /data/patterns/<id>/v1.json        original pattern (pyembroidery JSON)
    /data/patterns/<id>/v2.json ...    working pattern after each applied op

Version files ARE the undo history: "undo" deletes the highest version,
"reset" deletes everything above v1. No in-process state, no database.
"""
import json
import os
import re
import shutil
import threading
import time
import uuid

from . import settings

_write_lock = threading.Lock()

_VERSION_RE = re.compile(r"^v(\d+)\.json$")


def _pattern_dir(pattern_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", pattern_id):
        raise ValueError("invalid pattern id")
    return os.path.join(settings.PATTERN_ROOT, pattern_id)


def new_pattern_id() -> str:
    return uuid.uuid4().hex


# --- creation ----------------------------------------------------------------

def create_pattern(upload_name: str, upload_ext: str, upload_bytes: bytes,
                   original_pattern) -> str:
    """Persist an uploaded design as v1 and return its id."""
    pid = new_pattern_id()
    pdir = _pattern_dir(pid)
    os.makedirs(pdir, exist_ok=True)
    safe_name = os.path.basename(upload_name or "design")[:120]
    meta = {
        "filename": safe_name,
        "upload_ext": upload_ext,
        "created_at": time.time(),
    }
    _atomic_write_json(pdir, "meta.json", meta)
    with open(os.path.join(pdir, f"upload{upload_ext}"), "wb") as fh:
        fh.write(upload_bytes)
    save_version(pid, 1, original_pattern)
    return pid


# --- version IO --------------------------------------------------------------

def _atomic_write_json(pdir: str, name: str, payload) -> None:
    tmp = os.path.join(pdir, f".tmp-{uuid.uuid4().hex}")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, os.path.join(pdir, name))


def save_version(pattern_id: str, version: int, pattern) -> None:
    """Persist a pattern as vN.json using pyembroidery's lossless JSON."""
    import pyembroidery
    pdir = _pattern_dir(pattern_id)
    tmp = os.path.join(pdir, f".tmp-v{version}.json")
    pyembroidery.write_json(pattern, tmp)
    os.replace(tmp, os.path.join(pdir, f"v{version}.json"))


def load_version(pattern_id: str, version: int):
    import pyembroidery
    path = os.path.join(_pattern_dir(pattern_id), f"v{version}.json")
    return pyembroidery.read_json(path)


def list_versions(pattern_id: str) -> list:
    pdir = _pattern_dir(pattern_id)
    if not os.path.isdir(pdir):
        return []
    out = []
    for name in os.listdir(pdir):
        m = _VERSION_RE.match(name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def latest_version(pattern_id: str) -> int:
    versions = list_versions(pattern_id)
    if not versions:
        raise FileNotFoundError(f"pattern {pattern_id} has no versions")
    return versions[-1]


def append_version(pattern_id: str, pattern, max_versions: int | None = None) -> int:
    """Write pattern as next version; return the new version number."""
    limit = max_versions or settings.MAX_VERSIONS_PER_PATTERN
    with _write_lock:
        versions = list_versions(pattern_id)
        next_v = versions[-1] + 1 if versions else 1
        if next_v > limit:
            raise ValueError(f"version limit ({limit}) reached; reset the pattern")
        save_version(pattern_id, next_v, pattern)
        return next_v


def drop_versions_above(pattern_id: str, keep_version: int) -> list:
    """Undo helper: delete vN.json for all N > keep_version. Returns kept list."""
    pdir = _pattern_dir(pattern_id)
    for v in list_versions(pattern_id):
        if v > keep_version:
            os.unlink(os.path.join(pdir, f"v{v}.json"))
    return list_versions(pattern_id)


# --- meta / upload access ----------------------------------------------------

def read_meta(pattern_id: str) -> dict:
    with open(os.path.join(_pattern_dir(pattern_id), "meta.json"), encoding="utf-8") as fh:
        return json.load(fh)


def read_upload(pattern_id: str) -> tuple:
    """Return (bytes, ext) of the original upload."""
    pdir = _pattern_dir(pattern_id)
    meta = read_meta(pattern_id)
    path = os.path.join(pdir, f"upload{meta['upload_ext']}")
    with open(path, "rb") as fh:
        return fh.read(), meta["upload_ext"]


def pattern_exists(pattern_id: str) -> bool:
    try:
        return os.path.isdir(_pattern_dir(pattern_id)) and bool(list_versions(pattern_id))
    except ValueError:
        return False


# --- housekeeping ------------------------------------------------------------

def prune_expired() -> int:
    """Delete pattern dirs older than the TTL. Returns number removed."""
    cutoff = time.time() - settings.SESSION_TTL_HOURS * 3600
    removed = 0
    try:
        entries = os.listdir(settings.PATTERN_ROOT)
    except FileNotFoundError:
        return 0
    for entry in entries:
        path = os.path.join(settings.PATTERN_ROOT, entry)
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def start_prune_thread(interval_minutes: int = 60):
    import threading

    def _loop() -> None:
        prune_expired()
        timer = threading.Timer(interval_minutes * 60, _loop)
        timer.daemon = True
        timer.start()

    timer = threading.Timer(interval_minutes * 60, _loop)
    timer.daemon = True
    timer.start()
    return timer
