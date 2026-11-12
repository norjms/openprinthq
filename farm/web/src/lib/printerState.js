// How a printer's live status reads on a card: a short label and a tone
// (live, ok, warn, bad or neutral, the chip tones of artisanhub-ui).
// SPDX-License-Identifier: AGPL-3.0-or-later
import { recentlyOnline } from '$lib/online.js';

export function displayState(id, live) {
  const online = live ? (live.connected || recentlyOnline(id)) : recentlyOnline(id);
  if (!live && !online) return { key: 'checking', label: 'Checking', tone: 'neutral' };
  if (!online) return { key: 'offline', label: 'Offline', tone: 'bad' };
  const s = String(live?.state || '').toLowerCase();
  if ((live?.hms_errors || []).length && /fail|error/.test(s)) return { key: 'error', label: 'Error', tone: 'bad' };
  if (/fail|error/.test(s)) return { key: 'error', label: 'Error', tone: 'bad' };
  if (/run|print/.test(s)) return { key: 'printing', label: 'Printing', tone: 'live' };
  if (/pause/.test(s)) return { key: 'paused', label: 'Paused', tone: 'warn' };
  if (live?.awaiting_plate_clear) return { key: 'clear', label: 'Clear plate', tone: 'warn' };
  return { key: 'ready', label: 'Ready', tone: 'ok' };
}

// The job line for a card: file name, progress and time left.
export function jobOf(live) {
  const l = live || {};
  const name = l.subtask_name || l.gcode_file || l.current_print || '';
  const progress = Math.min(100, Math.max(0, Number(l.progress) || 0));
  return { name, progress, eta: fmtMinutes(l.remaining_time) };
}

export function fmtMinutes(mins) {
  if (mins == null || mins <= 0) return '';
  const h = Math.floor(mins / 60), m = Math.round(mins % 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}
