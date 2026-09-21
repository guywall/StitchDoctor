"""Stitch Doctor settings.

All tunables live here; deployment-specific values come from environment
variables so the container image stays generic for Plesk Docker deployments.
"""
import os

APP_NAME = "Stitch Doctor"

# --- Upload limits -----------------------------------------------------------
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # hard cap; nginx adds its own 50 MB cap

# Extensions pyembroidery can read (dispatches on file extension).
ALLOWED_EXTENSIONS = {
    ".pes", ".dst", ".jef", ".vp3", ".exp", ".pec", ".hus", ".u01",
    ".xxx", ".pcs", ".sew", ".shv", ".10o", ".100", ".bro", ".dat",
    ".dsb", ".dsz", ".emd", ".exy", ".fxy", ".gt", ".inb", ".jpx",
    ".ksm", ".max", ".mit", ".new", ".pcd", ".pcm", ".pcq", ".phb",
    ".phc", ".stc", ".stx", ".tap", ".tbf", ".zhs", ".zxy", ".csv",
    ".json", ".gcode",
}

# Formats offered as export targets in the UI.
EXPORT_FORMATS = ["pes", "dst", "jef", "vp3", "exp", "u01", "pec", "xxx"]

# --- Pattern store (versioned JSON on disk; the undo history IS the files) ---
DATA_DIR = os.environ.get("STITCHDR_DATA_DIR", "/data")
PATTERN_ROOT = os.path.join(DATA_DIR, "patterns")
SESSION_TTL_HOURS = int(os.environ.get("STITCHDR_TTL_HOURS", "24"))
MAX_VERSIONS_PER_PATTERN = 60

# --- Analysis thresholds (mm; pyembroidery stores 1/10 mm units) -------------
UNITS_PER_MM = 10.0

MICRO_STITCH_MM = 0.25         # below: redundant micro-stitch
SHORT_STITCH_MM = 0.7          # below: abnormally short stitch
LONG_STITCH_MM = 8.0           # above: may exceed hoop capability
LONG_JUMP_MM = 12.0            # above: long jump worth a trim
INFERRED_TRIM_MM = 3.0         # above: jump assumed to include a trim (PES/JEF)
DENSITY_STITCHES_PER_MM2 = 12.0
DENSITY_CELL_MM = 5.0
DENSITY_MIN_STITCHES = 120     # don't flag density on tiny designs
ISOLATED_RUN_MM = 4.0          # single stitch run far from others

# Density caveat: density is measured against an assumed hoop area.
ASSUMED_HOOP_MM = 100.0

# User-tunable analysis settings (exposed in the UI; clamped to these bounds).
ANALYSIS_SETTING_BOUNDS = {
    "micro_mm": (0.05, 2.0),
    "short_mm": (0.1, 5.0),
    "long_mm": (3.0, 30.0),
    "long_jump_mm": (5.0, 100.0),
    "density_per_mm2": (2.0, 60.0),
    "density_cell_mm": (2.0, 20.0),
    "isolated_mm": (1.0, 50.0),
}


def default_cfg() -> dict:
    """Analysis config with defaults from this module."""
    return {
        "micro_mm": MICRO_STITCH_MM,
        "short_mm": SHORT_STITCH_MM,
        "long_mm": LONG_STITCH_MM,
        "long_jump_mm": LONG_JUMP_MM,
        "density_per_mm2": DENSITY_STITCHES_PER_MM2,
        "density_cell_mm": DENSITY_CELL_MM,
        "density_min_stitches": DENSITY_MIN_STITCHES,
        "isolated_mm": ISOLATED_RUN_MM,
        "assumed_hoop_mm": ASSUMED_HOOP_MM,
    }


def effective_cfg(overrides: dict | None) -> dict:
    """Merge user overrides over defaults, coercing and clamping to bounds."""
    cfg = default_cfg()
    if not overrides:
        return cfg
    for key, value in overrides.items():
        if key not in ANALYSIS_SETTING_BOUNDS:
            continue
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        lo, hi = ANALYSIS_SETTING_BOUNDS[key]
        cfg[key] = max(lo, min(hi, num))
    return cfg

# --- Optional LLM explainer (strictly additive) ------------------------------
LLM_PROVIDER = os.environ.get("STITCHDR_LLM_PROVIDER", "").strip().lower()
LLM_API_KEY = os.environ.get("STITCHDR_LLM_API_KEY", "").strip()
LLM_MODEL = os.environ.get("STITCHDR_LLM_MODEL", "").strip()
LLM_TIMEOUT_SECS = 20


def llm_available() -> bool:
    return LLM_PROVIDER in ("gemini", "groq") and bool(LLM_API_KEY)


def default_llm_model() -> str:
    if LLM_MODEL:
        return LLM_MODEL
    return {"gemini": "gemini-2.0-flash", "groq": "llama-3.1-8b-instant"}.get(LLM_PROVIDER, "")
