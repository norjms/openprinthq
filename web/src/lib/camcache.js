// Last-known camera frame per printer, kept in localStorage.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The point of this cache is the first second of a page load. A camera frame
// arrives over the connector relay in a few hundred milliseconds at best, and a
// grid of empty rectangles for that long makes the whole app feel dead. Showing
// the last frame instantly, then replacing it, costs nothing and reads as
// responsive.
//
// It is a PLACEHOLDER, never a feed. A cached frame can be minutes old, and
// anything displaying one has to say so; that is what the SNAPSHOT badge and the
// `stale` styling in CameraImg are for. Treating this as a cheap live view is
// exactly the confusion the badge exists to prevent.
//
// Why this file exists at all: the cache used to live inside CameraImg, encoding
// the frame at full sensor resolution and then throwing the result away if it
// came out over 300KB. Measured against the real cameras, a 1680x1080 frame
// encodes to ~340KB and a 1920x1056 to ~338KB, so the guard silently rejected
// them and only one printer in three ever cached anything. The feature had been
// a no-op since it was written. Downscaling first brings the same frames to
// 21-47KB, which is both under any sane cap and plenty for a placeholder.

const PREFIX = 'ophq_cam_';
const KEY = (id) => `${PREFIX}${id}`;

// Wide enough for a detail view at 2x, small enough that a dozen of them fit in
// localStorage with room to spare.
const MAX_W = 640;
const QUALITY = 0.5;

// localStorage is ~5MB per origin and is shared with everything else the app
// keeps there. At ~45KB a frame this is well under a megabyte, and it bounds the
// damage on an instance with a lot of printers.
const MAX_ENTRIES = 16;
const MAX_BYTES = 250_000;   // a single frame this big means downscaling failed

function entries() {
  const out = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(PREFIX)) out.push(k);
    }
  } catch { /* private mode, disabled storage */ }
  return out;
}

function parse(raw) {
  if (!raw) return null;
  // Entries written before this module was extracted are a bare data URL.
  if (raw.startsWith('data:')) return { d: raw, t: 0 };
  try {
    const o = JSON.parse(raw);
    return o && typeof o.d === 'string' ? { d: o.d, t: Number(o.t) || 0 } : null;
  } catch { return null; }
}

/** The cached frame for a printer, or null. `{ d: dataUrl, t: epochMs }`. */
export function readFrame(id) {
  try { return parse(localStorage.getItem(KEY(id))); } catch { return null; }
}

/** How long ago this printer's frame was cached, in ms. Infinity if never. */
export function frameAge(id) {
  const e = readFrame(id);
  return e && e.t ? Date.now() - e.t : Infinity;
}

function evictOldest() {
  const all = entries()
    .map((k) => ({ k, t: (parse(localStorage.getItem(k)) || { t: 0 }).t }))
    .sort((a, b) => a.t - b.t);
  const victim = all[0];
  if (victim) { try { localStorage.removeItem(victim.k); } catch { /* */ } return true; }
  return false;
}

/**
 * Encode an already-loaded, same-origin <img> down to a small JPEG and store it.
 * Silently does nothing if the canvas is tainted, storage is unavailable, or the
 * frame will not shrink far enough — a missing placeholder is a cosmetic loss.
 */
export function writeFrame(id, img) {
  if (!img || !img.naturalWidth) return false;
  let data;
  try {
    const scale = Math.min(1, MAX_W / img.naturalWidth);
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(img.naturalWidth * scale));
    c.height = Math.max(1, Math.round(img.naturalHeight * scale));
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    data = c.toDataURL('image/jpeg', QUALITY);
  } catch { return false; }
  if (!data || data.length > MAX_BYTES) return false;

  const payload = JSON.stringify({ d: data, t: Date.now() });
  for (let attempt = 0; attempt < MAX_ENTRIES; attempt++) {
    try {
      localStorage.setItem(KEY(id), payload);
      // Trim after a successful write so the cap is enforced even when nothing
      // ever throws.
      while (entries().length > MAX_ENTRIES) { if (!evictOldest()) break; }
      return true;
    } catch {
      // Out of quota, or something else filled the store. Make room and retry
      // rather than losing the frame outright.
      if (!evictOldest()) return false;
    }
  }
  return false;
}

/** Load one frame over the network and cache it. Resolves either way. */
export function fetchAndCache(printerId) {
  return new Promise((resolve) => {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => { writeFrame(printerId, img); resolve(true); };
    img.onerror = () => resolve(false);
    img.src = `/api/engine/api/v1/printers/${printerId}/camera/snapshot?t=${Date.now()}`;
  });
}

/**
 * Warm the cache for a set of printers, skipping any whose frame is already
 * fresher than `maxAgeMs`.
 *
 * Deliberately serial with a small gap. This runs in the background on every app
 * page, and the frames travel over the same connector tunnel that carries print
 * control; a burst of parallel snapshot requests to compete with that is a poor
 * trade for filling a cache nobody is waiting on.
 */
export async function warm(printerIds, { maxAgeMs = 5 * 60_000, limit = MAX_ENTRIES } = {}) {
  let done = 0;
  for (const id of printerIds.slice(0, limit)) {
    if (frameAge(id) < maxAgeMs) continue;
    await fetchAndCache(id);
    done++;
    await new Promise((r) => setTimeout(r, 250));
  }
  return done;
}
