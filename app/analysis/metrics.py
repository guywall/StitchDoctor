"""Deterministic metrics for an embroidery pattern.

All lengths are millimetres; pyembroidery stores coordinates in 1/10 mm
units and we convert up front so callers never see raw units.

Honesty notes (per plan v2 §1.5):
- Only some formats (DST, U01, EXP) carry explicit TRIM commands. PES/JEF
  commonly encode trims as long jump sequences, so trim counts on those
  formats are *inferred* and flagged as such.
- Density is measured against an assumed hoop area (settings value) and is
  a heuristic, not a guarantee.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from pyembroidery import (
    COLOR_CHANGE,
    COMMAND_MASK,
    END,
    JUMP,
    NEEDLE_SET,
    SEQUIN_EJECT,
    SEQUIN_MODE,
    STOP,
    STITCH,
    TRIM,
    EmbPattern,
)

from .. import settings

# Commands that move the hoop to a new position with the needle up.
_MOVE_COMMANDS = {JUMP, TRIM, STOP, NEEDLE_SET, COLOR_CHANGE,
                  SEQUIN_MODE, SEQUIN_EJECT}


def _mm(units: float) -> float:
    return units / settings.UNITS_PER_MM


def _is_positioned(cmd: int, x: float, y: float) -> bool:
    """True when a stitch record carries meaningful coordinates.

    Positionless commands (TRIM, COLOR_CHANGE, ...) are stored as [0, 0, cmd]
    and must not update the tracked needle position.
    """
    if cmd in (STITCH, JUMP):
        return True
    return x != 0 or y != 0


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


class Run:
    """A contiguous run of stitches between needle-up moves."""

    __slots__ = ("start_index", "end_index", "points", "color_index")

    def __init__(self, start_index: int, color_index: int) -> None:
        self.start_index = start_index
        self.end_index = start_index
        self.points: List[Tuple[float, float]] = []
        self.color_index = color_index


def extract_runs(pattern: EmbPattern) -> List[Run]:
    """Split the pattern into stitch runs at jumps/trim/color changes."""
    runs: List[Run] = []
    current: Optional[Run] = None
    color_index = 0
    pos: Tuple[float, float] = (0.0, 0.0)

    for idx, s in enumerate(pattern.stitches):
        cmd = s[2] & COMMAND_MASK
        x, y = s[0], s[1]
        if cmd == END:
            break
        if cmd == STITCH:
            if current is None:
                current = Run(idx, color_index)
                runs.append(current)
                current.points.append(pos)
            pt = (_mm(x), _mm(y))
            current.points.append(pt)
            current.end_index = idx
        elif cmd in _MOVE_COMMANDS:
            if cmd == COLOR_CHANGE or cmd == NEEDLE_SET:
                color_index += 1
            if current is not None:
                current = None
        # update last known position only for records carrying coordinates
        # (positionless commands are stored as [0, 0, command])
        if _is_positioned(cmd, x, y):
            pos = (_mm(x), _mm(y))
    return runs


def analyze(pattern: EmbPattern, cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Compute the full metric set for a pattern.

    cfg carries the user-tunable thresholds (settings.effective_cfg());
    defaults come from settings when omitted.
    """
    cfg = dict(cfg or settings.default_cfg())
    stitches = pattern.stitches
    runs = extract_runs(pattern)

    stitch_lengths: List[float] = []
    jump_lengths: List[float] = []
    jumps: List[Dict[str, Any]] = []
    trims = 0
    color_changes = 0
    stops = 0
    travel_mm = 0.0        # total non-stitch (needle-up) travel
    trimmed_travel = 0.0   # portion of travel following an explicit trim
    last_was_trim = False
    total_stitches = 0
    seen_first_stitch = False
    prev_cmd = None  # command of the previous record (for run-start detection)
    micro_count = 0

    pos: Tuple[float, float] = (0.0, 0.0)

    for idx, s in enumerate(stitches):
        cmd = s[2] & COMMAND_MASK
        nxt = (_mm(s[0]), _mm(s[1]))
        if cmd == STITCH:
            total_stitches += 1
            if seen_first_stitch:
                # the first stitch's "length" would be measured from the
                # (0,0) origin — not a real stitch length; skip it.
                # Zero-length entries are genuine duplicate stitches.
                d = _dist(pos, nxt)
                stitch_lengths.append(d)
                # run-start micros are NOT counted: remove_micro_stitches
                # deliberately keeps the first stitch after a needle-up move
                # (it is a real punch point), so the finding must reflect
                # only stitches the fix would actually remove.
                is_run_start = prev_cmd is None or prev_cmd != STITCH
                if d < cfg["micro_mm"] and not is_run_start:
                    micro_count += 1
            seen_first_stitch = True
        elif cmd == JUMP:
            d = _dist(pos, nxt)
            jump_lengths.append(d)
            jumps.append({
                "index": idx,
                "from": [pos[0], pos[1]],
                "to": [nxt[0], nxt[1]],
                "length_mm": round(d, 2),
                "after_trim": last_was_trim,
            })
            travel_mm += d
            trimmed_travel += d if last_was_trim else 0.0
        elif cmd == TRIM:
            trims += 1
        elif cmd == COLOR_CHANGE:
            color_changes += 1
        elif cmd == STOP:
            stops += 1
        elif cmd == END:
            break
        elif cmd in _MOVE_COMMANDS:
            travel_mm += _dist(pos, nxt)
        if _is_positioned(cmd, s[0], s[1]):
            pos = nxt
        last_was_trim = cmd == TRIM
        prev_cmd = cmd

    xs = [s[0] for s in stitches]
    ys = [s[1] for s in stitches]
    extents: Dict[str, float] = {
        "min_x_mm": round(_mm(min(xs)), 2),
        "max_x_mm": round(_mm(max(xs)), 2),
        "min_y_mm": round(_mm(min(ys)), 2),
        "max_y_mm": round(_mm(max(ys)), 2),
    } if stitches else {"min_x_mm": 0, "max_x_mm": 0, "min_y_mm": 0, "max_y_mm": 0}
    extents["width_mm"] = round(extents["max_x_mm"] - extents["min_x_mm"], 2)
    extents["height_mm"] = round(extents["max_y_mm"] - extents["min_y_mm"], 2)

    explicit_trim_formats = {"dst", "u01", "exp"}
    upload_ext = ""  # filled by caller via format_hint
    return {
        "total_stitches": total_stitches,
        "num_runs": len(runs),
        "color_changes": color_changes,
        "stops": stops,
        "trims": trims,
        "jumps": jumps,
        "jump_count": len(jumps),
        "long_jump_count": sum(1 for j in jumps if j["length_mm"] > cfg["long_jump_mm"]),
        "jump_travel_mm": round(sum(j["length_mm"] for j in jumps), 2),
        "total_travel_mm": round(travel_mm, 2),
        "trimmed_travel_mm": round(trimmed_travel, 2),
        "max_stitch_mm": round(max(stitch_lengths), 2) if stitch_lengths else 0.0,
        "min_stitch_mm": round(min(stitch_lengths), 2) if stitch_lengths else 0.0,
        "avg_stitch_mm": round(sum(stitch_lengths) / len(stitch_lengths), 2)
        if stitch_lengths else 0.0,
        "short_stitch_count": sum(1 for l in stitch_lengths
                                  if 0 < l < cfg["short_mm"]),
        "micro_stitch_count": micro_count,  # removable only (run-anchors kept)
        "long_stitch_count": sum(1 for l in stitch_lengths
                                 if l > cfg["long_mm"]),
        "extents": extents,
        "density_hotspots": _density_hotspots(runs, cfg),
        "isolated_runs": _isolated_runs(runs, cfg),
        "explicit_trim_format": False,   # set by caller
        "stitch_lengths": stitch_lengths,
        "runs": runs,
    }


def _density_hotspots(runs: List[Run], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Grid-based stitch density hotspots (heuristic; hoop-relative caveat)."""
    cell = cfg["density_cell_mm"]
    grid: Dict[Tuple[int, int], int] = {}
    for run in runs:
        for x, y in run.points:
            key = (int(x // cell), int(y // cell))
            grid[key] = grid.get(key, 0) + 1
    total_cells = max(len(grid), 1)
    total_stitches = sum(grid.values())
    if total_stitches < cfg["density_min_stitches"]:
        return []
    avg_per_cell = total_stitches / total_cells
    hotspots = []
    for (cx, cy), count in sorted(grid.items(), key=lambda kv: -kv[1]):
        # hotspot = cell with far more stitches than the design average,
        # and above the absolute density threshold (stitches per mm²)
        density = count / (cell * cell)
        if density > cfg["density_per_mm2"] and count > avg_per_cell * 4:
            hotspots.append({
                "x_mm": round((cx + 0.5) * cell, 1),
                "y_mm": round((cy + 0.5) * cell, 1),
                "stitches": count,
                "stitches_per_mm2": round(density, 1),
            })
            if len(hotspots) >= 5:
                break
    return hotspots


def _isolated_runs(runs: List[Run], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Runs whose nearest neighbour run is unusually far away."""
    out = []
    for i, run in enumerate(runs):
        if len(run.points) > 2:
            continue
        cx = sum(p[0] for p in run.points) / len(run.points)
        cy = sum(p[1] for p in run.points) / len(run.points)
        nearest = None
        for j, other in enumerate(runs):
            if j == i:
                continue
            ox = sum(p[0] for p in other.points) / len(other.points)
            oy = sum(p[1] for p in other.points) / len(other.points)
            d = math.hypot(cx - ox, cy - oy)
            if nearest is None or d < nearest:
                nearest = d
        if nearest is not None and nearest > cfg["isolated_mm"]:
            out.append({
                "run_index": i,
                "x_mm": round(cx, 1),
                "y_mm": round(cy, 1),
                "nearest_run_mm": round(nearest, 1),
                "stitches": len(run.points) - 1,
            })
    return out[:10]
