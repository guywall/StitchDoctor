# Stitch Doctor — Plan v2 (failure-driven revision)

Built from the approved plan in "Building a Plesk Web App", stress-tested
against the actual server (Plesk Obsidian 18.0.80.7, AlmaLinux 8.10, root SSH)
with one hard constraint: **simple installation**.

---

## 1. Holes in the approved plan (what will go wrong)

### 1.1 FATAL: Passenger/Python is not on the server
The plan assumes "Python (Passenger) app" hosting. The server's extension list
has **no Python extension** (`plesk bin extension --list` shows no `python`).
Plesk's Python-by-Passenger support is legacy, EOL-adjacent, and would need to
be installed manually — the opposite of simple installation.

### 1.2 FATAL: System Python is 3.6.8 (EOL)
Even if Passenger existed, it would bind to system Python 3.6.8.
pyembroidery's modern releases and any maintained web framework target 3.8+.
Result: dependency hell, or hand-installing a toolchain Python into the
Plesk/Passenger stack. Fragile, and breaks on every Plesk update.

### 1.3 Certain: session state will break the "reversible fixes" feature
"Reversible fix operations applied to a working pattern" implies server-side
session state. With multiple workers (any real deployment), in-memory state
silently disappears between requests; with file state, uploads scattered in
tmp leak disk. This must be designed as **stateless + persisted pattern JSON**,
not bolted on later.

### 1.4 Likely: format round-trip data loss undermines "Original vs Proposed"
pyembroidery reads stitch data, not native object models. Reading a PES and
writing a PES back does not reproduce the original file's internal structure
(sew sequences, embedded thumbnails, color names). If the comparison view is
fed by re-read files, it will "work" while showing subtly wrong originals.
Rule: **never re-read an exported file as ground truth**; compare patterns
held in memory / persisted JSON.

### 1.5 Likely: analysis findings mislead on home formats
- DST has explicit trim/jump commands; **PES/JEF often encode trims as jump
  sequences**, so "trim count" without inference = misleading.
- Density math must use pyembroidery units (0.1 mm) and a chosen hoop; without
  a hoop assumption, density warnings are arbitrary.
- "Deterministic" is good — but determinism with wrong inputs is confidently
  wrong. Each finding needs a confidence/caveat flag from day one.

### 1.6 Possible: the optional LLM becomes a hard dependency
Free-tier APIs (Gemini/Groq) rate-limit, expire, and need keys stored somewhere.
If the UI renders around LLM output, a dead key bricks the experience.
Rule: LLM strictly additive — UI fully functional with it absent, timeouts +
static-text fallback, key in env var only.

### 1.7 Deployment-mechanics risks
- **Port collision**: webpagetest already binds 127.0.0.1:8085. Container must
  use a checked-free port (plan uses 8001; installer verifies).
- **Upload abuse**: a public convert endpoint fills /tmp and RAM. Need a hard
  upload cap (e.g. 20 MB), temp cleanup on failure, and request timeouts.
- **Imunify360 false positives**: it's installed; long-running python in a
  container is usually fine, but 422/500 floods may trip it. Note in install
  docs.
- **SELinux (AlmaLinux)**: proxying to a TCP loopback port avoids unix-socket
  label problems. Keep container networking TCP-only.
- **Reboots**: container must run with `--restart unless-stopped` or the site
  dies silently at 3am.

### 1.8 Process risk: two agents, one app
This workspace and the other thread both scaffolded toward the same domain.
Resolution: **one codebase, this Docker stack, the other thread's feature
roadmap**. The other thread's Passenger plan is retired by 1.1–1.2 above.

---

## 2. Revised architecture (the "what goes wrong" fixes applied)

```
stitchdr.threewalls.co.uk  (Let's Encrypt)
        │ nginx (Plesk vhost directives, 50 MB cap)
        ▼
127.0.0.1:8001  Docker container (python:3.12-slim)
   FastAPI + pyembroidery (pinned version)
   ├── /api/upload      → parse once, persist pattern JSON to disk, return id
   ├── /api/pattern/:id → state source of truth for UI
   ├── /api/analyze/:id → deterministic findings (with caveats per finding)
   ├── /api/fix/:id     → apply/revert ops, write NEW versioned pattern JSON
   ├── /api/compare/:id → renders original vs proposed from JSON, never re-read
   ├── /api/export/:id  → PES/DST/JEF/VP3/EXP from the pattern object
   └── (optional) /api/explain → LLM, additive, env-keyed, timeout-wrapped
```

State: `/data/patterns/<id>/v1.json, v2.json...` inside a mounted volume.
Fix history = file versions; "revert" = point at the previous version. No
database needed for v1 — files survive restarts, are trivially backed up, and
make the state problem (1.3) disappear.

---

## 3. Simple installation — the actual deliverable

Target: **one script, run once as root on the server, done.**

`install.sh` (idempotent, safe to re-run):
1. Verify Docker + free port 8001; fail with a message if taken.
2. `plesk bin site --create stitchdr.threewalls.co.uk -hosting true` (skip if
   exists; verify exact flags with `plesk help site` at runtime).
3. `docker build -t stitchdoctor .` in the deployed source dir.
4. Swap container: `docker rm -f stitchdoctor; docker run -d --name
   stitchdoctor --restart unless-stopped -p 127.0.0.1:8001:8000 -v
   stitchdoctor-data:/data stitchdoctor`.
5. Write nginx directives into the vhost include
   (`/var/www/vhosts/system/stitchdr.threewalls.co.uk/conf/vhost_nginx.conf`),
   then `plesk sbin nginxmgt reload`.
6. Issue cert: `plesk bin extension --exec letsencrypt --cli -d
   stitchdr.threewalls.co.uk` (skip if already valid).
7. Health check `curl 127.0.0.1:8001/api/health` and the public URL.

Redeploy = `git pull && ./install.sh`. That is the whole operational story.

---

## 4. Build order (each phase independently shippable)

| Phase | Ships | Why first |
|---|---|---|
| P0 | Convert + preview (exists in this workspace) + install.sh + subdomain live | Deployment risk retired before feature risk |
| P1 | Upload → persisted pattern JSON → analyze findings (jumps, inferred trims, length violations, density w/ hoop setting) | Core value; state design enforced early |
| P2 | Fix ops + versioned JSON + compare view | Depends entirely on P1's state model |
| P3 | Export formats + round-trip sanity tests | Guarded by tests from 1.4 lessons |
| P4 | Optional LLM explain, env-keyed, fallback text | Last, isolated, removable |

## 5. Explicitly deferred (on purpose)
- User accounts / MariaDB — no need until multi-user is real.
- docker compose — one container doesn't justify it yet.
- Async job queue — analysis should stay <2s for sane designs; revisit if not.
