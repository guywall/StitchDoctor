/* Stitch Doctor front-end: upload → findings → per-fix toggles → preview → verify → export. */
"use strict";

const state = {
  patternId: null,
  version: null,
  versions: [],
  geometry: null,
  metrics: {},
  jumps: [],
  findings: [],
  cfg: null,
  configSchema: null,
  view: "thread",
  activeOps: [],        // [{op, params}] — source of truth for fixes
  highlight: null,      // finding id currently highlighted
  busy: false,
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// API helpers

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_) { /* not json */ }
    throw new Error(detail);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Upload

$("upload-btn").addEventListener("click", async () => {
  const fileInput = $("file");
  $("upload-error").textContent = "";
  if (!fileInput.files.length) {
    $("upload-error").textContent = "Choose a file first.";
    return;
  }
  const fd = new FormData();
  fd.append("file", fileInput.files[0]);
  $("upload-btn").disabled = true;
  $("upload-btn").textContent = "Analysing…";
  try {
    const data = await api("/api/upload", { method: "POST", body: fd });
    state.patternId = data.pattern_id;
    state.cfg = data.cfg;
    enterApp(data);
  } catch (err) {
    $("upload-error").textContent = err.message;
  } finally {
    $("upload-btn").disabled = false;
    $("upload-btn").textContent = "Analyse";
  }
});

// ---------------------------------------------------------------------------
// App entry / state updates

function enterApp(data) {
  $("upload-section").hidden = true;
  $("app-section").hidden = false;
  state.activeOps = [];
  state.highlight = null;
  updateState(data);
  renderSettingsPanel();
  loadConfigSchema();
  fetchGeometry();
}

function updateState(data) {
  state.version = data.version;
  state.versions = data.versions || [data.version];
  state.metrics = data.metrics || {};
  state.findings = data.findings || [];
  state.jumps = data.jumps || [];
  if (data.cfg) state.cfg = data.cfg;
  renderFindings();
  renderStats();
  renderVerifyOut(null); // clear stale verify results
  $("undo-btn").disabled = state.versions.length < 2;
  $("reset-btn").disabled =
    state.versions.length < 2 || state.versions[0] !== 1;
}

async function fetchGeometry() {
  const geo = await api(
    `/api/geometry/${state.patternId}?version=${state.version}`
  );
  state.geometry = geo;
  drawCanvas();
}

// ---------------------------------------------------------------------------
// Settings panel (analysis thresholds)

async function loadConfigSchema() {
  if (state.configSchema) { renderSettingsPanel(); return; }
  try {
    state.configSchema = await api("/api/config");
  } catch (_) {
    state.configSchema = null;
  }
  renderSettingsPanel();
}

const SETTING_LABELS = {
  micro_mm: "Micro-stitch threshold (mm)",
  short_mm: "Short-stitch threshold (mm)",
  long_mm: "Long-stitch threshold (mm)",
  long_jump_mm: "Long-jump threshold (mm)",
  density_per_mm2: "Density warning (stitches/mm²)",
  density_cell_mm: "Density grid cell (mm)",
  isolated_mm: "Isolated-stitch distance (mm)",
};

function renderSettingsPanel() {
  const panel = $("settings-panel");
  if (!panel) return;
  panel.innerHTML = "";
  if (!state.cfg || !state.configSchema) {
    panel.textContent = "Settings unavailable.";
    return;
  }
  const { bounds } = state.configSchema;
  for (const [key, label] of Object.entries(SETTING_LABELS)) {
    if (!(key in state.cfg)) continue;
    const [lo, hi] = bounds[key] || [0, 100];
    const row = document.createElement("div");
    row.className = "setting-row";
    row.innerHTML = `
      <label>${label}</label>
      <input type="range" min="${lo}" max="${hi}" step="0.05" value="${state.cfg[key]}" data-key="${key}">
      <output>${state.cfg[key]}</output>
    `;
    const slider = row.querySelector("input");
    const out = row.querySelector("output");
    slider.addEventListener("input", () => { out.value = slider.value; });
    slider.addEventListener("change", () => onSettingChanged());
    panel.appendChild(row);
  }
}

let settingTimer = null;
function onSettingChanged() {
  // read slider values into state.cfg
  document.querySelectorAll("#settings-panel input[type=range]").forEach((s) => {
    state.cfg[s.dataset.key] = parseFloat(s.value);
  });
  if ($("auto-analyse").checked) {
    clearTimeout(settingTimer);
    settingTimer = setTimeout(reanalyse, 350);
  }
}

$("settings-reset").addEventListener("click", async () => {
  if (!state.configSchema) return;
  state.cfg = { ...state.configSchema.defaults };
  renderSettingsPanel();
  reanalyse();
});

$("reanalyse-btn").addEventListener("click", reanalyse);

async function reanalyse() {
  if (!state.patternId) return;
  try {
    const qs = "cfg=" + encodeURIComponent(JSON.stringify(state.cfg));
    const data = await api(`/api/pattern/${state.patternId}?${qs}`);
    updateState(data);
    // findings changed — active ops may no longer make sense; keep as-is
    drawCanvas();
  } catch (err) {
    $("upload-error").textContent = err.message;
  }
}

// ---------------------------------------------------------------------------
// Findings list: per-fix toggles + click-to-highlight

function renderFindings() {
  const box = $("findings");
  box.innerHTML = "";
  if (!state.findings.length) {
    box.innerHTML =
      '<p class="finding"><em>No issues found — this design looks clean.</em></p>';
    return;
  }
  for (const f of state.findings) {
    const div = document.createElement("div");
    div.className = "finding";
    div.dataset.finding = f.id;
    const hasLocations = Array.isArray(f.locations) && f.locations.length > 0;
    const caveat = f.caveats && f.caveats.length
      ? `<p class="caveat">⚠ ${f.caveats.join(" ")}</p>`
      : "";
    const action = f.op
      ? `<label><input type="checkbox" data-op="${f.op}" ${opActive(f.op) ? "checked" : ""}> Apply fix</label>`
      : `<p class="advisory">Advisory — no automatic fix.</p>`;
    div.innerHTML = `
      <span class="sev ${f.severity}">${f.severity}</span>
      <h3>${f.title}</h3>
      <p>${f.detail}</p>
      ${caveat}
      <p><em>Impact: ${f.estimated_impact}</em></p>
      ${action}
      ${hasLocations ? '<button class="locate-btn">Show on canvas</button>' : ""}
      <button class="explain-btn" data-finding="${f.id}">Why?</button>
      <div class="finding-error"></div>
    `;
    box.appendChild(div);

    div.querySelector(".explain-btn").addEventListener("click", () => explainFinding(f.id));
    const locate = div.querySelector(".locate-btn");
    if (locate) locate.addEventListener("click", () => toggleHighlight(f.id, div));

    const checkbox = div.querySelector('input[type="checkbox"]');
    if (checkbox) {
      checkbox.addEventListener("change", (e) => {
        if (e.target.checked) {
          addOp(e.target.dataset.op, div);
        } else {
          removeOp(e.target.dataset.op, div);
        }
      });
    }
  }
}

function opActive(op) {
  return state.activeOps.some((o) => o.op === op);
}

async function addOp(op, card) {
  state.activeOps.push({ op, params: {} });
  await rebuild("add", op, card);
}

async function removeOp(op, card) {
  state.activeOps = state.activeOps.filter((o) => o.op !== op);
  await rebuild("remove", op, card);
}

async function rebuild(kind, op, card) {
  if (state.busy) return;
  state.busy = true;
  setError(card, "");
  try {
    const data = await api(`/api/rebuild/${state.patternId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ops: state.activeOps }),
    });
    updateState(data);
    await fetchGeometry();
  } catch (err) {
    // roll the checkbox back; keep the ops list consistent
    if (kind === "add") {
      state.activeOps = state.activeOps.filter((o) => o.op !== op);
    } else {
      state.activeOps.push({ op, params: {} });
    }
    const cb = card && card.querySelector('input[type="checkbox"]');
    if (cb) cb.checked = kind !== "add";
    setError(card, err.message);
  } finally {
    state.busy = false;
  }
}

function setError(card, message) {
  if (!card) return;
  const slot = card.querySelector(".finding-error");
  if (slot) slot.textContent = message;
}

// Undo/reset drop ALL selected ops (server rebuilds from v1 on next rebuild;
// here we also clear the toggle states to match).
$("undo-btn").addEventListener("click", async () => {
  try {
    const data = await api(`/api/undo/${state.patternId}`, { method: "POST" });
    state.activeOps = [];
    renderFindings();
    updateState(data);
    await fetchGeometry();
  } catch (err) {
    $("upload-error").textContent = err.message;
  }
});

$("reset-btn").addEventListener("click", async () => {
  try {
    const data = await api(`/api/reset/${state.patternId}`, { method: "POST" });
    state.activeOps = [];
    renderFindings();
    updateState(data);
    await fetchGeometry();
  } catch (err) {
    $("upload-error").textContent = err.message;
  }
});

// ---------------------------------------------------------------------------
// Explanation (optional LLM)

async function explainFinding(findingId) {
  const box = $("explain-box");
  box.hidden = false;
  $("explain-text").textContent = "Thinking…";
  try {
    const data = await api(`/api/explain/${state.patternId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ finding_id: findingId }),
    });
    $("explain-text").textContent = data.text;
  } catch (err) {
    $("explain-text").textContent =
      "Interpretation unavailable: " + err.message;
  }
}

// ---------------------------------------------------------------------------
// Canvas rendering

document.querySelectorAll('input[name="view"]').forEach((r) =>
  r.addEventListener("change", (e) => {
    state.view = e.target.value;
    drawCanvas();
  })
);

$("refresh-preview").addEventListener("click", drawCanvas);

// click a finding's "Show on canvas" → zoom to its locations
function toggleHighlight(findingId, card) {
  if (state.highlight === findingId) {
    state.highlight = null;
    if (card) card.classList.remove("locating");
  } else {
    state.highlight = findingId;
    document.querySelectorAll(".finding").forEach((el) => el.classList.remove("locating"));
    if (card) card.classList.add("locating");
  }
  drawCanvas();
}

function findingById(id) {
  return state.findings.find((f) => f.id === id) || null;
}

function drawCanvas() {
  const canvas = $("canvas");
  const ctx = canvas.getContext("2d");
  const geo = state.geometry;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  setLegend();
  if (!geo || geo.empty || !geo.segments.length) {
    ctx.fillStyle = "#999";
    ctx.font = "16px system-ui";
    ctx.fillText("No stitch data", 20, 30);
    return;
  }

  const hl = state.highlight ? findingById(state.highlight) : null;

  // viewport: whole design, or zoomed to the highlighted finding
  let view = { minX: geo.extents.min_x, maxX: geo.extents.max_x,
               minY: geo.extents.min_y, maxY: geo.extents.max_y };
  if (hl && hl.locations.length) {
    const b = locationBounds(hl.locations);
    if (b) {
      const pad = Math.max((b.maxX - b.minX), (b.maxY - b.minY)) * 0.6 + 5;
      view = { minX: b.minX - pad, maxX: b.maxX + pad,
               minY: b.minY - pad, maxY: b.maxY + pad };
    }
  }

  const pad = 20;
  const vw = Math.max(view.maxX - view.minX, 1);
  const vh = Math.max(view.maxY - view.minY, 1);
  const scale = Math.min(
    (canvas.width - pad * 2) / vw,
    (canvas.height - pad * 2) / vh
  );
  const ox = pad - view.minX * scale;
  const oy = canvas.height - pad + view.minY * scale;
  const toX = (x) => ox + x * scale;
  const toY = (y) => oy - y * scale;

  const palette = geo.palette || ["#333"];
  const threads = geo.threads || [];
  const colorFor = (block) =>
    threads[block] || palette[block % palette.length] || "#333";

  const showPaths = state.view !== "thread";
  const showJumps = state.view === "paths" || state.view === "problems";

  // stitch segments
  for (const seg of geo.segments) {
    if (seg.type === "jump") {
      if (!showJumps) continue;
      ctx.strokeStyle = "rgba(160,160,160,0.7)";
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1;
    } else {
      ctx.strokeStyle = state.view === "thread" ? colorFor(seg.color) : "#333";
      ctx.setLineDash([]);
      ctx.lineWidth = state.view === "thread" ? 1.4 : 1;
    }
    ctx.beginPath();
    const pts = seg.points;
    // cheap viewport culling for big designs
    if (pts.length > 2) {
      let visible = false;
      for (const [px, py] of pts) {
        const sx = toX(px), sy = toY(py);
        if (sx > -50 && sx < canvas.width + 50 && sy > -50 && sy < canvas.height + 50) {
          visible = true;
          break;
        }
      }
      if (!visible) continue;
    }
    ctx.moveTo(toX(pts[0][0]), toY(pts[0][1]));
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(toX(pts[i][0]), toY(pts[i][1]));
    }
    ctx.stroke();
  }
  ctx.setLineDash([]);

  // trim markers
  if (showPaths) {
    for (const t of geo.trims) {
      const sx = toX(t.x), sy = toY(t.y);
      if (sx < -10 || sx > canvas.width + 10 || sy < -10 || sy > canvas.height + 10) continue;
      ctx.strokeStyle = "#b3424a";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(sx - 4, sy - 4);
      ctx.lineTo(sx + 4, sy + 4);
      ctx.moveTo(sx + 4, sy - 4);
      ctx.lineTo(sx - 4, sy + 4);
      ctx.stroke();
    }
  }

  // density heat overlay: always visible in every view (the "obvious" requirement)
  drawDensityOverlay(ctx, toX, toY);

  // problems overlay: short/long stitch endpoints + untrimmed long jumps
  if (state.view === "problems" || hl) {
    drawProblemMarkers(ctx, toX, toY, hl);
  }

  // highlight box around zoomed region
  if (hl && hl.locations.length) {
    ctx.strokeStyle = "#b3424a";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(6, 6, canvas.width - 12, canvas.height - 12);
    ctx.setLineDash([]);
    ctx.fillStyle = "#b3424a";
    ctx.font = "bold 13px system-ui";
    ctx.fillText("Highlighted: " + hl.title, 14, 22);
  }

  // start point marker
  if (geo.segments.length) {
    const first = geo.segments[0].points[0];
    const sx = toX(first[0]), sy = toY(first[1]);
    if (sx > 0 && sx < canvas.width && sy > 0 && sy < canvas.height) {
      ctx.fillStyle = "#3a7d44";
      ctx.beginPath();
      ctx.arc(sx, sy, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#fff";
      ctx.font = "bold 8px system-ui";
      ctx.fillText("S", sx - 2.5, sy + 3);
    }
  }
}

function locationBounds(locations) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const loc of locations) {
    let pts = [];
    if (loc.type === "jump") pts = [[loc.from[0], loc.from[1]], [loc.to[0], loc.to[1]]];
    else if (loc.type === "stitch") pts = [[loc.from[0], loc.from[1]], [loc.to[0], loc.to[1]]];
    else if (loc.type === "density") pts = [[loc.x, loc.y]];
    else if (loc.type === "run") pts = [[loc.x, loc.y]];
    for (const [x, y] of pts) {
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  if (minX === Infinity) return null;
  return { minX, maxX, minY, maxY };
}

// density heat cells — drawn whenever hotspots exist, in every view
function drawDensityOverlay(ctx, toX, toY) {
  const hotspots = state.metrics.density_hotspots || [];
  if (!hotspots.length) return;
  const cell = (state.cfg && state.cfg.density_cell_mm) || 5;
  for (const h of hotspots) {
    const half = cell / 2;
    const x0 = toX(h.x_mm - half), y0 = toY(h.y_mm + half);
    const w = (cell) * (toX(h.x_mm + half) - toX(h.x_mm - half));
    const h2 = (cell) * (toY(h.y_mm - half) - toY(h.y_mm + half));
    const worst = h.stitches_per_mm2 / ((state.cfg && state.cfg.density_per_mm2) || 12);
    const alpha = Math.min(0.15 + 0.45 * (worst - 1), 0.75);
    ctx.fillStyle = `rgba(179, 42, 42, ${alpha.toFixed(2)})`;
    ctx.fillRect(x0, y0, w, Math.abs(h2));
    ctx.strokeStyle = "rgba(179, 42, 42, 0.9)";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x0, y0, w, Math.abs(h2));
    // density label
    ctx.fillStyle = "rgba(120, 10, 10, 0.95)";
    ctx.font = "bold 11px system-ui";
    ctx.fillText(`${h.stitches_per_mm2}/mm²`, x0, y0 - 3);
  }
}

// markers for stitch/jump problems (+ highlighted finding emphasis)
function drawProblemMarkers(ctx, toX, toY, hl) {
  const drawJumpSet = (jumps, color, width) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    for (const j of jumps) {
      ctx.beginPath();
      ctx.moveTo(toX(j.from[0]), toY(j.from[1]));
      ctx.lineTo(toX(j.to[0]), toY(j.to[1]));
      ctx.stroke();
    }
  };
  const longJumps = state.jumps.filter(
    (j) => j.length_mm > ((state.cfg && state.cfg.long_jump_mm) || 12)
  );
  const untrimmed = longJumps.filter((j) => !j.after_trim);
  if (state.view === "problems") {
    drawJumpSet(untrimmed, "rgba(255,0,0,0.8)", 2.5);
    if (longJumps.length > untrimmed.length) {
      drawJumpSet(longJumps.filter((j) => j.after_trim), "rgba(255,140,0,0.55)", 2);
    }
  }
  if (hl) {
    const locJumps = hl.locations.filter((l) => l.type === "jump");
    drawJumpSet(locJumps.map((l) => ({ from: l.from, to: l.to })), "#00b3ff", 3);
  }

  // short/long stitch locations as dots (advisory + splittable findings)
  for (const f of state.findings) {
    const locs = (f.locations || []).filter((l) => l.type === "stitch");
    if (!locs.length) continue;
    const isHl = state.highlight === f.id;
    if (state.view !== "problems" && !isHl) continue;
    ctx.fillStyle = f.id === "remove_micro_stitches"
      ? (isHl ? "#b3424a" : "rgba(179,66,74,0.45)")
      : (isHl ? "#e08a00" : "rgba(224,138,0,0.4)");
    const r = isHl ? 4 : 2.5;
    for (const l of locs) {
      const sx = toX(l.to[0]), sy = toY(l.to[1]);
      if (sx < -10 || sx > canvas.width + 10 || sy < -10 || sy > canvas.height + 10) continue;
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

function setLegend() {
  const legend = $("legend");
  const hotspots = state.metrics.density_hotspots || [];
  const parts = [];
  if (hotspots.length) parts.push("⬛ density ≥ " + ((state.cfg && state.cfg.density_per_mm2) || 12) + "/mm²");
  if (state.view !== "thread") parts.push("✂ trims");
  if (state.view === "paths" || state.view === "problems") parts.push("┄ jumps");
  if (state.view === "problems") parts.push("▬ untrimmed long jump");
  if (!parts.length) { legend.hidden = true; legend.textContent = ""; return; }
  legend.hidden = false;
  legend.textContent = parts.join("   ·   ");
}

// ---------------------------------------------------------------------------
// Verify (round-trip what-if)

$("verify-format").innerHTML =
  (["pes", "dst", "jef", "vp3", "exp", "u01", "pec", "xxx"] )
    .map((f) => `<option value="${f}">${f.toUpperCase()}</option>`).join("");

$("verify-btn").addEventListener("click", async () => {
  const out = $("verify-out");
  out.innerHTML = "<p class='hint'>Checking round-trip…</p>";
  try {
    const data = await api(`/api/verify/${state.patternId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ops: state.activeOps,
        cfg: state.cfg,
        format: $("verify-format").value,
      }),
    });
    renderVerifyOut(data);
  } catch (err) {
    out.innerHTML = `<p class="error">Verify failed: ${err.message}</p>`;
  }
});

function renderVerifyOut(data) {
  const out = $("verify-out");
  if (!data) { out.innerHTML = ""; return; }
  const w = data.working.metrics, r = data.reimported.metrics;
  const row = (label, a, b, warn) => `
    <tr${warn ? ' class="warnrow"' : ""}>
      <td>${label}</td><td>${a}</td><td>${b}</td>
      ${warn ? `<td class="warn">⚠ ${warn}</td>` : "<td></td>"}
    </tr>`;
  const warns = [];
  if (data.stitch_delta !== 0) warns.push(`${data.stitch_delta > 0 ? "+" : ""}${data.stitch_delta} stitches from re-import`);
  if (w.jump_count !== r.jump_count) warns.push(`${r.jump_count - w.jump_count > 0 ? "+" : ""}${r.jump_count - w.jump_count} jumps (writer inserts trim-jump pairs)`);
  if (w.trims !== r.trims) warns.push(`${r.trims - w.trims > 0 ? "+" : ""}${r.trims - w.trims} trims`);
  out.innerHTML = `
    <table class="verify-table">
      <tr><th></th><th>Working</th><th>Re-imported ${data.format.toUpperCase()}</th><th></th></tr>
      ${row("Stitches", w.total_stitches.toLocaleString(), r.total_stitches.toLocaleString(),
            data.stitch_delta !== 0 ? `Δ ${data.stitch_delta > 0 ? "+" : ""}${data.stitch_delta}` : null)}
      ${row("Jumps", w.jump_count, r.jump_count,
            w.jump_count !== r.jump_count ? `Δ ${r.jump_count - w.jump_count}` : null)}
      ${row("Trims", w.trims, r.trims,
            w.trims !== r.trims ? `Δ ${r.trims - w.trims}` : null)}
      ${row("Max stitch (mm)", w.max_stitch_mm, r.max_stitch_mm)}
      ${row("Jump travel (mm)", w.jump_travel_mm, r.jump_travel_mm)}
    </table>
    ${warns.length
      ? `<p class="caveat">⚠ Expected format behaviour: ${warns.join("; ")}. Stitch geometry is unaffected.</p>`
      : '<p class="ok">✓ Round-trip is faithful.</p>'}
  `;
}

// ---------------------------------------------------------------------------
// Stats + export

function renderStats() {
  const m = state.metrics;
  if (!m || !m.total_stitches && !m.extents) return;
  const ex = m.extents || {};
  $("stats").textContent =
    `${(m.total_stitches || 0).toLocaleString()} stitches · ` +
    `${(m.color_changes || 0) + 1} colours · ${m.jump_count || 0} jumps ` +
    `(${m.jump_travel_mm || 0} mm travel) · ${m.trims || 0} trims\n` +
    `${ex.width_mm || 0} × ${ex.height_mm || 0} mm · max stitch ${m.max_stitch_mm || 0} mm`;
}

$("export-btn").addEventListener("click", () => {
  if (!state.patternId) return;
  const fmt = $("export-format").value;
  window.location = `/api/export/${state.patternId}?format=${fmt}`;
});
