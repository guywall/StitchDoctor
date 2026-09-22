# Stitch Doctor 🧵

Embroidery file **analysis & optimisation** web app: upload → deterministic
diagnosis → visual report → selectable reversible fixes → live preview →
export. Built on [pyembroidery](https://github.com/EmbroidePy/pyembroidery),
served by FastAPI, deployed as a Docker container on Plesk.

## The studio

The preview is a **virtual stitch-out**: zoom/pan/hover canvas, real thread
colours with satin sheen, fabric backgrounds, and a player that animates the
design sewing itself with a scrub timeline and sew-time estimates (machine
speed configurable in `app/settings.py`). The **Compare** button wipes between
original and fixed versions; every fix shows an impact card (stitches, travel,
sew time). The **Sew order** panel drag-reorders colour blocks (reverse,
merge, delete too) — each change is a new reversible version.

`?pattern=<id>` reopens a previous session (reload-safe, shareable).

## The flow

1. **Upload** — PES, DST, JEF, VP3, EXP and ~40 more formats. The original
   file is stored untouched. PES/PEC STOP-based colour encoding is normalised
   to real colour blocks on import.
2. **Analysis** — fixed diagnostic protocol every time: stitch count, colour
   blocks, stitch-length distribution, micro/short/long stitches, jumps,
   jump travel, trims, density hotspots, isolated stitches. Actionable
   findings carry an `estimated_savings` label (stitches/travel/minutes).
3. **Report** — findings as plain-language cards, each with a severity,
   honesty caveats, and canvas locations for highlighting.
4. **Fixes** — tick a finding to apply its operation. Every fix produces a
   new persisted version (`v1.json, v2.json …` on the volume); the UI always
   rebuilds from v1 + the current selection, so unticking one fix can never
   revert another. The original is never modified.
5. **Preview** — thread / paths / problems views, hover inspection, animated
   stitch-out with sew-time readout; live-updates after every fix.
6. **Export** — write the fixed pattern back out to PES/DST/JEF/VP3/EXP and
   more, compare Original vs Proposed metrics first, and print a
   before/after **Report** for the client.

The optional LLM explainer (Gemini/Groq free tier) only *interprets*
findings; the app is fully functional without it.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/upload` | upload file → `{pattern_id, metrics, findings}` |
| `GET /api/pattern/{id}` | metrics + findings for a version |
| `GET /api/geometry/{id}` | canvas geometry (segments, jumps, trims, stitch indices) |
| `GET /api/blocks/{id}` | per-colour-block summary (sew-order editor) |
| `POST /api/fix/{id}` | apply op → new version |
| `POST /api/undo/{id}` | drop latest version |
| `POST /api/reset/{id}` | back to v1 |
| `GET /api/compare/{id}` | original vs proposed (in-memory, never re-read exports) |
| `GET /api/export/{id}?format=pes` | download fixed design |
| `POST /api/explain/{id}` | optional LLM interpretation |
| `GET /api/health` | liveness + versions |
| `POST /api/convert` | legacy simple convert endpoint |

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `STITCHDR_DATA_DIR` | `/data` | pattern store root (mount a volume!) |
| `STITCHDR_TTL_HOURS` | `24` | prune uploads older than this |
| `STITCHDR_LLM_PROVIDER` | _(empty)_ | `gemini` or `groq` — empty disables LLM |
| `STITCHDR_LLM_API_KEY` | _(empty)_ | API key; never committed |
| `STITCHDR_LLM_MODEL` | provider default | override model name |

## Run locally

```bash
docker build -t stitchdoctor .
docker run -p 8001:8000 -v stitchdoctor-data:/data stitchdoctor
# open http://localhost:8001
```

Tests:

```bash
pip install -r requirements.txt && pytest app/tests -q
```

## Deploy on Plesk

**One command as root from the deployed source directory:**

```bash
./install.sh
```

The script is idempotent and does: verify Docker + free port → create
`stitchdr.threewalls.co.uk` subdomain → build image → swap container with
`--restart unless-stopped` and a `stitchdoctor-data` volume → write the
nginx proxy directives (50 MB upload cap) → request a Let's Encrypt
certificate → health-check.

Redeploy = `git pull && ./install.sh`.

Manual steps, if you prefer:

1. Plesk → **Websites & Domains → Add Subdomain** → `stitchdr.threewalls.co.uk`.
2. `docker build -t stitchdoctor . && docker run -d --name stitchdoctor
   --restart unless-stopped -p 127.0.0.1:8001:8000 -v stitchdoctor-data:/data stitchdoctor`
3. Subdomain → **Apache & nginx Settings → Additional nginx directives**:

   ```nginx
   location / {
       proxy_pass http://127.0.0.1:8001;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       client_max_body_size 50m;
   }
   ```

4. Issue a Let's Encrypt cert for the subdomain.

## Notes

- The Plesk Python extension is *not* used deliberately (it's absent and
  legacy); Docker pins Python 3.12.
- `/data` must be a volume — pattern versions (your undo history) live there.
- Comparison views are computed from in-memory/persisted patterns, never by
  re-reading exported machine files (PES/DST round-trips are lossy).
- Trim counts on PES/JEF are *inferred* from jump sequences; DST/U01/EXP
  carry explicit trims. Findings carry caveats where this applies.
- Imunify360 is installed on the host; 4xx/5xx floods from the app are
  unlikely to trip it, but if uploads start failing at the edge, check there.
