"""Turn raw metrics into user-facing findings.

Each finding carries:
- id: stable identifier (also the op id the UI offers)
- severity: info | warn | high
- title / detail: human-readable summary
- locations: indices / coordinates so the canvas can highlight
- caveats: honesty notes (inferred trims, hoop assumption, round-trip loss)
"""
from __future__ import annotations

from typing import Any, Dict, List

from .. import settings
from . import metrics as _metrics


def analyze(pattern, upload_ext: str = "", cfg: dict | None = None) -> Dict[str, Any]:
    cfg = dict(cfg or settings.default_cfg())
    raw = _metrics.analyze(pattern, cfg)
    raw["explicit_trim_format"] = upload_ext.lower().lstrip(".") in ("dst", "u01", "exp")
    findings: List[Dict[str, Any]] = []

    if raw["micro_stitch_count"]:
        findings.append({
            "id": "remove_micro_stitches",
            "op": "remove_micro_stitches",
            "severity": "info",
            "title": f"{raw['micro_stitch_count']} redundant micro-stitches",
            "detail": (
                f"Stitches shorter than {cfg['micro_mm']} mm add thread breaks "
                "and machine time with no visible effect."
            ),
            "locations": _short_stitch_locations(pattern, raw, cfg["micro_mm"]),
            "caveats": [],
            "estimated_impact": "none (visual)",
        })

    if raw["short_stitch_count"]:
        findings.append({
            "id": "flag_short_stitches",
            "op": None,  # advisory only — no safe automatic fix
            "severity": "warn",
            "title": f"{raw['short_stitch_count']} abnormally short stitches",
            "detail": (
                f"Stitches under {cfg['short_mm']} mm can cause thread "
                "breaks or puckering. Consider the machine's minimum-stitch setting."
            ),
            "locations": _short_stitch_locations(pattern, raw, cfg["short_mm"]),
            "caveats": ["Short stitches may be intentional (detail fills, outlines)."],
            "estimated_impact": "low",
        })

    long_jumps = [j for j in raw["jumps"] if j["length_mm"] > cfg["long_jump_mm"]]
    no_trim = [j for j in long_jumps if not j["after_trim"] and not j.get("trimmed")]
    if no_trim:
        worst = max(j["length_mm"] for j in no_trim)
        findings.append({
            "id": "add_trims",
            "op": "add_trims",
            "severity": "warn",
            "title": f"{len(no_trim)} long jumps without trims (longest {worst:.1f} mm)",
            "detail": (
                "Jumps this long leave visible thread across the front "
                "unless the machine trims before travelling."
            ),
            "locations": [
                {"type": "jump", "index": j["index"], "from": j["from"], "to": j["to"]}
                for j in no_trim
            ],
            "caveats": (
                ["Trim counts are inferred from jump sequences for this format "
                 "(PES/JEF encode trims as jumps)."]
                if not raw["explicit_trim_format"] else []
            ),
            "estimated_impact": "visible cleanup on front",
        })

    if raw["long_stitch_count"]:
        findings.append({
            "id": "split_long_stitches",
            "op": "split_long_stitches",
            "severity": "warn",
            "title": f"{raw['long_stitch_count']} very long stitches "
                     f"(longest {raw['max_stitch_mm']:.1f} mm)",
            "detail": (
                f"Stitches over {cfg['long_mm']} mm may snag or fail "
                "on machines with stitch-length limits."
            ),
            "locations": _long_stitch_locations(pattern, raw, cfg["long_mm"]),
            "caveats": [],
            "estimated_impact": "low (durability)",
        })

    if raw["density_hotspots"]:
        findings.append({
            "id": "flag_density",
            "op": None,  # advisory only
            "severity": "info",
            "title": f"{len(raw['density_hotspots'])} dense regions",
            "detail": (
                "Areas where stitch density far exceeds the design average — "
                "prone to stiff, puckered fabric."
            ),
            "locations": [
                {"type": "density", "x": h["x_mm"], "y": h["y_mm"],
                 "stitches_per_mm2": h["stitches_per_mm2"]}
                for h in raw["density_hotspots"]
            ],
            "caveats": [
                "Density assumes a hoop area of "
                f"{cfg['assumed_hoop_mm']} mm and grid sampling; treat as a hint, not a measurement."
            ],
            "estimated_impact": "fabric feel / puckering",
        })

    if raw["isolated_runs"]:
        findings.append({
            "id": "flag_isolated",
            "op": "remove_isolated_stitches",
            "severity": "info",
            "title": f"{len(raw['isolated_runs'])} isolated stitches",
            "detail": "Single stitches far from any other run — likely digitising artefacts.",
            "locations": [
                {"type": "run", "run_index": r["run_index"],
                 "x": r["x_mm"], "y": r["y_mm"]}
                for r in raw["isolated_runs"]
            ],
            "caveats": ["Some designs use intentional tie-off stitches."],
            "estimated_impact": "tiny visible dots",
        })

    travel = raw["jump_travel_mm"]
    if raw["jump_count"] and travel > 0:
        findings.append({
            "id": "reroute_travel",
            "op": None,  # needs an interactive block-order picker; not auto-fixable
            "severity": "info",
            "title": f"Non-stitch travel: {travel:.0f} mm over {raw['jump_count']} jumps",
            "detail": "Reordering colour blocks can reduce total needle-up travel.",
            "locations": [
                {"type": "jump", "index": j["index"], "from": j["from"], "to": j["to"]}
                for j in raw["jumps"][:50]
            ],
            "caveats": [
                "Reordering can change which colour sits on top; review the preview carefully."
            ],
            "estimated_impact": "machine time only",
        })

    if raw["color_changes"] == 0 and raw["stops"] == 0 and raw["num_runs"] > 1:
        findings.append({
            "id": "flag_monochrome",
            "op": None,
            "severity": "info",
            "title": "Single-colour design with multiple runs",
            "detail": "No colour changes found; runs are joined by jumps/trims only.",
            "locations": [],
            "caveats": [],
            "estimated_impact": "none",
        })

    raw["findings"] = findings
    return raw


# --- location helpers --------------------------------------------------------

def _short_stitch_locations(pattern, raw: Dict[str, Any], mm: float) -> List[Dict[str, Any]]:
    """Stitch indices + endpoints of stitches shorter than mm (incl. 0-length)."""
    return _stitch_length_locations(pattern, lambda d: d < mm)


def _long_stitch_locations(pattern, raw: Dict[str, Any], max_mm: float) -> List[Dict[str, Any]]:
    """Stitch indices + endpoints of stitches longer than max_mm."""
    return _stitch_length_locations(
        pattern, lambda d: d > max_mm)


def _stitch_length_locations(pattern, predicate) -> List[Dict[str, Any]]:
    import math

    from pyembroidery import COMMAND_MASK, STITCH

    out: List[Dict[str, Any]] = []
    pos = (0.0, 0.0)
    scale = settings.UNITS_PER_MM
    seen_first_stitch = False
    for idx, s in enumerate(pattern.stitches):
        cmd = s[2] & COMMAND_MASK
        nxt = (s[0] / scale, s[1] / scale)
        if cmd == STITCH:
            if seen_first_stitch:
                # skip the first stitch: its "length" from the (0,0) origin
                # is not a real stitch length
                d = math.hypot(nxt[0] - pos[0], nxt[1] - pos[1])
                if predicate(d):
                    out.append({
                        "type": "stitch",
                        "index": idx,
                        "from": [round(pos[0], 2), round(pos[1], 2)],
                        "to": [round(nxt[0], 2), round(nxt[1], 2)],
                        "length_mm": round(d, 2),
                    })
                    if len(out) >= 200:
                        break
            seen_first_stitch = True
        pos = nxt
    return out
