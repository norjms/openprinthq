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
