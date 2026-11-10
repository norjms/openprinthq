// OpenPrintHQ control-plane - single-tenant mode
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// By default every account gets its own engine and its own Spoolman, which the
// control-plane starts itself through the Docker socket. That suits a hosted
// deployment with many tenants.
//
// A self-hosted farm has one tenant: the people who run it. Giving that
// control-plane the Docker socket hands root on the host to a web service for
// no gain, so single-tenant mode replaces the whole container machinery with
// two URLs. Whoever runs the stack (a compose file, an installer) starts ONE
// engine and ONE Spoolman, and the control-plane talks to them by address:
//
//   OPHQ_ENGINE_URL     the engine every account uses, e.g. http://openprinthq-engine:8000
//   OPHQ_SPOOLMAN_URL   the Spoolman that engine uses, e.g. http://spoolman:8000 (optional)
//
// Setting OPHQ_ENGINE_URL turns the mode on. In it the control-plane never runs
// a `docker` command, so the socket need not be mounted at all.
//
// Read lazily, so tests can set the environment after import.

const trim = (s) => String(s || '').trim().replace(/\/+$/, '');

export function engineUrl() { return trim(process.env.OPHQ_ENGINE_URL); }

/** True when one engine, started by someone else, serves every account. */
export function singleTenant() { return !!engineUrl(); }

/** The Spoolman to use in single-tenant mode, or '' for none (or for multi-tenant mode). */
export function externalSpoolmanUrl() { return singleTenant() ? trim(process.env.OPHQ_SPOOLMAN_URL) : ''; }

/**
 * Base URL of the engine that serves this tenant: the shared engine in
 * single-tenant mode, else the tenant's own container on the internal network.
 * Engines are never published to the host, so they are reached by name.
 */
export function engineBaseFor(subdomain, port = 8000) {
  if (!subdomain) return null;
  return engineUrl() || `http://ophq-${subdomain}:${port}`;
}

/** engineBaseFor, from an instance row. */
export function engineBaseOf(inst, port = 8000) {
  return inst && inst.subdomain ? engineBaseFor(inst.subdomain, port) : null;
}
