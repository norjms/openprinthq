// OpenPrintHQ control-plane — opt-in debug tracing.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Enabled with OPHQ_DEBUG=1 (or true/yes/on). When off, dbg() is a no-op so
// there is zero overhead and nothing is emitted in production by default.
// Traces are written to stderr with a timestamp + scope so the connector <->
// control-plane <-> engine path can be followed end to end when diagnosing
// "the app can't reach my printer" type problems.
// OPHQ_DEBUG=1 (or true/yes/on) turns everything on. It also accepts a
// comma-separated scope list — OPHQ_DEBUG=connector,routing — because that is
// the obvious thing to type and it previously matched nothing at all, silently
// disabling every trace while looking enabled.
const RAW = (process.env.OPHQ_DEBUG || '').trim();
const ALL = /^(1|true|yes|on)$/i.test(RAW);
const SCOPES = ALL ? null : new Set(RAW.split(',').map((x) => x.trim().toLowerCase()).filter(Boolean));
export const DEBUG = ALL || (SCOPES && SCOPES.size > 0);

export function dbg(scope, ...args) {
  if (ALL || (SCOPES && SCOPES.has(String(scope).toLowerCase()))) {
    // eslint-disable-next-line no-console
    console.error(new Date().toISOString(), `[control-plane][${scope}]`, ...args);
  }
}
