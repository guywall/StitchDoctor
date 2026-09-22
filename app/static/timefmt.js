/* Small shared formatting helpers (client-side mirror of analysis/sewtime.py). */
"use strict";

export function fmtClock(secs) {
  const s = Math.round(secs);
  if (s >= 3600) {
    return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtMM(mm) {
  if (mm >= 10000) return `${(mm / 1000).toFixed(1)} m`;
  return `${Math.round(mm)} mm`;
}
