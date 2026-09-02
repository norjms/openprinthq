// OpenPrintHQ — printer model display names.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Bambu printers report a terse internal code (e.g. "O1D", "O1C2") or a full
// 3MF name ("Bambu Lab H2D"). We show the common marketing name (H2D, H2C,
// X1C…). Mirrors the engine's utils/printer_models.py so display matches
// discovery. Non-Bambu / unknown models pass through unchanged (minus a
// redundant "Bambu Lab " prefix).

// Internal device/model-id codes → common short name.
const CODE_MAP = {
  O1D: 'H2D', O1E: 'H2D Pro', O2D: 'H2D Pro', O1C: 'H2C', O1C2: 'H2C', O1S: 'H2S',
  C11: 'X1C', C12: 'X1', C13: 'X1E',
  P1P: 'P1P', P1S: 'P1S', P2S: 'P2S',
  N6: 'X2D', N9: 'A2L',
  A11: 'A1', A12: 'A1 Mini', N1: 'A1 Mini', N2S: 'A1', A04: 'A1 Mini'
};

// Full 3MF / cloud names → common short name.
const FULL_MAP = {
  'BAMBU LAB X1 CARBON': 'X1C', 'BAMBU LAB X1': 'X1', 'BAMBU LAB X1E': 'X1E',
  'BAMBU LAB P1S': 'P1S', 'BAMBU LAB P1P': 'P1P', 'BAMBU LAB P2S': 'P2S',
  'BAMBU LAB A1': 'A1', 'BAMBU LAB A1 MINI': 'A1 Mini', 'BAMBU LAB A1M': 'A1 Mini',
  'BAMBU LAB H2D': 'H2D', 'BAMBU LAB H2D PRO': 'H2D Pro', 'BAMBU LAB H2C': 'H2C',
  'BAMBU LAB H2S': 'H2S', 'BAMBU LAB X2D': 'X2D', 'BAMBU LAB A2L': 'A2L'
};

// Platform connection_type → manufacturer name, for platforms that ARE a single
// manufacturer. Generic platforms (klipper / octoprint / duet / mks) are left
// blank on purpose: there the free-text model already carries the brand
// (e.g. "Voron 2.4 R2 350", "RatRig V-Core 4"), so we don't want to prepend the
// platform name.
const MANUFACTURER = {
  bambu: 'Bambu Lab',
  prusalink: 'Prusa', prusa: 'Prusa',
  snapmaker: 'Snapmaker',
  flashforge: 'FlashForge'
};

/**
 * "<Manufacturer> <Model>" display label — e.g. "Bambu Lab H2D" for a Bambu, or
 * "Voron 2.4 R2 350" for a Klipper Voron (manufacturer already in the model).
 */
export function printerLabel(connectionType, model) {
  const mfr = MANUFACTURER[String(connectionType || '').toLowerCase()] || '';
  const m = prettyModel(model || '');
  // The model can already carry the brand: the printer catalog hands back
  // "Prusa MK4S", not "MK4S", so prepending would read "Prusa Prusa MK4S".
  if (mfr && m.toLowerCase().startsWith(mfr.toLowerCase())) return m;
  return [mfr, m].filter(Boolean).join(' ').trim();
}

// ---- OrcaSlicer printer-preset filtering by connected printers ----------------
// The slice dialog should offer profiles only for the printers a user has added,
// not OrcaSlicer's full ~hundreds-entry catalogue. We match a connected printer's
// marketing model (via prettyModel) against the preset display names.
// Some very-new models (e.g. H2C) aren't in older OrcaSlicer builds yet — fall
// back to the closest sibling's presets so those printers can still slice.
const _PRESET_ALIAS = { 'h2c': 'h2d' };
function _coreTokens(str) {
  return String(str || '').toLowerCase().replace(/[^a-z0-9. ]/g, ' ').split(/\s+/)
    .filter(Boolean).filter((t) => !/^r\d+$/.test(t)); // drop revision tokens (r2…)
}
function _nameMatches(nameLower, toks) {
  if (!toks.length) return false;
  return toks.length === 1 ? nameLower.includes(toks[0]) : toks.every((t) => nameLower.includes(t));
}
/**
 * Filter OrcaSlicer printer presets to those matching the user's connected
 * printers. `presets`: [{ name, … }]; `printers`: [{ model, … }]. Returns the
 * matching subset — or the full list if nothing matched (so slicing is never
 * blocked). Each printer contributes its native presets, or its alias sibling's
 * presets when it has no native match in the current catalogue.
 */
export function filterPresetsForConnected(presets, printers) {
  if (!Array.isArray(presets) || !Array.isArray(printers) || !printers.length) return presets;
  const keep = new Set();
  for (const p of printers) {
    const pretty = prettyModel(p.model || '').toLowerCase();
    if (!pretty) continue;
    let hits = presets.filter((pr) => _nameMatches(String(pr.name || '').toLowerCase(), _coreTokens(pretty)));
    if (!hits.length && _PRESET_ALIAS[pretty]) {
      hits = presets.filter((pr) => _nameMatches(String(pr.name || '').toLowerCase(), _coreTokens(_PRESET_ALIAS[pretty])));
    }
    for (const h of hits) keep.add(h);
  }
  const out = presets.filter((pr) => keep.has(pr));
  return out.length ? out : presets;
}

import { PRINTER_IMAGES } from './printerImages.js';

// Resolve a printer's cover image (from the bundled OrcaSlicer set) by matching
// its "<manufacturer> <model>" label to the image manifest, with a few tolerant
// fallbacks (model-name alias, revision-token drop). Returns a /printers URL or
// null (callers fall back to a generic printer glyph).
const _normImg = (s) => String(s || '').toLowerCase().replace(/\s+/g, ' ').trim();
const IMG_ALIAS = { 'x1c': 'x1 carbon' }; // our common name → OrcaSlicer file name
export function printerImage(connectionType, model) {
  const label = printerLabel(connectionType, model);
  const tries = [];
  const add = (s) => { const n = _normImg(s); if (n && !tries.includes(n)) tries.push(n); };
  add(label);
  const pm = _normImg(prettyModel(model));
  if (IMG_ALIAS[pm]) add(_normImg(label).replace(pm, IMG_ALIAS[pm]));
  add(_normImg(label).replace(/\br\d\b/g, ' ').replace(/\s+/g, ' ').trim()); // drop R2/R1 etc.
  add(model);
  for (const t of tries) {
    if (PRINTER_IMAGES[t]) return '/printers/' + encodeURIComponent(PRINTER_IMAGES[t]);
  }
  return null;
}

// Bambu H2-series nozzle hardware codes → readable material + flow. The code is
// <material><flow><rev>: 1st char material (H=Hardened Steel, S=Stainless Steel),
// 2nd char flow (H=High-Flow, S=Standard). e.g. HS01 = Hardened Steel,
// HH01 = Hardened Steel High-Flow (confirmed by the engine's own notes).
const NOZZLE_MAT = { H: 'Hardened Steel', S: 'Stainless Steel' };
export function nozzleType(code) {
  const c = String(code || '').trim().toUpperCase();
  if (!c) return { full: '', short: '', hf: false, material: '' };
  const material = NOZZLE_MAT[c[0]] || '';
  const hf = c[1] === 'H';
  const full = material ? material + (hf ? ' · High-Flow' : '') : c;
  const short = material ? (material.split(' ').map((w) => w[0]).join('') + (hf ? '·HF' : '')) : c;
  return { full, short, hf, material };
}

/** Return the printer's common model name for display. */
export function prettyModel(raw) {
  if (!raw) return raw || '';
  const s = String(raw).trim();
  const up = s.toUpperCase();
  // exact internal code
  if (CODE_MAP[up]) return CODE_MAP[up];
  // tolerate the printer reporting a leading zero for the letter O (e.g. "01D")
  const oForm = up.replace(/^0(?=[0-9A-Z])/, 'O');
  if (CODE_MAP[oForm]) return CODE_MAP[oForm];
  // full 3MF / cloud name
  if (FULL_MAP[up]) return FULL_MAP[up];
  // otherwise drop a redundant "Bambu Lab " prefix, else pass through
  return s.replace(/^Bambu\s*Lab\s*/i, '') || s;
}
