// OpenPrintHQ control-plane — connector command signing (RSA-2048)
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Copied to control-plane/src/signing.js. Each account can hold one RSA-2048
// key pair. The control-plane keeps the PRIVATE key (encrypted at rest) and
// signs every *command* it pushes to a connector (http / tcp-open / tcp-probe).
// The user copies the PUBLIC key into their connector (OPHQ_SIGNING_PUBKEY);
// the agent then rejects any command that isn't signed by the matching private
// key — so a spoofed or hijacked control-plane endpoint cannot drive the agent.
//
// Best practice: RSA-2048, RSA-PSS padding + SHA-256, salt = digest length,
// private key AES-256-GCM encrypted at rest, signed payload carries a timestamp
// for replay defence.
import crypto from 'node:crypto';
import { getSigningPrivateEnc } from './db.js';

const GATEWAY_SECRET = process.env.OPHQ_GATEWAY_SECRET || '';
// Key-encryption key for private keys at rest (distinct domain from the gateway).
const KEK = crypto.createHash('sha256').update('ophq-connector-signing-kek|' + GATEWAY_SECRET).digest();

// Cache decrypted private KeyObjects per user so we don't decrypt per job.
const privCache = new Map();   // userId -> crypto.KeyObject
export function invalidateSigningCache(userId) { privCache.delete(userId); }

export function generateKeyPair() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('rsa', {
    modulusLength: 2048,
    publicKeyEncoding: { type: 'spki', format: 'pem' },
    privateKeyEncoding: { type: 'pkcs8', format: 'pem' }
  });
  return { publicPem: publicKey, privatePem: privateKey };
}

export function encryptPrivate(pem) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', KEK, iv);
  const ct = Buffer.concat([cipher.update(pem, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, ct]).toString('base64');
}
export function decryptPrivate(enc) {
  const buf = Buffer.from(enc, 'base64');
  const iv = buf.subarray(0, 12), tag = buf.subarray(12, 28), ct = buf.subarray(28);
  const d = crypto.createDecipheriv('aes-256-gcm', KEK, iv);
  d.setAuthTag(tag);
  return Buffer.concat([d.update(ct), d.final()]).toString('utf8');
}

// Deterministic bytes to sign/verify — fixed field order, no object-key-order
// ambiguity (headers/body are stringified as-is; the same object round-trips
// identically through JSON on both sides).
export function canonJob(j) {
  return Buffer.from(JSON.stringify([
    j.id ?? null, j.ts ?? null, j.kind ?? null,
    j.host ?? null, j.port ?? null, j.scheme ?? null,
    j.path ?? null, j.method ?? null, j.headers ?? null, j.body ?? null
  ]));
}

const PSS = { padding: crypto.constants.RSA_PKCS1_PSS_PADDING, saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST };

async function privKey(userId) {
  if (privCache.has(userId)) return privCache.get(userId);
  const enc = await getSigningPrivateEnc(userId);
  if (!enc) return null;
  let key = null;
  try { key = crypto.createPrivateKey(decryptPrivate(enc)); } catch { key = null; }
  privCache.set(userId, key);
  return key;
}

// Commands (not the high-rate tcp-data/tcp-close stream traffic) are signed.
export function isCommand(job) {
  return job.kind === undefined || job.kind === 'tcp-open' || job.kind === 'tcp-probe';
}

// Mutates `job`: adds ts + sig when the account has a signing key and the job is
// a command. No-op otherwise (backward compatible with unsigned connectors).
export async function signJobIfKeyed(userId, job) {
  if (!isCommand(job)) return job;
  const key = await privKey(userId);
  if (!key) return job;
  job.ts = Date.now();
  job.sig = crypto.sign('sha256', canonJob(job), { key, ...PSS }).toString('base64');
  return job;
}
