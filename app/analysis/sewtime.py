"""Sew-time estimation helpers shared by findings (server) and the player.

The front-end re-implements the formatters in JS; the constants live in
settings and are also exposed via /api/config so the two never drift.
"""
from __future__ import annotations

from .. import settings


def stitch_seconds(count: float) -> float:
    """Seconds to sew `count` stitches at the nominal machine speed."""
    return count / settings.MACHINE_SPM * 60.0


def trim_seconds(count: float) -> float:
    return count * settings.TRIM_TIME_S


def stop_seconds(count: float) -> float:
    return count * settings.STOP_TIME_S


def split_seconds(count: float) -> float:
    """Added sew time from split_long_stitches inserting extra stitches."""
    return count * settings.EXTRA_STITCH_TIME_S


def fmt_mm(mm: float) -> str:
    if mm >= 10000:
        return f"{mm / 1000:.1f} m"
    return f"{mm:.0f} mm"


def fmt_secs(secs: float) -> str:
    secs = int(round(secs))
    if secs >= 3600:
        return f"{secs // 3600}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"
    return f"{secs // 60}:{secs % 60:02d}"
