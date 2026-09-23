"""Turn raw metrics into user-facing findings.

Each finding carries:
- id: stable identifier (also the op id the UI offers)
- severity: info | warn | high
- title / detail: human-readable summary
- locations: indices / coordinates so the canvas can highlight
- caveats: honesty notes (inferred trims, hoop assumption, round-trip loss)
- estimated_savings: what applying the fix buys (stitches, trims, seconds) —
  None for advisory findings, a dict for actionable ones.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .. import settings
from . import metrics as _metrics
from . import sewtime


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
            "estimated_savings": {
                "stitches": raw["micro_stitch_count"],
                "time_seconds": round(
                    sewtime.stitch_seconds(raw["micro_stitch_count"]), 1),
                "label": f"−{raw['micro_stitch_count']} stitches · "
                         f"−{sewtime.fmt_secs(sewtime.stitch_seconds(raw['micro_stitch_count']))}",
            },
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
            "estimated_savings": None,
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
            "estimated_savings": {
                "trims": len(no_trim),
                "time_seconds": round(sewtime.trim_seconds(len(no_trim)), 1),
                "label": f"+{len(no_trim)} trims (cleaner front) · "
                         f"+{sewtime.fmt_secs(sewtime.trim_seconds(len(no_trim)))} sew time",
            },
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
            "estimated_savings": {
                "stitches": 0,
                "added_stitches": raw["long_stitch_count"],
                "time_seconds": round(
                    -sewtime.split_seconds(raw["long_stitch_count"]), 1),
                "label": f"+{raw['long_stitch_count']} anchor stitches (durability)",
            },
        })

    if raw["density_hotspots"]:
        findings.append({
            "id": "flag_density",
            "op": "relieve_density",
            "severity": "warn",
            "title": f"{len(raw['density_hotspots'])} dense regions",
            "detail": (
                "Areas where stitch density far exceeds the design average — "
                "prone to stiff, puckered fabric. Where the density comes from "
                "two colour blocks overlapping, the fix thins the later block "
                "so the colours butt up against each other instead of stacking."
            ),
            "locations": [
                {"type": "density", "x": h["x_mm"], "y": h["y_mm"],
                 "stitches_per_mm2": h["stitches_per_mm2"]}
                for h in raw["density_hotspots"]
            ],
            "caveats": [
                "Density assumes a hoop area of "
                f"{cfg['assumed_hoop_mm']} mm and grid sampling; treat as a hint, not a measurement.",
                "Relief only applies where blocks OVERLAP: single-block density "
                "is a digitising style and is left untouched.",
                "Thinning the top colour can let the under-colour show through "
                "— ideal on tone-on-tone, review strong contrasts on the canvas.",
            ],
            "estimated_impact": "fabric feel / puckering",
            "estimated_savings": None,
            "priority_hint": "quality",
        })

    if raw["isolated_runs"]:
        findings.append({
            "id": "remove_isolated_stitches",
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
            "estimated_savings": {
                "stitches": sum(r["stitches"] for r in raw["isolated_runs"]),
                "time_seconds": round(sewtime.stitch_seconds(
                    sum(r["stitches"] for r in raw["isolated_runs"])), 1),
                "label": f"−{sum(r['stitches'] for r in raw['isolated_runs'])} stitches",
            },
        })

    # --- travel efficiency -------------------------------------------------
    te = raw.get("travel_efficiency") or {}
    travel = raw["jump_travel_mm"]
    if raw["jump_count"] and travel > 0:
        # rough upper bound: reordering can at best halve total jump travel
        # (each jump to a fresh location must still happen once); we claim a
        # conservative quarter as "typically recoverable".
        recoverable = round(travel * 0.25)
        # travel sews at roughly machine speed too (needle up, hoop move)
        secs = recoverable / settings.MACHINE_SPM * 60.0 * 0.6  # hoop moves slower
        title = f"Non-stitch travel: {travel:.0f} mm over {raw['jump_count']} jumps"
        detail = (
            "Reordering colour blocks can reduce total needle-up travel. "
            "Use the sew-order panel to drag blocks into a shorter path."
        )
        severity = "info"
        if te.get("warn"):
            severity = "warn"
            title = (f"Inefficient running order — {te['wasted_mm']:.0f} mm "
                     "excess travel")
            detail = (
                f"Needle-up travel is {te['actual_mm']:.0f} mm, but visiting the "
                f"same work nearest-first needs only ~{te['reference_mm']:.0f} mm "
                f"({te['ratio']:.1f}× worse than the reference). " + detail)
        findings.append({
            "id": "reroute_travel",
            "op": "reorder_blocks",
            "severity": severity,
            "title": title,
            "detail": detail,
            "locations": [
                {"type": "jump", "index": j["index"], "from": j["from"], "to": j["to"]}
                for j in raw["jumps"][:50]
            ],
            "caveats": [
                "The reference is a nearest-neighbour tour — a true optimum can "
                "only be better, never worse.",
                "Reordering can change which colour sits on top; review the preview.",
            ],
            "estimated_impact": "machine time only",
            "priority_hint": "speed",
            "estimated_savings": {
                "travel_mm": recoverable,
                "time_seconds": round(secs, 1),
                "label": f"up to −{sewtime.fmt_mm(recoverable)} travel · "
                         f"−{sewtime.fmt_secs(secs)} (best case)",
            },
        })

    # --- stitch direction smoothness ----------------------------------------
    direction = raw.get("direction") or {}
    jittery = [b for b in direction.get("blocks", []) if not b["smooth"]]
    if jittery:
        worst = jittery[0]
        findings.append({
            "id": "flag_direction",
            "op": None,  # re-angling fills = re-digitising; advisory only
            "severity": "warn",
            "title": f"{len(jittery)} runs with jittery stitch direction",
            "detail": (
                f"Worst run turns {worst['mean_turn_deg']:.0f}\u00b0 per stitch on "
                f"average ({cfg['direction_turn_deg']:.0f}\u00b0 or less is smooth). "
                "Every turn costs the machine a rotation and can shade thread "
                "twist unevenly. Smooth sewing keeps one direction per run, "
                "sweeping gradually for curves; consider re-angling or splitting "
                "this fill into direction bands."
            ),
            "locations": [
                {"type": "run", "run_index": b["run_index"],
                 "x": None, "y": None, "block_index": b["block_index"],
                 "mean_direction_deg": b["mean_direction_deg"],
                 "mean_turn_deg": b["mean_turn_deg"]}
                for b in jittery[:5]
            ],
            "caveats": [
                "Measured per run between needle-up moves; short detail runs "
                f"(under {direction.get('min_stitches', 12)} stitches) are excluded.",
                "Fixing this means re-angling fills \u2014 a re-digitising job, not "
                "an automatic transform. Use the direction overlay to see it.",
            ],
            "estimated_impact": "smoothness / sheen / speed",
            "priority_hint": "quality",
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
            "estimated_savings": None,
        })

    _apply_priority(findings, cfg.get("priority", "balanced"))

    raw["findings"] = findings
    return raw


def _apply_priority(findings: List[Dict[str, Any]], priority: str) -> None:
    """Re-rank and re-severity findings for the chosen optimisation goal.

    - speed:    travel/order problems surface first and loudest
    - quality:  density, direction and stitch-length issues first
    - balanced: severity order as computed (default)
    """
    if priority not in ("speed", "quality"):
        return
    rank_speed = {"reroute_travel": 0, "add_trims": 1, "remove_micro_stitches": 2,
                  "split_long_stitches": 3}
    rank_quality = {"flag_density": 0, "flag_direction": 1, "flag_short_stitches": 2,
                    "split_long_stitches": 3, "remove_isolated_stitches": 4}
    rank = rank_speed if priority == "speed" else rank_quality
    findings.sort(key=lambda f: (rank.get(f["id"], 10),
                                 {"high": 0, "warn": 1, "info": 2}[f["severity"]]))
    for f in findings:
        hint = f.get("priority_hint")
        if hint == priority and f["severity"] == "info":
            f["severity"] = "warn"
        elif hint and hint != priority and f["severity"] == "warn":
            f["severity"] = "info"


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
