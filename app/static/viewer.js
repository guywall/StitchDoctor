/* Stitch Doctor viewer: zoom/pan/hover canvas with Path2D prebake.
   One module owns all drawing so the player, compare and blocks editor
   can layer behaviour on top without stepping on each other. */
"use strict";

export const FABRICS = {
  white:  { css: "#ffffff", dark: false, stitchContrast: 1.0 },
  cream:  { css: "#f5efdf", dark: false, stitchContrast: 1.0 },
  denim:  { css: "#33506e", dark: true,  stitchContrast: 1.15 },
  dark:   { css: "#26221e", dark: true,  stitchContrast: 1.25 },
};

const PAD = 24;

export class SDViewer {
  constructor(canvas, tooltip) {
    this.canvas = canvas;
    this.tooltip = tooltip;
    this.ctx = canvas.getContext("2d");
    this.geo = null;            // current geometry
    this.chunkPaths = [];       // [{path, color, cssColor, dark, light, width, jump}]
    this.trimMarkers = [];      // screen-space markers (rebuilt on prebake)
    this.threads = [];
    this.fabric = "white";
    this.view = "thread";       // thread | paths | problems
    this.highlight = null;      // finding object or null
    this.problemJumps = [];     // world-mm jump lines for the problems view
    this.progress = { enabled: false, stitch: Infinity };
    this.wipe = null;           // {pct 0..100, vertical:false, otherGeo}
    this.blockPreview = [];     // [[x0,y0,x1,y1], ...] world mm
    this.scale = 1;             // CSS px per world mm at prebake (fit)
    this.zoom = 1;              // user zoom multiplier over fit
    this.tx = 0; this.ty = 0;   // pan in CSS px (eased into transform)
    this._needRebake = false;
    this._raf = 0;
    this.onHover = null;        // (info|null, mx, my)
    this.onClick = null;        // (info)
    this._bindEvents();
    this._resize();
  }

  // ---------------------------------------------------------------- events
  _bindEvents() {
    const c = this.canvas;
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const rect = c.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const factor = Math.exp(-e.deltaY * 0.0015);
      this._zoomAt(mx, my, factor);
    }, { passive: false });

    let dragging = false, moved = false, lx = 0, ly = 0;
    c.addEventListener("pointerdown", (e) => {
      dragging = true; moved = false; lx = e.clientX; ly = e.clientY;
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      const rect = c.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      if (dragging) {
        const dx = e.clientX - lx, dy = e.clientY - ly;
        if (moved || Math.abs(dx) + Math.abs(dy) > 3) {
          moved = true;
          this.tx += dx; this.ty += dy;
          lx = e.clientX; ly = e.clientY;
          this._scheduleDraw();
        }
      } else {
        this._scheduleHover(mx, my);
      }
    });
    c.addEventListener("pointerup", (e) => {
      dragging = false;
      if (!moved && this.onClick) {
        const rect = c.getBoundingClientRect();
        const info = this.getHoverInfo(e.clientX - rect.left, e.clientY - rect.top);
        this.onClick(info);
      }
    });
    c.addEventListener("pointerleave", () => {
      if (this.tooltip) this.tooltip.hidden = true;
      if (this.onHover) this.onHover(null, 0, 0);
    });
    new ResizeObserver(() => this._resize()).observe(c);
  }

  _zoomAt(mx, my, factor) {
    const nz = Math.min(60, Math.max(0.5, this.zoom * factor));
    factor = nz / this.zoom;
    // keep the world point under the cursor fixed:
    // screen = base(mx,my) + zoom*(scale*(world) - fitOffset) … simplify by
    // adjusting pan so the anchor point stays put.
    const cx = this.canvas.width / (window.devicePixelRatio || 1) / 2;
    const cy = this.canvas.height / (window.devicePixelRatio || 1) / 2;
    this.tx = mx - cx - (mx - cx - this.tx) * factor;
    this.ty = my - cy - (my - cy - this.ty) * factor;
    this.zoom = nz;
    this._scheduleDraw();
  }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.clientWidth || 600;
    const h = Math.max(380, Math.round(w * 0.82));
    if (!this.canvas.style.height) this.canvas.style.height = h + "px";
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(parseFloat(this.canvas.style.height) * dpr);
    this._needRebake = true;   // fit scale changes with size
    this._scheduleDraw();
  }

  _scheduleHover(mx, my) {
    if (this._hoverRaf) return;
    this._hoverRaf = requestAnimationFrame(() => {
      this._hoverRaf = 0;
      const info = this.getHoverInfo(mx, my);
      if (this.tooltip) {
        if (info) {
          this.tooltip.innerHTML = info.html;
          this.tooltip.hidden = false;
          this.tooltip.style.left = mx + "px";
          this.tooltip.style.top = my + "px";
        } else {
          this.tooltip.hidden = true;
        }
      }
      if (this.onHover) this.onHover(info, mx, my);
    });
  }

  // ---------------------------------------------------------------- data in
  setGeometry(geo) {
    this.geo = geo;
    this.threads = (geo && geo.threads) || [];
    this._needRebake = true;
    this.zoom = 1; this.tx = 0; this.ty = 0;   // refit on new data
    this._scheduleDraw();
  }

  setFabric(name) { this.fabric = name; this._scheduleDraw(); }
  setView(v) { this.view = v; this._scheduleDraw(); }
  setHighlight(f) { this.highlight = f; this._scheduleDraw(); }
  setProblemJumps(jumps) { this.problemJumps = jumps || []; this._scheduleDraw(); }
  setBlockPreview(jumps) { this.blockPreview = jumps || []; this._scheduleDraw(); }
  setProgress(stitch) {
    this.progress.enabled = true;
    this.progress.stitch = stitch;
    this._scheduleDraw();
  }
  clearProgress() { this.progress.enabled = false; this._scheduleDraw(); }
  setWipe(w) { this.wipe = w; this._scheduleDraw(); }

  invalidateFabricOnly() { this._needRebake = true; this._scheduleDraw(); }

  // ---------------------------------------------------------------- prebake
  _fitTransform() {
    const cssW = this.canvas.width / (window.devicePixelRatio || 1);
    const cssH = this.canvas.height / (window.devicePixelRatio || 1);
    const ex = this.geo && this.geo.extents;
    let scale = 2.5; // px per mm fallback
    if (ex && ex.width_mm > 0 && ex.height_mm > 0) {
      scale = Math.min((cssW - PAD * 2) / ex.width_mm,
                       (cssH - PAD * 2) / ex.height_mm);
    }
    // world origin → CSS px at fit zoom
    const ox = (cssW - ex.width_mm * scale) / 2 - ex.min_x * scale;
    const oy = cssH - (cssH - ex.height_mm * scale) / 2 + ex.min_y * scale;
    return { scale, ox, oy, cssW, cssH };
  }

  _prebake() {
    // Build Path2D per segment at the FIT zoom scale. Paths are zoom-agnostic:
    // the ctx transform scales them, and stroke width compensates via lineWidth.
    this.chunkPaths = [];
    this.trimMarkers = [];
    if (!this.geo || this.geo.empty) return;
    const t = this._fitTransform();
    this.scale = t.scale;
    const inv = 1 / t.scale;
    for (const seg of this.geo.segments) {
      const pts = seg.points;
      if (!pts || pts.length < 2) continue;
      const p = new Path2D();
      p.moveTo(pts[0][0] * t.scale, -pts[0][1] * t.scale);
      for (let i = 1; i < pts.length; i++) {
        p.lineTo(pts[i][0] * t.scale, -pts[i][1] * t.scale);
      }
      const isJump = seg.type === "jump";
      this.chunkPaths.push({
        path: p,
        colorIndex: seg.color,
        jump: isJump,
        trim: !!seg.trim,
        stitchStart: seg.stitch_start ?? null,
        stitchEnd: seg.stitch_end ?? null,
        stitchAt: seg.stitch_at ?? null,
        // world-length in mm, for sew-time estimates
        lengthMm: isJump ? seg.length_mm
          : _polylineLengthMm(pts),
      });
    }
    for (const tr of this.geo.trims || []) {
      this.trimMarkers.push({
        x: tr.x * t.scale, y: -tr.y * t.scale,
        stitchAt: tr.stitch_at ?? null,
      });
    }
    this._fit = t;
  }

  // ---------------------------------------------------------------- drawing
  _scheduleDraw() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = 0;
      if (this._needRebake) { this._needRebake = false; this._prebake(); }
      this._draw();
    });
  }

  _draw() {
    const ctx = this.ctx;
    const dpr = window.devicePixelRatio || 1;
    const cssW = this.canvas.width / dpr, cssH = this.canvas.height / dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const fab = FABRICS[this.fabric] || FABRICS.white;
    ctx.fillStyle = fab.css;
    ctx.fillRect(0, 0, cssW, cssH);

    if (!this.geo || this.geo.empty || !this.chunkPaths.length) {
      ctx.fillStyle = fab.dark ? "#998" : "#999";
      ctx.font = "16px system-ui";
      ctx.fillText("No stitch data", 20, 30);
      return;
    }

    const t = this._fit;
    const z = this.zoom;
    // base transform: fit (scale, ox, oy) then user zoom around centre, then pan
    const cx = cssW / 2, cy = cssH / 2;
    const drawAll = (extra = null) => {
      ctx.save();
      ctx.translate(this.tx, this.ty);
      ctx.translate(cx, cy);
      ctx.scale(z, z);
      ctx.translate(-cx, -cy);
      if (extra) extra();
      ctx.restore();
    };

    const paint = () => {
      this._paintPattern(ctx, t);
    };

    if (this.wipe && this.wipe.otherGeo) {
      // left/top = proposed (this.geo), right/bottom = original (otherGeo)
      const vert = this.wipe.vertical;
      const cut = vert ? cssH * (this.wipe.pct / 100) : cssW * (this.wipe.pct / 100);
      const savedGeo = this.geo, savedPaths = this.chunkPaths,
            savedTrims = this.trimMarkers, savedScale = this.scale, savedFit = this._fit;
      // draw original on the right/bottom half
      this._swapToOther();
      drawAll(() => {
        ctx.save();
        ctx.beginPath();
        if (vert) ctx.rect(0, cut, cssW, cssH - cut);
        else ctx.rect(cut, 0, cssW - cut, cssH);
        ctx.clip();
        this._paintPattern(ctx, this._fit);
        ctx.restore();
      });
      // draw proposed on the left/top half
      this.geo = savedGeo; this.chunkPaths = savedPaths;
      this.trimMarkers = savedTrims; this.scale = savedScale; this._fit = savedFit;
      drawAll(() => {
        ctx.save();
        ctx.beginPath();
        if (vert) ctx.rect(0, 0, cssW, cut);
        else ctx.rect(0, 0, cut, cssH);
        ctx.clip();
        paint();
        ctx.restore();
      });
      // divider
      ctx.save();
      ctx.strokeStyle = "#b3424a";
      ctx.lineWidth = 2;
      ctx.setLineDash([8, 5]);
      ctx.beginPath();
      if (vert) { ctx.moveTo(0, cut); ctx.lineTo(cssW, cut); }
      else { ctx.moveTo(cut, 0); ctx.lineTo(cut, cssH); }
      ctx.stroke();
      ctx.restore();
    } else {
      drawAll(paint);
    }

    // block reorder travel preview (world → screen by hand)
    if (this.blockPreview.length) {
      ctx.save();
      ctx.strokeStyle = "rgba(125,60,152,0.9)";
      ctx.lineWidth = 2;
      ctx.setLineDash([7, 5]);
      for (const [x0, y0, x1, y1] of this.blockPreview) {
        const ax = this._worldToCssX(x0, t), ay = this._worldToCssY(y0, t);
        const bx = this._worldToCssX(x1, t), by = this._worldToCssY(y1, t);
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
        _arrowHead(ctx, ax, ay, bx, by);
      }
      ctx.restore();
    }

    // finding highlight overlay
    if (this.highlight) this._drawHighlight(ctx, t);
  }

  _swapToOther() {
    // load the compare-partner prebake (set up by beginCompare)
    const o = this._otherBaked;
    if (!o) return;
    this.geo = o.geo; this.chunkPaths = o.paths; this.trimMarkers = o.trims;
    this.scale = o.scale; this._fit = o.fit;
  }

  beginCompare(otherGeo) {
    // Save the proposed pattern's prebake, then bake the original with the
    // SAME fit transform so both halves align pixel-perfectly.
    if (!this._fit) this._prebake();
    this._otherBaked = {
      geo: this.geo, paths: this.chunkPaths, trims: this.trimMarkers,
      fit: this._fit, scale: this.scale,
    };
    this._prebakeOther(otherGeo);
  }

  _prebakeOther(otherGeo) {
    // prebake otherGeo with the SAME fit transform so both halves align
    const t = this._fit;
    const paths = [];
    const trims = [];
    for (const seg of otherGeo.segments) {
      const pts = seg.points;
      if (!pts || pts.length < 2) continue;
      const p = new Path2D();
      p.moveTo(pts[0][0] * t.scale, -pts[0][1] * t.scale);
      for (let i = 1; i < pts.length; i++) {
        p.lineTo(pts[i][0] * t.scale, -pts[i][1] * t.scale);
      }
      paths.push({ path: p, colorIndex: seg.color, jump: seg.type === "jump",
                   stitchStart: seg.stitch_start ?? null,
                   stitchEnd: seg.stitch_end ?? null,
                   stitchAt: seg.stitch_at ?? null,
                   lengthMm: seg.type === "jump" ? (seg.length_mm || 0)
                     : _polylineLengthMm(pts) });
    }
    for (const tr of otherGeo.trims || []) {
      trims.push({ x: tr.x * t.scale, y: -tr.y * t.scale, stitchAt: tr.stitch_at ?? null });
    }
    this._otherBaked = {
      geo: otherGeo, paths, trims,
      fit: t, scale: t.scale,
    };
  }

  _worldToCssX(x, t) {
    // world → fit px → user transform (tx, zoom about centre)
    const dpr = window.devicePixelRatio || 1;
    const cssW = this.canvas.width / dpr;
    const cx = cssW / 2;
    const fx = x * t.scale + t.ox;
    return this.tx + cx + (fx - cx) * this.zoom;
  }
  _worldToCssY(y, t) {
    const dpr = window.devicePixelRatio || 1;
    const cssH = this.canvas.height / dpr;
    const cy = cssH / 2;
    const fy = -y * t.scale + t.oy;
    return this.ty + cy + (fy - cy) * this.zoom;
  }

  _paintPattern(ctx, t) {
    const fab = FABRICS[this.fabric] || FABRICS.white;
    const z = this.zoom * t.scale;             // css px per world mm (effective)
    const isThreadView = this.view === "thread";
    // lineWidth lives in prebaked path units (fit-px); ctx multiplies by
    // `zoom`, so screen px = width × zoom. Keep a thread's physical
    // ~0.55 mm width, but never let it fall under ~1.4 screen px when
    // zoomed out, or thin designs vanish.
    const baseWidth = (isThreadView ? 0.55 : 0.38) * t.scale;
    const stitchWidth = Math.max(baseWidth, 1.4 / this.zoom);
    const progress = this.progress.enabled ? this.progress.stitch : Infinity;
    // prebaked paths live in fit-px space; land them on the canvas
    ctx.save();
    ctx.translate(t.ox, t.oy);

    for (const cp of this.chunkPaths) {
      if (cp.jump) {
        if (!(this.view === "paths" || this.view === "problems")) continue;
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = "rgba(150,150,150,0.75)";
        ctx.lineWidth = 1 / this.zoom;
      } else {
        if (progress < (cp.stitchStart ?? Infinity)) continue;
        ctx.setLineDash([]);
        const hex = this.threads[cp.colorIndex] || "#333";
        ctx.lineWidth = stitchWidth / this.zoom;
        // during playback, clip the run to the stitches sewn so far
        let path = cp.path;
        if (progress <= (cp.stitchEnd ?? -1) && cp.stitchStart != null) {
          const seg = this._segByStart.get(cp.stitchStart);
          if (seg) {
            const n = Math.min(progress - cp.stitchStart + 1, seg.points.length);
            if (n < 2) continue;   // one point sewn — needle dot shows it
            path = new Path2D();
            path.moveTo(seg.points[0][0] * t.scale, -seg.points[0][1] * t.scale);
            for (let i = 1; i < n; i++) {
              path.lineTo(seg.points[i][0] * t.scale, -seg.points[i][1] * t.scale);
            }
          }
        }
        if (isThreadView && !fab.dark) {
          // two-pass sheen: dark under-edge, light core
          ctx.strokeStyle = _shade(hex, -0.28);
          ctx.lineCap = "round"; ctx.lineJoin = "round";
          ctx.stroke(path);
          ctx.lineWidth *= 0.62;
          ctx.strokeStyle = _shade(hex, 0.18);
          ctx.stroke(path);
          continue;
        }
        ctx.strokeStyle = fab.dark ? _shade(hex, 0.12) : hex;
        ctx.lineCap = "round"; ctx.lineJoin = "round";
        ctx.stroke(path);
        if (isThreadView && fab.dark) {
          ctx.lineWidth *= 0.55;
          ctx.strokeStyle = _shade(hex, 0.3);
          ctx.stroke(path);
        }
        continue;
      }
      ctx.stroke(cp.path);
    }
    ctx.setLineDash([]);

    // untrimmed long jumps (problems view) — from metrics, world mm
    if (this.view === "problems" && this.problemJumps.length) {
      ctx.save();
      ctx.strokeStyle = "rgba(255,40,40,0.85)";
      ctx.lineWidth = 2.5 / this.zoom;
      for (const j of this.problemJumps) {
        ctx.beginPath();
        ctx.moveTo(j.from[0] * t.scale, -j.from[1] * t.scale);
        ctx.lineTo(j.to[0] * t.scale, -j.to[1] * t.scale);
        ctx.stroke();
      }
      ctx.restore();
    }

    // trim scissors markers
    if (this.view !== "thread" || this.highlight) {
      ctx.strokeStyle = "#b3424a";
      ctx.lineWidth = 1.4 / this.zoom;
      for (const m of this.trimMarkers) {
        if (progress < (m.stitchAt ?? 0)) continue;
        const s = 4 / this.zoom;
        ctx.beginPath();
        ctx.moveTo(m.x - s, m.y - s); ctx.lineTo(m.x + s, m.y + s);
        ctx.moveTo(m.x + s, m.y - s); ctx.lineTo(m.x - s, m.y + s);
        ctx.stroke();
      }
    }

    // needle position during playback
    ctx.restore();   // back out of the fit offset (needle uses css coords)
    if (this.progress.enabled && progress < Infinity && this.geo) {
      const pt = this._needlePosition(progress);
      if (pt) {
        const nx = this._worldToCssX(pt[0], t), ny = this._worldToCssY(pt[1], t);
        ctx.save();
        ctx.setTransform(window.devicePixelRatio || 1, 0, 0,
                         window.devicePixelRatio || 1, 0, 0);
        ctx.fillStyle = "#b3424a";
        ctx.beginPath();
        ctx.arc(nx, ny, 4.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.restore();
      }
    }
  }

  _needlePosition(stitchIdx) {
    // world-mm position of the needle after `stitchIdx` stitches
    for (const cp of this.chunkPaths) {
      if (cp.jump || cp.stitchStart == null) continue;
      if (stitchIdx >= cp.stitchStart && stitchIdx <= (cp.stitchEnd ?? cp.stitchStart)) {
        const seg = this._segByStart.get(cp.stitchStart);
        if (!seg) continue;
        const off = Math.min(stitchIdx - cp.stitchStart, seg.points.length - 1);
        return seg.points[Math.max(0, off)];
      }
    }
    return null;
  }

  // index helper rebuilt when geometry changes
  get _segByStart() {
    if (!this.__segByStart || this.__segGeo !== this.geo) {
      this.__segGeo = this.geo;
      this.__segByStart = new Map();
      for (const s of (this.geo && this.geo.segments) || []) {
        if (s.type === "stitch" && s.stitch_start != null) {
          this.__segByStart.set(s.stitch_start, s);
        }
      }
    }
    return this.__segByStart;
  }

  _drawHighlight(ctx, t) {
    const hl = this.highlight;
    if (!hl || !hl.locations || !hl.locations.length) return;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const mark = (x, y) => {
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    };
    for (const loc of hl.locations) {
      if (loc.type === "jump" || loc.type === "stitch") {
        mark(loc.from[0], loc.from[1]); mark(loc.to[0], loc.to[1]);
      } else if (loc.type === "density" || loc.type === "run") {
        mark(loc.x ?? loc.x_mm, loc.y ?? loc.y_mm);
      }
    }
    if (minX === Infinity) return;
    // outline box (in world → css space)
    const x0 = this._worldToCssX(minX, t), x1 = this._worldToCssX(maxX, t);
    const y0 = this._worldToCssY(maxY, t), y1 = this._worldToCssY(minY, t);
    ctx.save();
    ctx.setTransform(window.devicePixelRatio || 1, 0, 0,
                     window.devicePixelRatio || 1, 0, 0);
    ctx.strokeStyle = "#b3424a";
    ctx.lineWidth = 2;
    ctx.setLineDash([7, 5]);
    ctx.strokeRect(Math.min(x0, x1) - 8, Math.min(y0, y1) - 8,
                   Math.abs(x1 - x0) + 16, Math.abs(y1 - y0) + 16);
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(179,66,74,0.28)";
    for (const loc of hl.locations) {
      const pts = [];
      if (loc.type === "jump" || loc.type === "stitch") {
        pts.push([loc.from[0], loc.from[1]], [loc.to[0], loc.to[1]]);
      } else if (loc.type === "density" || loc.type === "run") {
        pts.push([loc.x ?? loc.x_mm, loc.y ?? loc.y_mm]);
      }
      for (const [px, py] of pts) {
        const sx = this._worldToCssX(px, t), sy = this._worldToCssY(py, t);
        ctx.beginPath();
        ctx.arc(sx, sy, 3.4, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  }

  zoomIn() { this._zoomAt(this.canvas.width / 2, this.canvas.height / 2, 1.35); }
  zoomOut() { this._zoomAt(this.canvas.width / 2, this.canvas.height / 2, 1 / 1.35); }
  fit() { this.zoom = 1; this.tx = 0; this.ty = 0; this._scheduleDraw(); }

  // ---------------------------------------------------------------- hover
  getHoverInfo(mx, my) {
    if (!this.geo || this.geo.empty || !this._fit) return null;
    const t = this._fit;
    // css px → world mm
    const wx = (mx - this.tx - this.canvas.width / (window.devicePixelRatio || 1) / 2)
      / this.zoom + this.canvas.width / (window.devicePixelRatio || 1) / 2;
    const wy = (my - this.ty - this.canvas.height / (window.devicePixelRatio || 1) / 2)
      / this.zoom + this.canvas.height / (window.devicePixelRatio || 1) / 2;
    const fx = (wx - t.ox) / t.scale;         // world mm
    const fy = -(wy - t.oy) / t.scale;
    const thresh = 6 / (t.scale * this.zoom); // 6 css px radius in world mm
    let best = null, bestD = thresh;
    for (const seg of this.geo.segments) {
      const pts = seg.points;
      for (let i = 0; i < pts.length; i++) {
        const dx = pts[i][0] - fx, dy = pts[i][1] - fy;
        const d = Math.hypot(dx, dy);
        if (d < bestD) {
          bestD = d;
          best = { seg, i };
        }
      }
    }
    if (!best) return null;
    const seg = best.seg;
    const pts = seg.points;
    const i = Math.max(1, best.i);
    const a = pts[i - 1], b = pts[i];
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const color = this.threads[seg.color] || "#808080";
    const stitchNo = seg.type === "stitch"
      ? ((seg.stitch_start ?? 0) + i) : null;
    const html = `
      <strong>${stitchNo != null ? `Stitch #${stitchNo.toLocaleString()}`
        : "Jump (needle up)"}</strong><br>
      Length: ${len.toFixed(2)} mm<br>
      Colour block: ${seg.color + 1}
      <span style="display:inline-block;width:9px;height:9px;background:${color};
        border:1px solid #888;border-radius:2px;margin-left:4px"></span>`;
    return { html, seg, len, stitchNo, color, block: seg.color };
  }

  // ---------------------------------------------------------------- report
  renderTo(canvasEl, fabricName = "white") {
    // offscreen render for the printable report
    const geo = this.geo;
    const saved = { geo: this.geo, view: this.view, fabric: this.fabric,
                    progress: { ...this.progress }, wipe: this.wipe,
                    highlight: this.highlight, zoom: this.zoom, tx: this.tx, ty: this.ty };
    const ctx = canvasEl.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const W = canvasEl.width / dpr, H = canvasEl.height / dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = (FABRICS[fabricName] || FABRICS.white).css;
    ctx.fillRect(0, 0, W, H);
    if (!geo || geo.empty || !geo.segments.length) return;
    const pad = 14;
    const scale = Math.min((W - pad * 2) / geo.extents.width_mm,
                           (H - pad * 2) / geo.extents.height_mm);
    const ox = (W - geo.extents.width_mm * scale) / 2 - geo.extents.min_x * scale;
    const oy = H - (H - geo.extents.height_mm * scale) / 2 + geo.extents.min_y * scale;
    ctx.save();
    ctx.translate(ox, oy);
    ctx.scale(scale, -scale);
    const width = 0.4 * Math.max(1, Math.min(3, scale / 3));
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    for (const seg of geo.segments) {
      if (seg.type === "jump") continue;
      const pts = seg.points;
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.strokeStyle = this.threads[seg.color] || "#333";
      ctx.lineWidth = width / scale;
      ctx.stroke();
    }
    ctx.restore();
    // restore is a no-op; we drew into a foreign canvas
    void saved;
  }
}

function _polylineLengthMm(pts) {
  let len = 0;
  for (let i = 1; i < pts.length; i++) {
    len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  }
  return len;
}

function _shade(hex, amt) {
  // amt -1..1: negative darkens, positive lightens
  const m = hex.replace("#", "");
  const n = parseInt(m.length === 3 ? m.split("").map(c => c + c).join("") : m, 16);
  let r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  if (amt >= 0) {
    r += (255 - r) * amt; g += (255 - g) * amt; b += (255 - b) * amt;
  } else {
    r *= 1 + amt; g *= 1 + amt; b *= 1 + amt;
  }
  return `rgb(${r | 0},${g | 0},${b | 0})`;
}

function _arrowHead(ctx, ax, ay, bx, by) {
  const ang = Math.atan2(by - ay, bx - ax);
  const s = 7;
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.moveTo(bx, by);
  ctx.lineTo(bx - s * Math.cos(ang - 0.5), by - s * Math.sin(ang - 0.5));
  ctx.lineTo(bx - s * Math.cos(ang + 0.5), by - s * Math.sin(ang + 0.5));
  ctx.closePath();
  ctx.fill();
}
