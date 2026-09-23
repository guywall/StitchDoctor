"""Single-page web UI for Stitch Doctor (server-rendered shell + ES modules)."""
from __future__ import annotations

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>@@APP_NAME@@</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🧵</text></svg>">
<link rel="stylesheet" href="/static/style.css?v=12">
</head>
<body>
<header>
  <h1>&#129526; @@APP_NAME@@</h1>
  <p class="tagline">Embroidery file doctor &mdash; diagnosis before treatment.</p>
</header>

<main>
  <section id="upload-section">
    <h2>Upload a design</h2>
    <p>PES, DST, JEF, VP3, EXP and more. Your original file is never modified.</p>
    <input type="file" id="file" accept=".pes,.dst,.jef,.vp3,.exp,.pec,.u01,.xxx" />
    <button id="upload-btn" class="primary">Analyse</button>
    <div id="upload-error" class="error"></div>
  </section>

  <section id="app-section" hidden>
    <div class="workspace">
      <div class="col left">
        <div class="panel-head">
          <h2>Findings</h2>
          <span id="findings-count" class="pill"></span>
        </div>
        <p class="hint">Tick a fix to apply it. Click a finding to highlight it on the canvas.</p>
        <div id="findings"></div>

        <details class="settings" id="blocks-panel">
          <summary>Sew order <span id="blocks-travel" class="pill subtle"></span></summary>
          <p class="hint">Drag blocks to reorder. Hover to locate on canvas; the dashed
             line previews the needle-up travel each move creates.</p>
          <div id="blocks-list"></div>
          <div class="settings-actions">
            <button id="blocks-refresh">Refresh</button>
            <span class="hint">Every change becomes a new reversible version.</span>
          </div>
        </details>

        <details class="settings">
          <summary>Analysis settings (thresholds)</summary>
          <div id="settings-panel"></div>
          <div class="settings-actions">
            <label class="inline"><input type="checkbox" id="auto-analyse" checked> Re-analyse automatically</label>
            <button id="reanalyse-btn">Re-analyse now</button>
            <button id="settings-reset">Reset defaults</button>
          </div>
        </details>

        <details class="verify">
          <summary>Export round-trip check</summary>
          <p class="hint">Applies the selected fixes to a copy, writes it out in the chosen
             format, re-imports that file, and compares — what the machine will actually get.</p>
          <div class="export-row">
            <select id="verify-format"></select>
            <button id="verify-btn">Check round-trip</button>
          </div>
          <div id="verify-out"></div>
        </details>

        <h2>Export</h2>
        <div class="export-row">
          <select id="export-format">
            @@FORMAT_OPTIONS@@
          </select>
          <button id="export-btn" class="primary">Download</button>
        </div>
        <div class="history-row">
          <button id="undo-btn" disabled>Undo</button>
          <button id="reset-btn" disabled>Reset to original</button>
          <button id="report-btn" title="Printable before/after report">Report</button>
        </div>
      </div>

      <div class="col right">
        <div class="toolbar">
          <div class="view-toggle" role="group" aria-label="View mode">
            <label><input type="radio" name="view" value="thread" checked> Thread</label>
            <label><input type="radio" name="view" value="paths"> Paths</label>
            <label><input type="radio" name="view" value="problems"> Problems</label>
          </div>
          <select id="fabric-select" title="Fabric background">
            <option value="white">White fabric</option>
            <option value="dark">Dark garment</option>
            <option value="denim">Denim</option>
            <option value="cream">Cream linen</option>
          </select>
          <div class="spacer"></div>
          <button id="zoom-out" title="Zoom out">−</button>
          <button id="zoom-in" title="Zoom in">+</button>
          <button id="zoom-fit" title="Fit design">Fit</button>
          <button id="compare-btn" title="Before / after wipe">Compare</button>
        </div>

        <div class="canvas-wrap">
          <canvas id="canvas"></canvas>
          <div id="tooltip" hidden></div>
          <div id="compare-bar" hidden>
            <span>Original</span>
            <input type="range" id="wipe-slider" min="0" max="100" value="50">
            <span>Proposed</span>
            <button id="compare-mode" title="Wipe direction">⇔</button>
            <button id="compare-close" title="Exit compare">✕</button>
          </div>
          <div id="player" class="player" hidden>
            <button id="play-btn" title="Play stitch-out (space)">▶</button>
            <button id="stop-btn" title="Back to start">⏮</button>
            <input type="range" id="scrub" min="0" max="1000" value="0">
            <select id="speed" title="Playback speed">
              <option value="0.5">×0.5</option>
              <option value="1" selected>×1</option>
              <option value="2">×2</option>
              <option value="4">×4</option>
              <option value="8">×8</option>
            </select>
            <span id="player-time" class="time">0:00 / 0:00</span>
            <label class="inline" title="Sound effects"><input type="checkbox" id="sound"> 🔊</label>
          </div>
        </div>

        <div id="impact" class="impact" hidden></div>
        <div id="legend" class="legend" hidden></div>
        <div id="stats" class="stats"></div>
        <div id="explain-box" hidden>
          <h3>Why this recommendation?</h3>
          <p id="explain-text"></p>
        </div>
      </div>
    </div>
  </section>

  <section id="report-section" hidden>
    <div class="report-actions no-print">
      <button id="report-print" class="primary">Print / save PDF</button>
      <button id="report-close">Back to editor</button>
    </div>
    <div id="report-body"></div>
  </section>
</main>

<script type="module" src="/static/app.js?v=12"></script>
</body>
</html>"""


def page(app_name: str, export_formats: list) -> str:
    """Token replacement (not str.format — the template contains literal CSS/JS braces)."""
    options = "".join(
        f'<option value="{f}">{f.upper()}</option>' for f in export_formats
    )
    return (PAGE
            .replace("@@APP_NAME@@", app_name)
            .replace("@@FORMAT_OPTIONS@@", options))
