// OpenPrintHQ — robust printer online/offline state.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// A printer's `connected` flag can flap for a second or two on transient MQTT
// reconnects even while it stays powered and reachable. Showing "offline" on
// each blip makes the dashboard bounce. We apply hysteresis: a printer counts as
// online if it reported connected within a grace window. The last-online time is
// cached in localStorage so a page reload shows the true state immediately
// (before the first poll returns) instead of flashing offline.

const GRACE_MS = 90_000; // > the engine's 60s offline-notification debounce
const KEY = (id) => `ophq_online_${id}`;

// Record the latest reading. Call from data-load code (not during render).
export function markSeen(id, connected) {
  if (id == null) return;
  if (connected) {
    try { localStorage.setItem(KEY(id), String(Date.now())); } catch { /* */ }
  }
}

export function lastOnline(id) {
  try { return Number(localStorage.getItem(KEY(id))) || 0; } catch { return 0; }
}

// True if the printer was connected within the grace window (so a momentary
// disconnect still reads as online).
export function recentlyOnline(id) {
  const t = lastOnline(id);
  return !!t && (Date.now() - t) < GRACE_MS;
}

// Resolve a display state: true = online, false = offline, null = unknown yet.
export function isOnline(id, live) {
  if (live && live.connected) { markSeen(id, true); return true; }
  if (recentlyOnline(id)) return true;
  if (!live) return null; // no reading yet → "checking"
  return false;
}
