// OpenPrintHQ control-plane — Authentik admin API (signup user provisioning)
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Signup creates the *login identity* in Authentik via its admin API, then the
// app provisions the tenant instance. All config is env-driven so the tier is
// swappable with no code change: dev/test point at the homelab Authentik; prod
// (OCI) points at its own bundled Authentik.
const AK_URL = (process.env.OPHQ_AUTHENTIK_URL || '').replace(/\/+$/, '');
const AK_TOKEN = process.env.OPHQ_AUTHENTIK_TOKEN || '';
export const OWNER_GROUP = process.env.OPHQ_OWNER_GROUP || 'openprinthq-owners';
export const USER_GROUP = process.env.OPHQ_USER_GROUP || 'openprinthq-users';

export function authentikConfigured() { return !!(AK_URL && AK_TOKEN); }

async function ak(path, { method = 'GET', body } = {}) {
  const r = await fetch(`${AK_URL}/api/v3${path}`, {
    method,
    headers: { authorization: `Bearer ${AK_TOKEN}`, 'content-type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  });
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-json */ }
  if (!r.ok) {
    const msg = data?.detail || (data ? JSON.stringify(data) : text) || `HTTP ${r.status}`;
    const e = new Error(`authentik ${method} ${path} -> ${r.status}: ${msg}`);
    e.status = r.status; e.data = data;
    throw e;
  }
  return data;
}

async function groupPk(name) {
  if (!name) return null;
  const res = await ak(`/core/groups/?name=${encodeURIComponent(name)}`);
  const list = res?.results || [];
  const g = list.find((x) => x.name === name) || list[0];
  return g?.pk || null;
}

// True if a user with this email/username already exists in Authentik.
export async function authentikUserExists(email) {
  try {
    const res = await ak(`/core/users/?email=${encodeURIComponent(email)}`);
    return (res?.results || []).length > 0;
  } catch { return false; }
}

// Create the Authentik login user, set the password, and add to the owner or
// user group. Returns the Authentik user pk. Throws (with .status) on failure.
export async function createAuthentikUser(email, name, password, { owner = false } = {}) {
  if (!authentikConfigured()) throw new Error('authentik not configured');
  const groups = [];
  const gp = await groupPk(owner ? OWNER_GROUP : USER_GROUP);
  if (gp) groups.push(gp);
  // Username must be unique; the email doubles as the username.
  const user = await ak('/core/users/', {
    method: 'POST',
    body: { username: email, name: name || email, email, is_active: true, type: 'internal', groups }
  });
  await ak(`/core/users/${user.pk}/set_password/`, { method: 'POST', body: { password } });
  return user.pk;
}
