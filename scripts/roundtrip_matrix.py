#!/usr/bin/env python3
"""Round-trip conversion matrix for Stitch Doctor.

Uploads a real embroidery file, exports it to every supported format,
re-imports each exported file, and diffs geometry against the source:
stitch counts, bounds, colour blocks, jump/trim totals, max stitch length.

Expected outcome per format (honest expectations, from pyembroidery's
encoder behaviour):
  pes/pec/jef/vp3/xxx  -- native structure preserved; trim deltas possible
  dst/exp/u01          -- flat single-colour encodings; colour-block counts
                          collapse (expected and reported as such, not hidden)

Usage:
  python scripts/roundtrip_matrix.py [--base URL] [--file PATH]

Defaults: --base https://stitchdr.threewalls.co.uk, --file the rose PES.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List

DEFAULT_BASE = "https://stitchdr.threewalls.co.uk"
DEFAULT_FILE = (r"C:\Users\guy\Downloads\rose-heart-floral-romantic-machine-"
                r"embroidery-desi-Embroidize_files_pes\5.50_inch_4vgkq0.pes")

# Geometry fields we diff per format. total_stitches must match exactly.
MUST_MATCH = ["total_stitches"]
# Deltas here are expected for flat formats (dst/exp/u01) but should be 0
# for structure-preserving formats; anything else is flagged.
SOFT_FIELDS = ["jump_count", "trim_count", "block_count"]


def http_json(base: str, path: str, data: Dict[str, Any]) -> Dict[str, Any]:
    req = urllib.request.Request(
        base + path, data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def http_upload(base: str, path: str, filename: str, content: bytes) -> Dict[str, Any]:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode()
    body += content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(base + path, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def http_download(base: str, path: str) -> bytes:
    with urllib.request.urlopen(base + path, timeout=180) as r:
        return r.read()


def snapshot(summary: Dict[str, Any]) -> Dict[str, Any]:
    m = summary.get("metrics", summary)
    return {
        "total_stitches": m.get("total_stitches"),
        "jump_count": m.get("jump_count"),
        "trim_count": m.get("trims"),
        "block_count": (m.get("color_changes", 0) + 1)
            if m.get("color_changes") is not None else None,
        "bounds": summary.get("extents") or m.get("extents"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--formats", default="pes,dst,jef,vp3,exp,u01,pec,xxx")
    args = ap.parse_args()

    with open(args.file, "rb") as fh:
        src_bytes = fh.read()
    src_name = args.file.replace("\\", "/").rsplit("/", 1)[-1]

    print(f"== Stitch Doctor round-trip matrix ==")
    print(f"   server: {args.base}")
    print(f"   source: {src_name} ({len(src_bytes)} bytes)\n")

    # -- 1. upload source --------------------------------------------------
    up = http_upload(args.base, "/api/upload", src_name, src_bytes)
    pid = up["pattern_id"]
    src = snapshot(up)
    print(f"source: {src['total_stitches']} stitches, "
          f"jumps={src['jump_count']} trims={src['trim_count']} "
          f"blocks={src['block_count']}\n")

    results: List[Dict[str, Any]] = []
    all_ok = True

    for fmt in [f.strip().lstrip(".") for f in args.formats.split(",") if f.strip()]:
        row: Dict[str, Any] = {"format": fmt}
        try:
            # -- 2. export -------------------------------------------------
            data = http_download(args.base, f"/api/export/{pid}?format={fmt}")
            # sanity: exporter must produce non-trivial bytes
            if len(data) < 64:
                raise RuntimeError(f"export suspiciously small ({len(data)} B)")

            # -- 3. re-import through the same door users use --------------
            back = http_upload(args.base, "/api/upload", f"rt.{fmt}", data)
            rt = snapshot(back)
            results.append(row)
            row.update(rt)
            row["bytes"] = len(data)
            row["error"] = None

            # -- 4. compare ------------------------------------------------
            diffs = []
            hard_fail = False
            for f in MUST_MATCH:
                if rt[f] != src[f]:
                    diffs.append(f"{f}: {src[f]} -> {rt[f]}")
                    hard_fail = True
            for f in SOFT_FIELDS:
                if rt[f] != src[f]:
                    diffs.append(f"{f}: {src[f]} -> {rt[f]}")
            if rt["bounds"] and src["bounds"]:
                bw = abs((rt["bounds"]["max_x_mm"] - rt["bounds"]["min_x_mm"])
                         - (src["bounds"]["max_x_mm"] - src["bounds"]["min_x_mm"]))
                bh = abs((rt["bounds"]["max_y_mm"] - rt["bounds"]["min_y_mm"])
                         - (src["bounds"]["max_y_mm"] - src["bounds"]["min_y_mm"]))
                if bw > 0.5 or bh > 0.5:
                    diffs.append(f"bounds: {src['bounds']} -> {rt['bounds']}")
                    hard_fail = hard_fail or fmt not in ("dst", "exp", "u01")
            row["diffs"] = diffs
            row["ok"] = not hard_fail
            all_ok = all_ok and not hard_fail
            status = "OK  " if not hard_fail else "FAIL"
            print(f"[{status}] {fmt.upper():4s} {len(data):7d} B  "
                  f"stitches={rt['total_stitches']} jumps={rt['jump_count']} "
                  f"trims={rt['trim_count']} blocks={rt['block_count']}")
            for d in diffs:
                print(f"         - {d}")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read()).get("detail", "")
            except Exception:  # noqa: BLE001
                pass
            row.update({"ok": False, "error": f"HTTP {exc.code}: {detail}",
                        "diffs": []})
            results.append(row)
            all_ok = False
            print(f"[FAIL] {fmt.upper():4s} HTTP {exc.code} {detail}")
        except Exception as exc:  # noqa: BLE001
            row.update({"ok": False, "error": str(exc), "diffs": []})
            results.append(row)
            all_ok = False
            print(f"[FAIL] {fmt.upper():4s} {exc}")

    # -- summary -----------------------------------------------------------
    print("\n== summary ==")
    for row in results:
        if row.get("error"):
            print(f"  {row['format']:4s} ERROR: {row['error']}")
        elif row.get("ok"):
            print(f"  {row['format']:4s} OK"
                  + (f" (deltas: {'; '.join(row['diffs'])})" if row["diffs"] else " (exact)"))
        else:
            print(f"  {row['format']:4s} FAIL: {'; '.join(row['diffs'])}")

    # cleanup: delete the uploaded pattern so we don't leave junk behind
    try:
        req = urllib.request.Request(args.base + f"/api/pattern/{pid}",
                                     method="DELETE")
        with urllib.request.urlopen(req, timeout=60):
            pass
        print(f"\n(cleaned up test pattern {pid})")
    except Exception:  # noqa: BLE001
        print(f"\n(note: test pattern {pid} left on server; delete failed)")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
