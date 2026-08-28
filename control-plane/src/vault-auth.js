/**
 * How the control-plane proves who it is speaking for to a tenant's library.
 *
 * The library has no accounts of its own: it takes the identity we assert in
 * headers, and, when a secret is configured, only if the assertion is signed.
 * That signature is the whole trust boundary, so it lives here, in one file
 * both the proxy and the provisioner use, rather than being reimplemented at
 * either end.
 *
 * The secret is DERIVED from the gateway secret rather than being a new one to
 * configure, the same way the connector signing key is. That keeps deployment
 * unchanged, at the cost of one thing worth knowing: rotating the gateway
 * secret changes this one too, and a library container started with the old
 * value will refuse every request until it is recreated. `ensureVault` checks
 * for exactly that and recreates the container when it finds it.
 */

import { createHash, createHmac } from 'node:crypto';

const GATEWAY_SECRET = process.env.OPHQ_GATEWAY_SECRET || '';

/** The value handed to the container as OPHQ_AUTH_SECRET. */
export function vaultAuthSecret() {
  if (process.env.OPHQ_VAULT_AUTH_SECRET) return process.env.OPHQ_VAULT_AUTH_SECRET;
  if (!GATEWAY_SECRET) return '';
  return createHash('sha256').update('ophq-vault-auth|' + GATEWAY_SECRET).digest('base64url');
}

// The identity the control-plane uses for the calls it makes on nobody's
// behalf: seeding the print host, triggering a scan after an upload. The
// library knows this name and will never adopt a tenant's existing library
// into it.
export const VAULT_SERVICE_USER = 'openprinthq-control';
const VAULT_ADMIN_GROUP = 'openprinthq-admins';

/**
 * Headers asserting an identity to a tenant library.
 *
 * The timestamp is part of the signed material along with the identity, so a
 * captured header cannot be replayed under a different name, and not for long
 * under its own.
 */
export function vaultIdentityHeaders({ username, email = '', groups = [] }) {
  const groupList = Array.isArray(groups) ? groups.join(',') : String(groups || '');
  const headers = {
    'x-ophq-user': username,
    'x-ophq-email': email || '',
    'x-ophq-groups': groupList
  };
  const secret = vaultAuthSecret();
  if (secret) {
    const ts = Math.floor(Date.now() / 1000);
    const mac = createHmac('sha256', secret)
      .update(`${username}\n${email || ''}\n${groupList}\n${ts}`)
      .digest('base64url');
    headers['x-ophq-auth'] = `${ts}.${mac}`;
  }
  return headers;
}

/** Headers for the control-plane acting as itself. */
export function vaultServiceHeaders() {
  return vaultIdentityHeaders({ username: VAULT_SERVICE_USER, groups: [VAULT_ADMIN_GROUP] });
}

/**
 * Headers for a tenant.
 *
 * Every tenant is an admin of their own library, because it holds only their
 * files. Roles exist in the library for deployments that share one, and the
 * per-tenant arrangement here has no use for a lesser role.
 */
export function vaultUserHeaders(user) {
  return vaultIdentityHeaders({
    username: user.email || `user-${user.id}`,
    email: user.email || '',
    groups: [VAULT_ADMIN_GROUP]
  });
}
