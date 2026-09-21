"""Single-page web UI for Stitch Doctor (server-rendered shell + vanilla JS)."""
from __future__ import annotations

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>@@APP_NAME@@</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🧵</text></svg>">
<link rel="stylesheet" href="/static/style.css?v=5">
</head>
<body>
<header>
  <h1>&#129526; @@APP_NAME@@</h1>
  <p class="tagline">Embroidery file doctor &mdash; diagnosis before treatment.</p>
</header>

<main>
  <section id="upload-section">
    <h2>1. Upload a design</h2>
    <p>PES, DST, JEF, VP3, EXP and more. Your original file is never modified.</p>
    <input type="file" id="file" accept=".pes,.dst,.jef,.vp3,.exp,.pec,.u01,.xxx" />
    <button id="upload-btn" class="primary">Analyse</button>
    <div id="upload-error" class="error"></div>
  </section>

  <section id="app-section" hidden>
    <div class="cols">
      <div class="col left">
        <h2>2. Findings</h2>
        <p class="hint">Tick a fix to apply it. Click a finding to highlight it on the canvas.</p>
        <div id="findings"></div>

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

        <h2>3. Export</h2>
        <div class="export-row">
          <select id="export-format">
            @@FORMAT_OPTIONS@@
          </select>
          <button id="export-btn">Download</button>
        </div>
        <div class="history-row">
          <button id="undo-btn" disabled>Undo</button>
          <button id="reset-btn" disabled>Reset to original</button>
        </div>
      </div>

      <div class="col right">
        <div class="view-toggle">
          <label><input type="radio" name="view" value="thread" checked> Thread preview</label>
          <label><input type="radio" name="view" value="paths"> Stitch paths</label>
          <label><input type="radio" name="view" value="problems"> Problems</label>
          <button id="refresh-preview" title="Redraw from the current pattern">↻ Redraw</button>
        </div>
        <canvas id="canvas" width="640" height="640"></canvas>
        <div id="legend" class="legend" hidden></div>
        <div id="stats"></div>
        <div id="explain-box" hidden>
          <h3>Why this recommendation?</h3>
          <p id="explain-text"></p>
        </div>
      </div>
    </div>
  </section>
</main>

<script src="/static/app.js?v=5"></script>
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
