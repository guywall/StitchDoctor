/* Stitch Doctor front-end: studio shell wiring viewer, player, compare,
   sew-order editor, findings, verify and export. ES modules, no build step. */
"use strict";

import { SDViewer } from "./viewer.js";
import { SDPlayer } from "./player.js";
import { SDCompare, renderImpact } from "./compare.js";
import { SDBlocks } from "./blocks.js";

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
  machine: { spm: 700, trim_s: 3, stop_s: 0.6, extra_stitch_s: 0.15 },
  view: "thread",
  activeOps: [],
  highlight: null,
  busy: false,
  v1Metrics: null,          // cached original metrics for impact deltas
  originalGeometry: null,   // cached v1 geometry for the wipe compare
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Core instances

const viewer = new SDViewer($("canvas"), $("tooltip"));
const player = new SDPlayer(viewer, {
  player: $("player"), playBtn: $("play-btn"), stopBtn: $("stop-btn"),
  scrub: $("scrub"), speed: $("speed"), time: $("player-time"),
  sound: $("sound"),
}, state.machine);

const compare = new SDCompare({
  viewer,
  els: {
    bar: $("compare-bar"), slider: $("wipe-slider"),
    modeBtn: $("compare-mode"), closeBtn: $("compare-close"),
  },
  fetchOriginalGeometry: async () => {
    if (!state.originalGeometry) {
      state.originalGeometry = await api(
        `/api/geometry/${state.patternId}?version=1`);
    }
    return state.originalGeometry;
  },
});

const blocks = new SDBlocks({
  api,
  getPid: () => state.patternId,
  viewer,
  els: {
    panel: $("blocks-panel"), list: $("blocks-list"),
    travel: $("blocks-travel"), refreshBtn: $("blocks-refresh"),
  },
  onChange: async () => {
    await refreshAfterChange();
  },
});

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
    state.filename = data.filename;
    state.v1Metrics = null;
    state.originalGeometry = null;
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
  $("report-section").hidden = true;
  state.activeOps = [];
  state.highlight = null;
  compare.exit();
  updateState(data);
  renderSettingsPanel();
  loadConfigSchema();
  fetchGeometry();
  player.show();
  blocks.refresh();
  cacheV1Metrics();
}

function updateState(data) {
  state.version = data.version;
  state.versions = data.versions || [data.version];
  state.metrics = data.metrics || {};
  state.findings = data.findings || [];
  state.jumps = data.jumps || [];
  if (data.cfg) state.cfg = data.cfg;
  if (data.filename) state.filename = data.filename;
  viewer.setProblemJumps(state.jumps.filter(
    (j) => j.length_mm > ((state.cfg && state.cfg.long_jump_mm) || 12) && !j.after_trim));
  renderFindings();
  renderStats();
  renderImpact($("impact"), state.v1Metrics, state.metrics);
  renderVerifyOut(null);
  setLegend();
  $("undo-btn").disabled = state.versions.length < 2;
  $("reset-btn").disabled =
    state.versions.length < 2 || state.versions[0] !== 1;
}

async function refreshAfterChange() {
  const data = await api(`/api/pattern/${state.patternId}`);
  updateState(data);
  await fetchGeometry();
  blocks.refresh(data.version);
}

async function cacheV1Metrics() {
  if (!state.patternId) return;
  try {
    const data = await api(`/api/pattern/${state.patternId}?version=1`);
    state.v1Metrics = data.metrics;
    renderImpact($("impact"), state.v1Metrics, state.metrics);
  } catch (_) { /* impact card just stays off */ }
}

async function fetchGeometry() {
  const geo = await api(
    `/api/geometry/${state.patternId}?version=${state.version}`
  );
  state.geometry = geo;
  viewer.setGeometry(geo);
  player.setGeometry(geo);
}

// ---------------------------------------------------------------------------
// Settings panel (analysis thresholds)

async function loadConfigSchema() {
  if (state.configSchema) { renderSettingsPanel(); return; }
  try {
    state.configSchema = await api("/api/config");
    if (state.configSchema.machine) {
      Object.assign(state.machine, state.configSchema.machine);
    }
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
  direction_turn_deg: "Direction smoothness (° mean turn)",
  travel_efficiency_warn: "Travel efficiency warn (× nearest-first)",
};

const PRIORITY_INFO = {
  speed: "Machine time first: travel/order and trim issues surface first and loudest.",
  balanced: "Default: findings ranked by severity as measured.",
  quality: "Surface quality first: density, direction and stitch-length issues first.",
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
  // priority selector at the top
  const prioRow = document.createElement("div");
  prioRow.className = "setting-row priority-row";
  prioRow.innerHTML = `
    <label>Optimise for</label>
    <div class="priority-group">
      ${["speed", "balanced", "quality"].map((m) => `
        <button type="button" class="prio-btn${state.cfg.priority === m ? " active" : ""}" data-prio="${m}">${m}</button>`).join("")}
    </div>
    <div class="hint priority-hint">${PRIORITY_INFO[state.cfg.priority] || ""}</div>
  `;
  prioRow.querySelectorAll(".prio-btn").forEach((b) =>
    b.addEventListener("click", () => {
      state.cfg.priority = b.dataset.prio;
      renderSettingsPanel();
      onSettingChanged();
    }));
  panel.appendChild(prioRow);
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
  document.querySelectorAll("#settings-panel input[type=range]").forEach((s) => {
    state.cfg[s.dataset.key] = parseFloat(s.value);
  });
  viewer.setProblemJumps(state.jumps.filter(
    (j) => j.length_mm > state.cfg.long_jump_mm && !j.after_trim));
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
  $("findings-count").textContent =
    state.findings.length ? String(state.findings.length) : "clean";
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
    const savings = f.estimated_savings && f.estimated_savings.label
      ? `<span class="savings-badge">⚡ ${f.estimated_savings.label}</span>`
      : "";
    div.innerHTML = `
      <span class="sev ${f.severity}">${f.severity}</span>
      <h3>${f.title}</h3>
      <p>${f.detail}</p>
      ${caveat}
      <p><em>Impact: ${f.estimated_impact}</em></p>
      ${savings}
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
        if (e.target.checked) addOp(e.target.dataset.op, div);
        else removeOp(e.target.dataset.op, div);
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
    blocks.refresh(data.version);
  } catch (err) {
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

$("undo-btn").addEventListener("click", async () => {
  try {
    const data = await api(`/api/undo/${state.patternId}`, { method: "POST" });
    state.activeOps = [];
    renderFindings();
    updateState(data);
    await fetchGeometry();
    blocks.refresh(data.version);
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
    blocks.refresh(data.version);
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
// Canvas toolbar

document.querySelectorAll('input[name="view"]').forEach((r) =>
  r.addEventListener("change", (e) => {
    state.view = e.target.value;
    viewer.setView(state.view);
    setLegend();
  })
);

$("fabric-select").addEventListener("change", (e) => {
  viewer.setFabric(e.target.value);
});
$("zoom-in").addEventListener("click", () => viewer.zoomIn());
$("zoom-out").addEventListener("click", () => viewer.zoomOut());
$("zoom-fit").addEventListener("click", () => viewer.fit());
$("compare-btn").addEventListener("click", () => compare.enter());

function toggleHighlight(findingId, card) {
  if (state.highlight === findingId) {
    state.highlight = null;
    viewer.setHighlight(null);
    if (card) card.classList.remove("locating");
  } else {
    state.highlight = findingId;
    viewer.setHighlight(findingById(findingId));
    document.querySelectorAll(".finding").forEach((el) => el.classList.remove("locating"));
    if (card) card.classList.add("locating");
  }
}

function findingById(id) {
  return state.findings.find((f) => f.id === id) || null;
}

// shared overlay helpers used by the studio viewer
function locationBounds(locations) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  const stitchSegs = (state.geometry && state.geometry.segments || [])
    .filter((s) => s.type === "stitch");
  for (const loc of locations) {
    let pts = [];
    if (loc.type === "jump") pts = [[loc.from[0], loc.from[1]], [loc.to[0], loc.to[1]]];
    else if (loc.type === "stitch") pts = [[loc.from[0], loc.from[1]], [loc.to[0], loc.to[1]]];
    else if (loc.type === "density") pts = [[loc.x, loc.y]];
    else if (loc.type === "run") {
      // run locations carry no coordinates; resolve to the nth stitch segment
      const seg = stitchSegs[loc.run_index];
      if (seg) pts = seg.points;
    }
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
    // run locations (direction finding): overlay the whole run thick
    const runLocs = hl.locations.filter((l) => l.type === "run");
    if (runLocs.length) {
      const stitchSegs = (state.geometry && state.geometry.segments || [])
        .filter((s) => s.type === "stitch");
      ctx.strokeStyle = "#00b3ff";
      ctx.lineWidth = 3;
      for (const l of runLocs) {
        const seg = stitchSegs[l.run_index];
        if (!seg) continue;
        ctx.beginPath();
        ctx.moveTo(toX(seg.points[0][0]), toY(seg.points[0][1]));
        for (const [x, y] of seg.points.slice(1)) ctx.lineTo(toX(x), toY(y));
        ctx.stroke();
      }
    }
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
  const parts = [];
  if (state.view !== "thread") parts.push("✂ trims · ┄ jumps");
  if (state.view === "problems") parts.push("▬ untrimmed long jump");
  if (!parts.length) { legend.hidden = true; legend.textContent = ""; return; }
  legend.hidden = false;
  legend.textContent = parts.join("   ·   ");
}

// ---------------------------------------------------------------------------
// Verify (round-trip what-if)

$("verify-format").innerHTML =
  (["pes", "dst", "jef", "vp3", "exp", "u01", "pec", "xxx"])
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
  const b = data.baseline ? data.baseline.metrics : null;
  const row = (label, a, b_, c, warn) => `
    <tr${warn ? ' class="warnrow"' : ""}>
      <td>${label}</td><td>${a}</td><td>${b_}</td><td>${c}</td>
      ${warn ? `<td class="warn">⚠ ${warn}</td>` : "<td></td>"}
    </tr>`;
  // Real verdict: fixed re-import vs ORIGINAL re-import — the format's own
  // overhead (e.g. PES trim-jump pairs) appears in BOTH and cancels out.
  const fmt = data.format.toUpperCase();
  const fmtNote = b
    ? `<span class="hint">Format overhead on the untouched original: ` +
      `${b.jump_count - w.jump_count >= 0 ? "+" : ""}${b.jump_count - w.jump_count} jumps, ` +
      `${b.trims - w.trims >= 0 ? "+" : ""}${b.trims - w.trims} trims — ` +
      `added by the ${fmt} writer itself, not by your fixes.</span>`
    : "";
  const warns = [];
  if (b) {
    if (r.total_stitches !== b.total_stitches)
      warns.push(`${r.total_stitches > b.total_stitches ? "+" : ""}${r.total_stitches - b.total_stitches} stitches vs original`);
    if (r.jump_travel_mm > b.jump_travel_mm * 1.02 + 1)
      warns.push(`+${Math.round(r.jump_travel_mm - b.jump_travel_mm)} mm jump travel vs original`);
  } else {
    if (data.stitch_delta !== 0) warns.push(`${data.stitch_delta > 0 ? "+" : ""}${data.stitch_delta} stitches from re-import`);
  }
  out.innerHTML = `
    <table class="verify-table">
      <tr><th></th><th>Working</th><th>Original as ${fmt}</th><th>Fixed as ${fmt}</th><th></th></tr>
      ${row("Stitches", w.total_stitches.toLocaleString(),
            b ? b.total_stitches.toLocaleString() : "—",
            r.total_stitches.toLocaleString(),
            b && r.total_stitches !== b.total_stitches
              ? `Δ ${r.total_stitches - b.total_stitches > 0 ? "+" : ""}${r.total_stitches - b.total_stitches}` : null)}
      ${row("Jumps", w.jump_count, b ? b.jump_count : "—", r.jump_count,
            b && r.jump_count !== b.jump_count ? `Δ ${r.jump_count - b.jump_count > 0 ? "+" : ""}${r.jump_count - b.jump_count}` : null)}
      ${row("Trims", w.trims, b ? b.trims : "—", r.trims,
            b && r.trims !== b.trims ? `Δ ${r.trims - b.trims > 0 ? "+" : ""}${r.trims - b.trims}` : null)}
      ${row("Max stitch (mm)", w.max_stitch_mm, b ? b.max_stitch_mm : "—", r.max_stitch_mm)}
      ${row("Jump travel (mm)", w.jump_travel_mm, b ? b.jump_travel_mm : "—", r.jump_travel_mm)}
    </table>
    ${warns.length
      ? `<p class="caveat">⚠ vs the original as ${fmt}: ${warns.join("; ")}.</p>`
      : '<p class="ok">✓ The fixed design sews with no extra stitches or travel vs the original.</p>'}
    ${fmtNote}
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

// ---------------------------------------------------------------------------
// Printable report

$("report-btn").addEventListener("click", buildReport);
$("report-print").addEventListener("click", () => window.print());
$("report-close").addEventListener("click", () => {
  $("report-section").hidden = true;
  $("app-section").hidden = false;
});

async function buildReport() {
  let orig = state.originalGeometry;
  let origMetrics = state.v1Metrics;
  if (!orig || !origMetrics) {
    const [g, p] = await Promise.all([
      api(`/api/geometry/${state.patternId}?version=1`),
      api(`/api/pattern/${state.patternId}?version=1`),
    ]);
    orig = g; origMetrics = p.metrics;
    state.originalGeometry = orig; state.v1Metrics = origMetrics;
  }
  const m = state.metrics;
  const name = (state.filename || "design");
  const rows = [
    ["Stitches", (origMetrics.total_stitches || 0).toLocaleString(),
     (m.total_stitches || 0).toLocaleString()],
    ["Colours", String((origMetrics.color_changes || 0) + 1),
     String((m.color_changes || 0) + 1)],
    ["Jumps", String(origMetrics.jump_count || 0), String(m.jump_count || 0)],
    ["Jump travel", `${origMetrics.jump_travel_mm || 0} mm`, `${m.jump_travel_mm || 0} mm`],
    ["Trims", String(origMetrics.trims || 0), String(m.trims || 0)],
    ["Max stitch", `${origMetrics.max_stitch_mm || 0} mm`, `${m.max_stitch_mm || 0} mm`],
    ["Size", `${origMetrics.extents?.width_mm || 0} × ${origMetrics.extents?.height_mm || 0} mm`,
     `${m.extents?.width_mm || 0} × ${m.extents?.height_mm || 0} mm`],
  ].map(([k, a, b]) => `<tr><th>${k}</th><td>${a}</td><td>${b}</td></tr>`).join("");

  const findingsHtml = state.findings.length
    ? state.findings.map((f) => `
        <div class="finding">
          <span class="sev ${f.severity}">${f.severity}</span>
          <h3>${f.title}</h3>
          <p>${f.detail}</p>
          ${f.estimated_savings && f.estimated_savings.label
            ? `<span class="savings-badge">⚡ ${f.estimated_savings.label}</span>` : ""}
        </div>`).join("")
    : "<p><em>No issues found — this design looks clean.</em></p>";

  $("report-body").innerHTML = `
    <h2>Stitch Doctor report</h2>
    <p class="report-meta">${escapeHtml(name)} · ${new Date().toLocaleString()} ·
       version ${state.version} of ${state.versions.length}</p>
    <div class="report-grid">
      <div><h3>Original</h3><canvas id="report-orig" width="640" height="480"></canvas></div>
      <div><h3>Proposed</h3><canvas id="report-prop" width="640" height="480"></canvas></div>
    </div>
    <table>
      <tr><th></th><th>Original</th><th>Proposed</th></tr>
      ${rows}
    </table>
    <h2>Findings</h2>
    ${findingsHtml}
  `;
  drawReportCanvas($("report-orig"), orig);
  drawReportCanvas($("report-prop"), state.geometry);
  $("app-section").hidden = true;
  $("report-section").hidden = false;
}

function drawReportCanvas(canvasEl, geo) {
  if (!geo || geo.empty || !geo.segments.length) return;
  const ctx = canvasEl.getContext("2d");
  const W = canvasEl.width, H = canvasEl.height;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, W, H);
  const pad = 14;
  const scale = Math.min((W - pad * 2) / geo.extents.width_mm,
                         (H - pad * 2) / geo.extents.height_mm);
  const ox = (W - geo.extents.width_mm * scale) / 2 - geo.extents.min_x * scale;
  // pyembroidery's +y is down, same as canvas — no flip needed
  const oy = (H - geo.extents.height_mm * scale) / 2 - geo.extents.min_y * scale;
  ctx.save();
  ctx.translate(ox, oy);
  ctx.scale(scale, scale);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const threads = geo.threads || [];
  const width = 0.4 / scale * Math.max(1, Math.min(3, scale / 3));
  for (const seg of geo.segments) {
    if (seg.type === "jump") continue;
    const pts = seg.points;
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.strokeStyle = threads[seg.color] || "#333";
    ctx.lineWidth = width;
    ctx.stroke();
  }
  ctx.restore();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// expose internals for debugging / QA tooling
window.__sdMachine = state.machine;
window.__sdViewer = viewer;
window.__sdState = state;

function drawCanvas() { /* the viewer schedules its own redraws */ }

// ---------------------------------------------------------------------------
// Boot: ?pattern=<id> reopens an existing session (reload / shareable link)

(async function boot() {
  const pid = new URLSearchParams(location.search).get("pattern");
  if (!pid) return;
  try {
    const data = await api(`/api/pattern/${pid}`);
    state.patternId = pid;
    state.cfg = data.cfg;
    state.filename = data.filename;
    enterApp(data);
  } catch (err) {
    $("upload-error").textContent = "Could not reopen pattern: " + err.message;
  }
})();
