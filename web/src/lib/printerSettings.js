// OpenPrintHQ — per-printer UI/hardware settings.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Small per-printer preferences that describe hardware the engine can't always
// auto-detect — e.g. whether a Klipper/Mainsail machine has a chamber heater.
// The engine already accepts a `chamber` temperature target; this just controls
// whether the chamber control is offered in the UI. Stored per printer id in
// localStorage (device-local, mirrors the online.js pattern) so it applies
// instantly on load without an extra round-trip.

const KEY = (id) => `ophq_printer_cfg_${id}`;

export const DEFAULT_SETTINGS = {
  chamberHeater: false,     // this printer has a controllable chamber heater
  showFilamentPanel: true   // show the multi-material (AMS/CFS/MMU) panel when a unit is present
};

export function getPrinterSettings(id) {
  if (id == null) return { ...DEFAULT_SETTINGS };
  try {
    const raw = localStorage.getItem(KEY(id));
    if (!raw) return { ...DEFAULT_SETTINGS };
    return { ...DEFAULT_SETTINGS, ...(JSON.parse(raw) || {}) };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function savePrinterSettings(id, cfg) {
  if (id == null) return;
  try {
    localStorage.setItem(KEY(id), JSON.stringify({ ...DEFAULT_SETTINGS, ...(cfg || {}) }));
  } catch { /* */ }
}
