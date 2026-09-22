/* Before/after wipe compare + fix-impact cards.
   The heavy lifting (aligned dual prebake, clipped painting) lives in the
   viewer; this module owns the controls and the numbers. */
"use strict";

import { fmtClock, fmtMM } from "./timefmt.js";

export class SDCompare {
  constructor({ viewer, els, fetchOriginalGeometry }) {
    this.viewer = viewer;
    this.els = els;           // {bar, slider, modeBtn, closeBtn}
    this.fetchOriginal = fetchOriginalGeometry;
    this.active = false;
    this._bind();
  }

  _bind() {
    this.els.slider.addEventListener("input", () => {
      if (!this.active) return;
      this.viewer.setWipe({ pct: +this.els.slider.value,
                            vertical: this._vertical(), otherGeo: true });
    });
    this.els.modeBtn.addEventListener("click", () => {
      this.els.modeBtn.dataset.vertical =
        this._vertical() ? "" : "1";
      if (this.active) {
        this.viewer.setWipe({ pct: +this.els.slider.value,
                              vertical: this._vertical(), otherGeo: true });
      }
    });
    this.els.closeBtn.addEventListener("click", () => this.exit());
  }

  _vertical() { return !!this.els.modeBtn.dataset.vertical; }

  async enter() {
    if (this.active) { this.exit(); return; }
    try {
      const otherGeo = await this.fetchOriginal();
      this.viewer.beginCompare(otherGeo);
      this.active = true;
      this.els.bar.hidden = false;
      this.els.slider.value = 50;
      this.viewer.setWipe({ pct: 50, vertical: false, otherGeo: true });
      // pause the player and hide its bar: the wipe overlay lives at the
      // canvas bottom and the two rows would collide
      document.body.classList.add("player-visible");
      document.dispatchEvent(new CustomEvent("sd:compare", { detail: true }));
    } catch (err) {
      alert(`Compare failed: ${err.message}`);
    }
  }

  exit() {
    this.active = false;
    this.els.bar.hidden = true;
    this.viewer.setWipe(null);
    document.body.classList.remove("player-visible");
    document.dispatchEvent(new CustomEvent("sd:compare", { detail: false }));
  }
}

/* Impact card: before/after metric deltas for the current fix set. */
export function renderImpact(el, before, after) {
  if (!el) return;
  if (!before || !after) { el.hidden = true; el.innerHTML = ""; return; }
  const dSt = after.total_stitches - before.total_stitches;
  const dTravel = (after.jump_travel_mm || 0) - (before.jump_travel_mm || 0);
  const dTrims = (after.trims || 0) - (before.trims || 0);
  const dJumps = (after.jump_count || 0) - (before.jump_count || 0);
  const parts = [];
  const fmtDelta = (v, unit, cls = "") =>
    v === 0 ? "" : `<span class="${cls}">${v > 0 ? "+" : "−"}${Math.abs(v).toLocaleString()}${unit}</span>`;

  parts.push(fmtDelta(dSt, " stitches", dSt < 0 ? "ok" : "warn"));
  if (dTrims) parts.push(fmtDelta(dTrims, " trims", dTrims > 0 ? "ok" : "warn"));
  if (dJumps) parts.push(fmtDelta(dJumps, " jumps", dJumps < 0 ? "ok" : "warn"));
  if (Math.abs(dTravel) >= 1) {
    parts.push(fmtDelta(Math.round(dTravel), " mm travel",
                        dTravel < 0 ? "ok" : "warn"));
  }

  // sew-time delta using the client machine model
  const m = window.__sdMachine || { spm: 700, trim_s: 3, stop_s: 0.6 };
  const tBefore = before.total_stitches / m.spm * 60 + (before.trims || 0) * m.trim_s
    + (before.stops || 0) * m.stop_s;
  const tAfter = after.total_stitches / m.spm * 60 + (after.trims || 0) * m.trim_s
    + (after.stops || 0) * m.stop_s;
  const dT = tAfter - tBefore;
  if (Math.abs(dT) >= 1) {
    parts.push(`<span class="${dT < 0 ? "ok" : "warn"}">${dT < 0 ? "−" : "+"}${fmtClock(Math.abs(dT))} sew time</span>`);
  }

  if (!parts.length) {
    el.hidden = true; el.innerHTML = "";
    return;
  }
  el.hidden = false;
  el.innerHTML = `<span class="impact-title">Fix impact</span> ${parts.filter(Boolean).join(" · ")}`;
}
