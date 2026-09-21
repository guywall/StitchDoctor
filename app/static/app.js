/* Stitch Doctor front-end: upload → findings → fixes → preview → export. */
"use strict";

const state = {
  patternId: null,
  version: null,
  versions: [],
  geometry: null,
  summary: null,
  findings: [],
  view: "thread",
  highlight: null,
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
      detail = body.detail || detail;
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
    enterApp(data);
  } catch (err) {
    $("upload-error").textContent = err.message;
  } finally {
    $("upload-btn").disabled = false;
    $("upload-btn").textContent = "Analyse";
  }
});

// ---------------------------------------------------------------------------
// Rendering state into UI

function enterApp(data) {
  $("upload-section").hidden = true;
  $("app-section").hidden = false;
  updateState(data);
  fetchGeometry();
}

function updateState(data) {
  state.version = data.version;
  state.versions = data.versions || [data.version];
  state.summary = data.metrics || {};
  state.findings = data.findings || [];
  renderFindings();
  renderStats();
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
// Findings list

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
    const caveat = f.caveats && f.caveats.length
      ? `<p class="caveat">⚠ ${f.caveats.join(" ")}</p>`
      : "";
    div.innerHTML = `
      <span class="sev ${f.severity}">${f.severity}</span>
      <h3>${f.title}</h3>
      <p>${f.detail}</p>
      ${caveat}
      <p><em>Impact: ${f.estimated_impact}</em></p>
      <label><input type="checkbox" data-op="${f.id}"> Apply fix</label>
      <button class="explain-btn" data-finding="${f.id}">Why?</button>
    `;
    box.appendChild(div);

    div.querySelector('input[type="checkbox"]').addEventListener("change", (e) => {
      const op = e.target.dataset.op;
      const params = opParams(f);
      if (e.target.checked) applyOp(op, params);
      // unchecking = undo (versions make this safe)
      else undo();
    });
    div.querySelector(".explain-btn").addEventListener("click", () => explainFinding(f.id));
  }
}

// op parameters per finding — extendable
function opParams(f) {
  return {};
}

async function applyOp(op, params) {
  try {
    const data = await api(`/api/fix/${state.patternId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op, params }),
    });
    updateState(data);
    await fetchGeometry();
  } catch (err) {
    alert(`Fix failed: ${err.message}`);
    renderFindings(); // uncheck the box
  }
}

async function undo() {
  try {
    const data = await api(`/api/undo/${state.patternId}`, { method: "POST" });
    updateState(data);
    await fetchGeometry();
  } catch (err) {
    $("upload-error").textContent = err.message;
  }
}

$("undo-btn").addEventListener("click", undo);
$("reset-btn").addEventListener("click", async () => {
  try {
    const data = await api(`/api/reset/${state.patternId}`, { method: "POST" });
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

function drawCanvas() {
  const canvas = $("canvas");
  const ctx = canvas.getContext("2d");
  const geo = state.geometry;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!geo || geo.empty || !geo.segments.length) {
    ctx.fillStyle = "#999";
    ctx.font = "16px system-ui";
    ctx.fillText("No stitch data", 20, 30);
    return;
  }

  // fit extents with padding; Y flips (embroidery Y grows downward)
  const e = geo.extents;
  const pad = 20;
  const scale = Math.min(
    (canvas.width - pad * 2) / Math.max(e.width_mm, 1),
    (canvas.height - pad * 2) / Math.max(e.height_mm, 1)
  );
  const ox = pad - e.min_x * scale;
  const oy = canvas.height - pad + e.min_y * scale;
  const toX = (x) => ox + x * scale;
  const toY = (y) => oy - y * scale;

  // colour per block
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
      if (state.view === "problems" && state.highlight &&
          state.highlight.id !== "canvas-all") {
        // dim non-highlighted stitches when a finding is highlighted
      }
      ctx.strokeStyle = state.view === "thread" ? colorFor(seg.color) : "#333";
      ctx.setLineDash([]);
      ctx.lineWidth = state.view === "thread" ? 1.4 : 1;
    }
    ctx.beginPath();
    const pts = seg.points;
    ctx.moveTo(toX(pts[0][0]), toY(pts[0][1]));
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(toX(pts[i][0]), toY(pts[i][1]));
    }
    ctx.stroke();
  }
  ctx.setLineDash([]);

  // trim markers
  if (showPaths) {
    ctx.fillStyle = "#b3424a";
    for (const t of geo.trims) {
      ctx.beginPath();
      ctx.moveTo(toX(t.x) - 4, toY(t.y) - 4);
      ctx.lineTo(toX(t.x) + 4, toY(t.y) + 4);
      ctx.moveTo(toX(t.x) + 4, toY(t.y) - 4);
      ctx.lineTo(toX(t.x) - 4, toY(t.y) + 4);
      ctx.strokeStyle = "#b3424a";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }

  // problems overlay
  if (state.view === "problems") {
    drawProblems(ctx, toX, toY);
  }

  // start point marker
  if (geo.segments.length) {
    const first = geo.segments[0].points[0];
    ctx.fillStyle = "#3a7d44";
    ctx.beginPath();
    ctx.arc(toX(first[0]), toY(first[1]), 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.font = "bold 8px system-ui";
    ctx.fillText("S", toX(first[0]) - 2.5, toY(first[1]) + 3);
  }
}

function drawProblems(ctx, toX, toY) {
  // highlight jumps (long ones in red)
  const longJumps = (state.summary.jumps || []).filter(
    (j) => j.length_mm > 12
  );
  ctx.strokeStyle = "rgba(255,0,0,0.75)";
  ctx.lineWidth = 2;
  for (const j of longJumps) {
    ctx.beginPath();
    ctx.moveTo(toX(j.from[0]), toY(j.from[1]));
    ctx.lineTo(toX(j.to[0]), toY(j.to[1]));
    ctx.stroke();
  }
  // density hotspots as circles
  for (const h of state.summary.density_hotspots || []) {
    ctx.strokeStyle = "rgba(179,66,74,0.6)";
    ctx.beginPath();
    ctx.arc(toX(h.x_mm), toY(h.y_mm), 15, 0, Math.PI * 2);
    ctx.stroke();
  }
}

// ---------------------------------------------------------------------------
// Stats + export

function renderStats() {
  const m = state.summary;
  if (!m || !m.total_stitches && !m.extents) return;
  const ex = m.extents || {};
  $("stats").textContent =
    `${m.total_stitches.toLocaleString()} stitches · ` +
    `${m.color_changes + 1} colours · ${m.jump_count} jumps ` +
    `(${m.jump_travel_mm} mm travel) · ${m.trims} trims\n` +
    `${ex.width_mm} × ${ex.height_mm} mm · max stitch ${m.max_stitch_mm} mm`;
}

$("export-btn").addEventListener("click", () => {
  if (!state.patternId) return;
  const fmt = $("export-format").value;
  window.location = `/api/export/${state.patternId}?format=${fmt}`;
});
