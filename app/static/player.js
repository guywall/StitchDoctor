/* Stitch Doctor player: animates the design sewing itself in stitch order.
   Owns the time model (stitches → seconds using /api/config machine constants)
   and drives SDViewer.setProgress(). WebAudio sound is optional. */
"use strict";

import { fmtClock } from "./timefmt.js";

export class SDPlayer {
  constructor(viewer, ui, machine) {
    this.viewer = viewer;
    this.ui = ui;             // {player, playBtn, stopBtn, scrub, speed, time, sound}
    this.machine = machine || { spm: 700, trim_s: 3, stop_s: 0.6, extra_stitch_s: 0.15 };
    this.total = 0;           // total stitch count
    this.pos = 0;             // current stitch index
    this.playing = false;
    this._raf = 0;
    this._lastT = 0;
    this._audio = null;
    this._timeline = null;    // cumulative seconds per stitch event
    this._buildTimeline();
    this._bind();
    // the compare wipe overlay shares this space — duck out while it's open
    document.addEventListener("sd:compare", (e) => {
      this.ui.player.hidden = e.detail ? true : this.hiddenByDefault();
      if (e.detail) this.pause();
    });
  }

  hiddenByDefault() {
    // the transport only appears once a pattern is loaded
    return !this.total;
  }

  setGeometry(geo) {
    this.total = (geo && geo.stitch_total) || 0;
    this.pos = Math.min(this.pos, this.total);
    this._buildTimeline();
    this._syncUI();
    this.viewer.clearProgress();   // constructor: show the full design
  }

  _buildTimeline() {
    // cumulative sew-seconds at each stitch index, using real jump lengths
    const geo = this.viewer.geo;
    const tl = new Float64Array(this.total + 1);
    if (!geo || !geo.segments) { this._timeline = tl; return; }
    let t = 0;
    const m = this.machine;
    const perStitch = 60 / m.spm;
    for (const seg of geo.segments) {
      if (seg.type === "jump") {
        t += (seg.length_mm || 0) / m.spm * 60 * 0.6; // hoop moves slower
      } else if (seg.type === "stitch") {
        const n = (seg.stitch_end ?? 0) - (seg.stitch_start ?? 0);
        t += n * perStitch;
      }
    }
    this._totalSeconds = t;
    // per-stitch lookup: walking segments on demand is fine (scrub is rare)
    this._timeline = tl;
  }

  _secondsAt(stitch) {
    // walk segments up to `stitch` — O(segments) but scrubbing is user-rate
    const geo = this.viewer.geo;
    if (!geo || !geo.segments) return 0;
    const m = this.machine;
    let t = 0;
    for (const seg of geo.segments) {
      if (seg.type === "jump") {
        if ((seg.stitch_at ?? 0) >= stitch) break;
        t += (seg.length_mm || 0) / m.spm * 60 * 0.6;
      } else if (seg.type === "stitch") {
        const s = seg.stitch_start ?? 0, e = seg.stitch_end ?? 0;
        if (s >= stitch) break;
        const upto = Math.min(stitch, e);
        t += Math.max(0, upto - s) * (60 / m.spm);
      }
    }
    return t;
  }

  _bind() {
    const u = this.ui;
    u.playBtn.addEventListener("click", () => this.toggle());
    u.stopBtn.addEventListener("click", () => {
      this.pause();
      this.pos = 0;
      this.ui.scrub.value = 0;
      this.viewer.clearProgress();   // back to the plain static view
      this._syncUI();
    });
    u.scrub.addEventListener("input", () => {
      this.pause();
      this.pos = Math.round((u.scrub.value / 1000) * this.total);
      this._syncUI(); this._push();
    });
    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input[type=text], select, textarea")) return;
      if (e.code === "Space" && this.ui.player.hidden === false) {
        e.preventDefault();
        this.toggle();
      } else if (e.code === "ArrowRight" && this.ui.player.hidden === false) {
        this.pause(); this.pos = Math.min(this.total, this.pos + 25);
        this._syncUI(); this._push();
      } else if (e.code === "ArrowLeft" && this.ui.player.hidden === false) {
        this.pause(); this.pos = Math.max(0, this.pos - 25);
        this._syncUI(); this._push();
      }
    });
  }

  show() {
    this.ui.player.hidden = false;
    this.pos = 0;
    this.ui.scrub.value = 0;
    // Full design until the user actually plays — arming the player must
    // not blank the canvas at stitch 0.
    this.viewer.clearProgress();
    this._syncUI();
  }

  toggle() { this.playing ? this.pause() : this.play(); }

  play() {
    if (!this.total) return;
    if (this.pos >= this.total) this.pos = 0;
    this.playing = true;
    this.ui.playBtn.textContent = "⏸";
    this._lastT = performance.now();
    if (this.ui.sound.checked) this._ensureAudio();
    const step = (now) => {
      if (!this.playing) return;
      const dt = (now - this._lastT) / 1000;
      this._lastT = now;
      const speed = parseFloat(this.ui.speed.value);
      this.pos = Math.min(this.total, this.pos + dt * speed * this.machine.spm / 60);
      this._tickColourChange();
      this._syncUI(); this._push();
      if (this.pos >= this.total) { this.pause(); }
      else this._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
  }

  _tickColourChange() {
    // soft chime when the needle enters a new colour block
    if (!this.ui.sound.checked) return;
    this._ensureAudio();
    if (!this._audio) return;
    const pos = Math.floor(this.pos);
    let color = null;
    for (const seg of (this.viewer.geo && this.viewer.geo.segments) || []) {
      if (seg.type !== "stitch") continue;
      const s = seg.stitch_start ?? 0, e = seg.stitch_end ?? 0;
      if (pos >= s && pos <= e) { color = seg.color; break; }
    }
    if (color !== this._lastColour) {
      this._lastColour = color;
      if (color !== null && this._audio) {
        try {
          const o = this._audio.createOscillator();
          const g = this._audio.createGain();
          o.frequency.value = 660 + color * 60;
          g.gain.setValueAtTime(0.06, this._audio.currentTime);
          g.gain.exponentialRampToValueAtTime(0.0001, this._audio.currentTime + 0.18);
          o.connect(g); g.connect(this._audio.destination);
          o.start(); o.stop(this._audio.currentTime + 0.2);
        } catch (_) { /* audio unavailable */ }
      }
    }
  }

  pause() {
    this.playing = false;
    this.ui.playBtn.textContent = "▶";
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = 0;
  }

  _syncUI() {
    const pct = this.total ? this.pos / this.total : 0;
    this.ui.scrub.value = Math.round(pct * 1000);
    const at = this._secondsAt(Math.floor(this.pos));
    const est = (this._totalSeconds || 0) * (1 - pct);
    this.ui.time.textContent =
      `${fmtClock(at)} / ${fmtClock(this._totalSeconds || 0)}` +
      (this.playing ? `  (−${fmtClock(est)})` : "");
  }

  _push() {
    this.viewer.setProgress(Math.floor(this.pos));
  }

  // --- optional audio --------------------------------------------------------
  _ensureAudio() {
    if (!this._audio) {
      try { this._audio = new AudioContext(); } catch (_) { this._audio = null; }
    }
    if (this._audio && this._audio.state === "suspended") this._audio.resume();
  }
}
