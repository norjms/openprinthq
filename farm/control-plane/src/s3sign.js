// OpenPrintHQ control-plane -- AWS SigV4 presigning for tenant object storage.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Why this exists rather than an SDK: the control-plane needs to mint short
// lived, single-object URLs and nothing else. The full S3 client is a large
// dependency tree for one signature, and bulk data must never flow through
// here anyway, so there is no client to reuse. This is the same trade already
// made in signing.js for connector commands.
//
// The security property being bought: a tenant never holds a long lived S3
// secret. Previously /api/storage/credentials handed the browser the bucket
// key, which could be read out of devtools and used forever, outside the app,
// with no way to revoke a single session. A presigned URL is scoped to one
// method, one object key and a few minutes.
import crypto from 'node:crypto';

const ALGO = 'AWS4-HMAC-SHA256';
const UNSIGNED = 'UNSIGNED-PAYLOAD';

/** RFC3986 escaping. encodeURIComponent leaves these four alone; S3 does not. */
function rfc3986(str) {
  return encodeURIComponent(str).replace(/[!'()*]/g, (c) =>
    '%' + c.charCodeAt(0).toString(16).toUpperCase());
}

/** Object keys keep their slashes: each segment is escaped, separators are not. */
function encodeKeyPath(key) {
  return String(key).split('/').map(rfc3986).join('/');
}

function hmac(key, data) {
  return crypto.createHmac('sha256', key).update(data, 'utf8').digest();
}
function sha256hex(data) {
  return crypto.createHash('sha256').update(data, 'utf8').digest('hex');
}

/** Date pair SigV4 wants: 20260827T154500Z and 20260827. */
export function amzDates(now = new Date()) {
  const iso = now.toISOString().replace(/[:-]|\.\d{3}/g, '');
  return { amzDate: iso, dateStamp: iso.slice(0, 8) };
}

function signingKey(secret, dateStamp, region, service) {
  return hmac(hmac(hmac(hmac('AWS4' + secret, dateStamp), region), service), 'aws4_request');
}

/**
 * Presign a single-object request as a query-string-authenticated URL.
 *
 * `endpoint` is the origin the CLIENT will actually connect to, and its host is
 * what gets signed. That matters: if a reverse proxy rewrites the Host header
 * the signature breaks, so the proxy in front of the store must preserve it.
 *
 * `pathPrefix` covers the case where the store is served under a sub-path and
 * the proxy strips it before the store sees the request. We sign the path the
 * STORE will see, and return a URL carrying the prefix, so both ends agree.
 */
export function presign({
  method = 'GET',
  endpoint,
  bucket,
  key,
  accessKeyId,
  secretAccessKey,
  region = 'garage',
  service = 's3',
  expiresIn = 900,
  pathPrefix = '',
  now = new Date()
}) {
  if (!endpoint) throw new Error('presign: endpoint required');
  if (!bucket || !key) throw new Error('presign: bucket and key required');
  if (!accessKeyId || !secretAccessKey) throw new Error('presign: credentials required');

  const url = new URL(endpoint);
  // Port is part of the signed host only when it is non-default, matching how
  // browsers and curl construct the Host header.
  const host = url.host;

  const { amzDate, dateStamp } = amzDates(now);
  const scope = `${dateStamp}/${region}/${service}/aws4_request`;

  // Path-style addressing throughout: bucket in the path, not the hostname.
  // Virtual-host style would need a wildcard certificate and a wildcard DNS
  // record per deployment, which self-hosters should not have to arrange.
  const canonicalUri = '/' + rfc3986(bucket) + '/' + encodeKeyPath(key);

  const q = {
    'X-Amz-Algorithm': ALGO,
    'X-Amz-Credential': `${accessKeyId}/${scope}`,
    'X-Amz-Date': amzDate,
    'X-Amz-Expires': String(expiresIn),
    'X-Amz-SignedHeaders': 'host'
  };
  const canonicalQuery = Object.keys(q).sort()
    .map((k) => `${rfc3986(k)}=${rfc3986(q[k])}`).join('&');

  const canonicalRequest = [
    method.toUpperCase(),
    canonicalUri,
    canonicalQuery,
    `host:${host}\n`,
    'host',
    UNSIGNED
  ].join('\n');

  const stringToSign = [ALGO, amzDate, scope, sha256hex(canonicalRequest)].join('\n');
  const signature = crypto
    .createHmac('sha256', signingKey(secretAccessKey, dateStamp, region, service))
    .update(stringToSign, 'utf8').digest('hex');

  const prefix = pathPrefix ? '/' + String(pathPrefix).replace(/^\/+|\/+$/g, '') : '';
  return {
    url: `${url.protocol}//${host}${prefix}${canonicalUri}?${canonicalQuery}&X-Amz-Signature=${signature}`,
    method: method.toUpperCase(),
    expiresAt: new Date(now.getTime() + expiresIn * 1000).toISOString(),
    signedHost: host
  };
}
