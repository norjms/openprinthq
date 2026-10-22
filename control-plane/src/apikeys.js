// Scoped API keys for scripts and integrations (Artisan Hub, n8n, the phone app).
//
// A browser keeps using the Authentik session. A program sends `Authorization: Bearer ophqk_...` (or
// `X-API-Key`) and gets what the key's scope allows, on the same routes:
//   read     look at printers, the queue, files, spools, history
//   submit   read, plus upload files and add or reprint jobs
//   control  submit, plus everything else a person can change (printer commands, queue edits, settings)
// Account and credential routes (sign-in, users, groups, MFA, keys, cloud logins, backups) never take a
// key: a leaked key must not be able to mint more keys or lock its owner out.
//
// Keys live in printhost_tokens with kind 'api' and a scope. The secret is shown once, at creation.
// SPDX-License-Identifier: AGPL-3.0-or-later

export const API_KEY_PREFIX = 'ophqk_';
export const SCOPES = ['read', 'submit', 'control'];
const RANK = { read: 1, submit: 2, control: 3 };

// Engine paths (after /api/engine, with the /api/v1 prefix) a key may never use.
const NEVER = [
  /^\/api\/v1\/auth(\/|$)/,
  /^\/api\/v1\/mfa(\/|$)/,
  /^\/api\/v1\/users(\/|$)/,
  /^\/api\/v1\/groups(\/|$)/,
  /^\/api\/v1\/api-keys(\/|$)/,
  /^\/api\/v1\/cloud\/(login|verify|token|logout)(\/|$)/,
  /^\/api\/v1\/orca-cloud\/(device|logout)(\/|$)/,
  /^\/api\/v1\/settings\/(backup|restore|reset)(\/|$)/,
  /^\/api\/v1\/github-backup(\/|$)/,
  /^\/api\/v1\/local-backup(\/|$)/,
  /^\/api\/v1\/support(\/|$)/
];

// Writes a 'submit' key may make: getting work into the farm, nothing else.
const SUBMIT = [
  /^\/api\/v1\/queue\/?$/,                                  // add to queue
  /^\/api\/v1\/library\/files\/?$/,                         // upload a file
  /^\/api\/v1\/library\/files\/add-to-queue\/?$/,
  /^\/api\/v1\/library\/files\/[^/]+\/(print|slice)\/?$/,
  /^\/api\/v1\/library\/files\/extract-zip\/?$/,
  /^\/api\/v1\/archives\/upload(-bulk|-source)?\/?$/,
  /^\/api\/v1\/archives\/[^/]+\/(reprint|slice)\/?$/,
  /^\/api\/v1\/makerworld\/(resolve|import)\/?$/
];

function pathOnly(p) {
  const s = String(p || '/');
  const q = s.indexOf('?');
  return q >= 0 ? s.slice(0, q) : s;
}

/** The scope a request to the engine needs, or 'deny' when no key may make it. */
export function scopeFor(method, enginePath) {
  const p = pathOnly(enginePath);
  if (/(^|\/)\.\.(\/|$)/.test(p) || p.includes('%2e') || p.includes('%2E')) return 'deny';
  if (NEVER.some((r) => r.test(p))) return 'deny';
  const m = String(method || 'GET').toUpperCase();
  if (m === 'GET' || m === 'HEAD' || m === 'OPTIONS') return 'read';
  if (m === 'POST' && SUBMIT.some((r) => r.test(p))) return 'submit';
  return 'control';
}

/** Does a key with scope `have` cover a request that needs `need`? */
export function allows(have, need) {
  if (need === 'deny') return false;
  return (RANK[have] || 0) >= (RANK[need] || 99);
}

export function normalizeScope(s) {
  const v = String(s || '').toLowerCase().trim();
  return SCOPES.includes(v) ? v : null;
}

/** The key a request carries, if any (never a session cookie). */
export function presentedKey(req) {
  const h = req.headers || {};
  const auth = String(h['authorization'] || '');
  const bearer = /^Bearer\s+(.+)$/i.exec(auth);
  const k = (bearer ? bearer[1] : (h['x-api-key'] || '')).trim();
  return k.startsWith(API_KEY_PREFIX) ? k : '';
}

/**
 * Resolve the caller as a key holder. Answers { user, key } or sends the error and answers null.
 * `resolveToken(secret)` -> { userId, email, kind, scope, tokenId } | null; `getUser(id)` -> user row.
 */
export async function keyCaller(req, reply, need, { resolveToken, getUser }) {
  const secret = presentedKey(req);
  if (!secret) { reply.code(401).send({ error: 'not authenticated' }); return null; }
  const who = await resolveToken(secret);
  if (!who || who.kind !== 'api') { reply.code(401).send({ error: 'invalid or expired API key' }); return null; }
  if (!allows(who.scope, need)) {
    reply.code(403).send({
      error: need === 'deny' ? 'API keys cannot use this route; sign in instead' : `this key's scope is ${who.scope}; this needs ${need}`,
      needs: need === 'deny' ? null : need
    });
    return null;
  }
  const user = await getUser(who.userId);
  if (!user) { reply.code(401).send({ error: 'invalid or expired API key' }); return null; }
  return { user, key: { id: who.tokenId, scope: who.scope } };
}
