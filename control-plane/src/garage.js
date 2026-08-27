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

// The S3 endpoint handed to tenants. Deliberately separate from ADMIN_URL: the
// admin API must never be reachable by a slicer session, or a tenant could
// raise their own quota.
const S3_ENDPOINT = (process.env.OPHQ_GARAGE_S3_ENDPOINT || '').replace(/\/+$/, '');
const S3_REGION = process.env.OPHQ_GARAGE_S3_REGION || 'garage';

const DEFAULT_QUOTA_BYTES = Number(process.env.OPHQ_TENANT_QUOTA_BYTES || 0) || 5 * 1024 * 1024 * 1024;

export function garageConfigured() { return !!(ADMIN_URL && ADMIN_TOKEN && S3_ENDPOINT); }
export function defaultQuotaBytes() { return DEFAULT_QUOTA_BYTES; }
export function s3Endpoint() { return S3_ENDPOINT; }
export function s3Region() { return S3_REGION; }

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
  await setQuota(bucket.id, quotaBytes);

  return {
    bucketId: bucket.id,
    bucket: alias,
    accessKeyId: key.accessKeyId,
    secretAccessKey: key.secretAccessKey,
    endpoint: S3_ENDPOINT,
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
