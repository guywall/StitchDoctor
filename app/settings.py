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
