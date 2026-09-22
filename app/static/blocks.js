/* Sew-order editor: drag-to-reorder colour blocks, reverse/delete/merge,
   with travel-preview lines on the canvas. Applies ops through the existing
   rebuild pipeline so every change stays reversible. */
"use strict";

export class SDBlocks {
  constructor({ api, getPid, viewer, els, onChange }) {
    this.api = api;           // (path, opts) => json
    this.getPid = getPid;
    this.viewer = viewer;
    this.els = els;           // {panel, list, travel, refreshBtn}
    this.onChange = onChange; // async () => after any applied op
    this.blocks = [];
    this.threads = [];
    this.palette = [];
    this._dragIndex = null;
    this._bind();
  }

  _bind() {
    this.els.refreshBtn.addEventListener("click", () => this.refresh());
    this.els.panel.addEventListener("toggle", () => {
      if (this.els.panel.open && !this.blocks.length) this.refresh();
    });
  }

  async refresh(version) {
    const pid = this.getPid();
    if (!pid) return;
    try {
      const qs = version != null ? `?version=${version}` : "";
      const data = await this.api(`/api/blocks/${pid}${qs}`);
      this.blocks = data.blocks;
      this.threads = data.threads;
      this.palette = data.palette;
      this.render();
    } catch (_) { /* panel just stays stale */ }
  }

  colorFor(i) {
    return this.threads[i] || this.palette[i % this.palette.length] || "#888";
  }

  render() {
    const list = this.els.list;
    list.innerHTML = "";
    let totalTravel = 0;
    this.blocks.forEach((b, i) => {
      totalTravel += b.travel_in_mm || 0;
      const row = document.createElement("div");
      row.className = "block-row";
      row.draggable = true;
      row.dataset.index = i;
      const sw = `<span class="swatch" style="background:${this.colorFor(i)}"></span>`;
      const ops = `
        <span class="b-ops">
          <button data-op="reverse" title="Reverse sew direction">⇄</button>
          ${i < this.blocks.length - 1
            ? `<button data-op="merge" title="Merge with next block">⇥</button>` : ""}
          ${this.blocks.length > 1
            ? `<button data-op="delete" title="Delete block">✕</button>` : ""}
        </span>`;
      row.innerHTML = `${sw}
        <span class="b-name">Block ${i + 1}</span>
        ${ops}
        <span class="b-meta">${b.stitches.toLocaleString()} st · ${Math.round(b.travel_in_mm)} mm in</span>`;
      this._bindRow(row, b, i);
      list.appendChild(row);
    });
    this.els.travel.textContent =
      `· ${Math.round(totalTravel)} mm travel-in`;
  }

  _bindRow(row, b, i) {
    row.addEventListener("dragstart", (e) => {
      this._dragIndex = i;
      row.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", String(i)); } catch (_) {}
    });
    row.addEventListener("dragend", () => {
      row.classList.remove("dragging");
      this.viewer.setBlockPreview([]);
      this._dragIndex = null;
    });
    row.addEventListener("dragover", (e) => {
      e.preventDefault();
      row.classList.add("drop-target");
      if (this._dragIndex != null && this._dragIndex !== i) this._previewTravel(i);
    });
    row.addEventListener("dragleave", () => row.classList.remove("drop-target"));
    row.addEventListener("drop", (e) => {
      e.preventDefault();
      row.classList.remove("drop-target");
      if (this._dragIndex != null && this._dragIndex !== i) {
        this.applyOp("move_block", { index: this._dragIndex, to: i });
      }
      this._dragIndex = null;
    });
    row.addEventListener("mouseenter", () => this._previewTravel(i));
    row.addEventListener("mouseleave", () => this.viewer.setBlockPreview([]));
    row.querySelectorAll(".b-ops button").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const op = btn.dataset.op;
        if (op === "reverse") this.applyOp("reverse_block", { index: i });
        else if (op === "delete") this.applyOp("delete_block", { index: i });
        else if (op === "merge") this.applyOp("merge_blocks",
          { index: i, with_index: i + 1 });
      });
    });
  }

  _previewTravel(targetIndex) {
    // dashed line: previous block's bbox centre → this block's start
    const target = this.blocks[targetIndex];
    if (!target || !target.start) return;
    const prev = this.blocks[targetIndex - 1];
    let x0, y0;
    if (prev && prev.bbox) {
      x0 = (prev.bbox.min_x + prev.bbox.max_x) / 2;
      y0 = (prev.bbox.min_y + prev.bbox.max_y) / 2;
    } else {
      x0 = target.start[0]; y0 = target.start[1] + 30; // from hoop edge-ish
    }
    this.viewer.setBlockPreview([[x0, y0, target.start[0], target.start[1]]]);
  }

  async applyOp(op, params) {
    const pid = this.getPid();
    if (!pid) return;
    const body = { ops: [{ op, params }] };
    try {
      await this.api(`/api/rebuild/${pid}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      await this.refresh();
      if (this.onChange) await this.onChange(op, params);
    } catch (err) {
      alert(`Sew-order change failed: ${err.message}`);
    }
  }
}
