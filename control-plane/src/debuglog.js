// OpenPrintHQ control-plane — opt-in debug tracing.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Enabled with OPHQ_DEBUG=1 (or true/yes/on). When off, dbg() is a no-op so
// there is zero overhead and nothing is emitted in production by default.
// Traces are written to stderr with a timestamp + scope so the connector <->
// control-plane <-> engine path can be followed end to end when diagnosing
// "the app can't reach my printer" type problems.
export const DEBUG = /^(1|true|yes|on)$/i.test(process.env.OPHQ_DEBUG || '');

export function dbg(scope, ...args) {
  if (DEBUG) {
    // eslint-disable-next-line no-console
    console.error(new Date().toISOString(), `[control-plane][${scope}]`, ...args);
  }
}
