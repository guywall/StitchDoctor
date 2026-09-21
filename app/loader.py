"""pyembroidery I/O helpers.

Readers dispatch on file extension, so uploads are parsed through a temp
file with the original suffix. The original upload bytes are never touched.
All pyembroidery work is serialised behind a lock: it is CPU-bound and not
guaranteed thread-safe on first use under concurrent requests.
"""
import os
import tempfile
import threading

import pyembroidery

_io_lock = threading.Lock()


def load_pattern(data: bytes, filename: str) -> pyembroidery.EmbPattern:
    ext = os.path.splitext(filename or "")[1].lower() or ".dst"
    fd, tmp_path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        with _io_lock:
            pattern = pyembroidery.read(tmp_path)
    finally:
        os.unlink(tmp_path)
    if pattern is None:
        raise ValueError("could not parse embroidery file")
    if not pattern.stitches:
        raise ValueError("file contains no stitch data")
    return pattern


def pattern_to_bytes(pattern: pyembroidery.EmbPattern, fmt: str) -> bytes:
    fmt = fmt.lower().lstrip(".")
    fd, tmp_path = tempfile.mkstemp(suffix="." + fmt)
    try:
        os.close(fd)
        with _io_lock:
            pyembroidery.write(pattern, tmp_path)
        with open(tmp_path, "rb") as fh:
            return fh.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
