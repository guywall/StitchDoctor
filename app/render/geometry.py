"""Pattern → JSON geometry for the canvas renderer.

Coordinates are millimetres (rounded to 2 dp); the client flips Y.
All views (thread preview, stitch paths, problems overlay) derive from this
one structure so the preview can never disagree with the metrics.
"""
from __future__ import annotations

from typing import Any, Dict, List

from pyembroidery import (
    COLOR_CHANGE,
    COMMAND_MASK,
    END,
    JUMP,
    NEEDLE_SET,
    STITCH,
    TRIM,
    EmbPattern,
)

from .. import settings

DEFAULT_PALETTE = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080",
]

_POSITIONLESS = {TRIM, COLOR_CHANGE, NEEDLE_SET}


def geometry(pattern: EmbPattern) -> Dict[str, Any]:
    """Full canvas geometry: segments, colour blocks, extents, trims."""
    segments: List[Dict[str, Any]] = []   # stitch runs and jumps
    trims: List[Dict[str, Any]] = []      # trim marker positions
    blocks: List[Dict[str, Any]] = []     # colour block metadata

    current_block = -1
    color_changes_seen = 0
    pos = (0.0, 0.0)
    scale = settings.UNITS_PER_MM

    current_run: List[List[float]] = []

    def _flush_run() -> None:
        nonlocal current_run
        if len(current_run) > 1:
            segments.append({
                "type": "stitch",
                "points": current_run,
                "color": current_block,
            })
        current_run = []

    def _mm(v: float) -> float:
        return round(v / scale, 2)

    for s in pattern.stitches:
        cmd = s[2] & COMMAND_MASK
        if cmd == END:
            break
        if cmd == STITCH:
            pt = (_mm(s[0]), _mm(s[1]))
            current_run.append([pt[0], pt[1]])
            pos = pt
        elif cmd == JUMP:
            _flush_run()
            nxt = (_mm(s[0]), _mm(s[1]))
            segments.append({
                "type": "jump",
                "points": [[pos[0], pos[1]], [nxt[0], nxt[1]]],
                "color": current_block,
            })
            pos = nxt
        elif cmd == TRIM:
            _flush_run()
            trims.append({"x": pos[0], "y": pos[1]})
        elif cmd in (COLOR_CHANGE, NEEDLE_SET):
            _flush_run()
            if cmd == COLOR_CHANGE or cmd == NEEDLE_SET:
                color_changes_seen += 1
                current_block = color_changes_seen
                blocks.append({"index": color_changes_seen, "command_at_mm":
                               [pos[0], pos[1]]})
        # positionless commands keep pos unchanged
    _flush_run()

    if not segments and not trims:
        return {"empty": True, "segments": [], "trims": [],
                "blocks": [], "extents": None}

    all_pts = [p for seg in segments for p in seg["points"]]
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    extents = {
        "min_x": min(xs), "max_x": max(xs),
        "min_y": min(ys), "max_y": max(ys),
        "width_mm": round(max(xs) - min(xs), 2),
        "height_mm": round(max(ys) - min(ys), 2),
    }
    threads = []
    for t in pattern.threadlist:
        color = getattr(t, "hex_color", None)
        if callable(color):  # EmbThread.hex_color is a method in pyembroidery
            color = color()
        threads.append(color or "#808080")

    return {
        "empty": False,
        "segments": segments,
        "trims": trims,
        "blocks": blocks,
        "extents": extents,
        "threads": threads,
        "palette": DEFAULT_PALETTE,
    }
