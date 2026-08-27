// OpenPrintHQ control-plane -- Garage (S3) tenant storage.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Each tenant gets their own bucket plus an access key scoped to it, created on
// first use. Nothing is shared between tenants: no common bucket, no prefix
// policies to get wrong.
//
// IMPORTANT, and the reason this module only ever speaks to the ADMIN api:
// bulk data must never flow through the control-plane. Slicer sessions and the
// engine talk S3 to the object store directly over the network. What travels
// here is provisioning and accounting only, a few hundred bytes per call, which
// is why it is fine for this to run from a control-plane that is off-site.
const ADMIN_URL = (process.env.OPHQ_GARAGE_ADMIN_URL || '').replace(/\/+$/, '');
const ADMIN_TOKEN = process.env.OPHQ_GARAGE_ADMIN_TOKEN || '';

// The S3 endpoint. Deliberately separate from ADMIN_URL: the admin API must
// never be reachable by a slicer session, or a tenant could raise their own
// quota.
//
// There are two of these because the store has two classes of client sitting in
// different places on the network, and handing either one the other's address
// fails in a way that is tedious to diagnose:
//
//   PUBLIC  browsers, which are off-site. Must be a routable name with a valid
//           certificate. Cannot be an RFC-1918 address, which is what the
//           single-endpoint version was, so web uploads could never have worked.
//   LAN     slicer sessions and the engine, which sit on the same network as the
//           store and should not hairpin out to the internet and back to move a
//           few hundred megabytes.
//
// If only the legacy OPHQ_GARAGE_S3_ENDPOINT is set, both fall back to it, so an
// existing deployment keeps working until its .env is updated.
const S3_LEGACY = (process.env.OPHQ_GARAGE_S3_ENDPOINT || '').replace(/\/+$/, '');
const S3_PUBLIC = (process.env.OPHQ_GARAGE_S3_ENDPOINT_PUBLIC || S3_LEGACY).replace(/\/+$/, '');
const S3_LAN = (process.env.OPHQ_GARAGE_S3_ENDPOINT_LAN || S3_LEGACY).replace(/\/+$/, '');
// A third address, because "on the LAN" is not one place. Slicer sessions run on
// a separate VLAN and reach the store by its LAN address. The engine runs as a
// container on the SAME host as the control-plane, which off-site deployments
// firewall off from the LAN entirely, so it reaches the store through a
// host-side forwarder on the bridge IP instead. Handing the engine the VLAN
// address there produces a silent timeout, not a routing error.
// Falls back to LAN, which is correct whenever both sit on one network.
const S3_ENGINE = (process.env.OPHQ_GARAGE_S3_ENDPOINT_ENGINE || '').replace(/\/+$/, '');
// Set when the store is served under a sub-path the proxy strips before the
// store sees it. Signed paths must match the store's view, not the URL.
const S3_PATH_PREFIX = process.env.OPHQ_GARAGE_S3_PATH_PREFIX || '';
const S3_REGION = process.env.OPHQ_GARAGE_S3_REGION || 'garage';
// How long a presigned URL stays valid. Short by design: it is minted on demand,
// so a longer window buys nothing and widens the replay gap.
const PRESIGN_TTL = Number(process.env.OPHQ_GARAGE_PRESIGN_TTL || 0) || 900;

// A single long-lived key with READ access to every tenant bucket, used only by
// the host-side rclone mount that exposes buckets to the engines as read-only
// library folders. It is created once by an operator and referenced by id here,
// because CreateKey is not idempotent by name and calling it per provision would
// quietly accumulate a key per tenant.
//
// It never leaves the host. Tenants are isolated by bind-mounting only their own
// bucket directory into their own engine, not by the key.
const READER_KEY_ID = process.env.OPHQ_GARAGE_READER_KEY_ID || '';

const DEFAULT_QUOTA_BYTES = Number(process.env.OPHQ_TENANT_QUOTA_BYTES || 0) || 5 * 1024 * 1024 * 1024;

export function garageConfigured() { return !!(ADMIN_URL && ADMIN_TOKEN && S3_PUBLIC); }
export function defaultQuotaBytes() { return DEFAULT_QUOTA_BYTES; }
export function s3EndpointPublic() { return S3_PUBLIC; }
export function s3EndpointLan() { return S3_LAN || S3_PUBLIC; }
export function s3EndpointEngine() { return S3_ENGINE || S3_LAN || S3_PUBLIC; }
export function s3PathPrefix() { return S3_PATH_PREFIX; }
export function s3Region() { return S3_REGION; }
export function presignTtl() { return PRESIGN_TTL; }
export function readerKeyId() { return READER_KEY_ID; }

async function admin(endpoint, { method = 'POST', body = null, query = '' } = {}) {
  if (!garageConfigured()) throw new Error('object storage not configured');
  const res = await fetch(`${ADMIN_URL}/v2/${endpoint}${query}`, {
    method,
    headers: {
      authorization: `Bearer ${ADMIN_TOKEN}`,
      ...(body ? { 'content-type': 'application/json' } : {})
    },
    ...(body ? { body: JSON.stringify(body) } : {})
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`storage ${endpoint} failed (${res.status}): ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : {};
}

export async function clusterHealth() {
  return admin('GetClusterHealth', { method: 'GET' });
}

/** Bucket name for a tenant. Aliases are global, so this must be unique. */
export function bucketNameFor(userId, email) {
  const slug = String(email || '').toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '');
  return `ophq-${userId}-${slug}`.slice(0, 60);
}

export async function getBucket(alias) {
  try {
    return await admin('GetBucketInfo', { method: 'GET', query: `?globalAlias=${encodeURIComponent(alias)}` });
  } catch (e) {
    if (/404|NoSuchBucket|not found/i.test(e.message)) return null;
    throw e;
  }
}

/**
 * Create the bucket, a key scoped to it, and apply the quota. Idempotent: a
 * half-finished provision (bucket but no key, say) is completed rather than
 * duplicated, because this runs on login and will be retried.
 */
export async function ensureTenantStorage(userId, email, quotaBytes = DEFAULT_QUOTA_BYTES) {
  const alias = bucketNameFor(userId, email);
  let bucket = await getBucket(alias);
  if (!bucket) bucket = await admin('CreateBucket', { body: { globalAlias: alias } });

  const key = await admin('CreateKey', { body: { name: `${alias}-key` } });
  await admin('AllowBucketKey', {
    body: {
      bucketId: bucket.id,
      accessKeyId: key.accessKeyId,
      // Never owner: owner lets a key change bucket configuration, which
      // includes its own quota.
      permissions: { read: true, write: true, owner: false }
    }
  });
  // Let the library reader see this bucket, read-only. Best effort: a tenant
  // whose bucket is not readable loses the library-folder view, which is a
  // degraded feature, not a broken account, and it is retried on next provision.
  if (READER_KEY_ID) {
    try {
      await admin('AllowBucketKey', {
        body: {
          bucketId: bucket.id,
          accessKeyId: READER_KEY_ID,
          permissions: { read: true, write: false, owner: false }
        }
      });
    } catch { /* degraded, not fatal */ }
  }

  await setQuota(bucket.id, quotaBytes);

  return {
    bucketId: bucket.id,
    bucket: alias,
    accessKeyId: key.accessKeyId,
    secretAccessKey: key.secretAccessKey,
    endpoint: S3_LAN || S3_PUBLIC,
    region: S3_REGION,
    quotaBytes
  };
}

/** Both quota fields are required on every update; null means unlimited. */
export async function setQuota(bucketId, maxSizeBytes, maxObjects = null) {
  return admin('UpdateBucket', {
    query: `?id=${encodeURIComponent(bucketId)}`,
    body: { quotas: { maxSize: maxSizeBytes ?? null, maxObjects } }
  });
}

/** Usage and quota in one call, so accounting costs one request per tenant. */
export async function usage(alias) {
  const b = await getBucket(alias);
  if (!b) return null;
  return {
    bytes: b.bytes ?? 0,
    objects: b.objects ?? 0,
    quotaBytes: b.quotas?.maxSize ?? null,
    overQuota: b.quotas?.maxSize != null && (b.bytes ?? 0) >= b.quotas.maxSize
  };
}

/**
 * Revoke a tenant's write access, leaving reads intact.
 *
 * The store enforces the quota itself on PUT, so this is a backstop rather than
 * the primary control: it exists for suspending an account, or if a quota is
 * lowered below current usage and we want writes to stop immediately.
 */
export async function setWritable(bucketId, accessKeyId, writable) {
  return admin(writable ? 'AllowBucketKey' : 'DenyBucketKey', {
    body: { bucketId, accessKeyId, permissions: { read: false, write: true, owner: false } }
  });
}
