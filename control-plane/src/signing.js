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
import { getSigningPrivateEnc, getSigningPublic, ensureSigningKey } from './db.js';

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

// Which jobs get signed.
//
// This is a DENY-list, deliberately. Everything the control-plane pushes to a
// connector is signed except traffic riding an already-authorised tunnel.
//
// It used to be an allow-list naming tcp-open and tcp-probe, which meant every
// job kind added since (discover, camera-frame, camera-webrtc, camera-register)
// silently shipped unsigned. Each of those makes the agent originate a NEW
// connection to a LAN address, which is exactly what signing exists to
// authorise. An allow-list fails open every time someone adds a feature; a
// deny-list fails safe.
//
// Only add a kind here if it rides a tunnel that a signed tcp-open already
// authorised, and if signing it would be a measurable throughput problem.
const STREAM_KINDS = new Set(['tcp-data', 'tcp-close']);

export function isCommand(job) {
  return !STREAM_KINDS.has(job.kind);
}

// SHA-256 over the DER SPKI bytes, base64. Shown to the operator when a
// connector pins the key, so the pin can be confirmed out of band.
export function fingerprint(publicPem) {
  const der = crypto.createPublicKey(publicPem).export({ type: 'spki', format: 'der' });
  return crypto.createHash('sha256').update(der).digest('base64');
}

// Idempotent. Guarantees the account has a signing key pair, so no account can
// sit in a state where the control-plane is unable to sign. Safe to call on a
// hot path: it short-circuits on the cheap read once a key exists.
export async function ensureKeyPair(userId) {
  const existing = await getSigningPublic(userId);
  if (existing?.public_pem) return existing.public_pem;
  const { publicPem, privatePem } = generateKeyPair();
  // Insert-if-absent, not upsert: losing a concurrent race must return the key
  // that won rather than replacing it, or a connector that already pinned the
  // winner would start rejecting every command.
  const inForce = await ensureSigningKey(userId, publicPem, encryptPrivate(privatePem));
  invalidateSigningCache(userId);
  return inForce || publicPem;
}

export class SigningUnavailableError extends Error {
  constructor(userId, cause) {
    super(`no usable signing key for user ${userId}`);
    this.name = 'SigningUnavailableError';
    this.statusCode = 503;
    this.cause = cause;
  }
}

// Mutates `job`: adds ts + sig. Throws rather than emitting an unsigned command.
//
// Shipping unsigned on a missing key is a silent downgrade: any connector that
// still accepts unsigned commands would execute it, and nothing anywhere would
// record that authentication had been skipped. If we cannot sign, we do not
// send.
//
// Self-heals a missing key pair rather than failing, so rolling this out does
// not depend on the backfill having run first. Throws only when signing is
// genuinely impossible: database unavailable, or a private key that will not
// decrypt.
export async function signJob(userId, job) {
  if (!isCommand(job)) return job;   // rides a tunnel a signed tcp-open authorised
  let key = await privKey(userId);
  if (!key) {
    try {
      await ensureKeyPair(userId);
      key = await privKey(userId);
    } catch (err) {
      throw new SigningUnavailableError(userId, err);
    }
  }
  if (!key) throw new SigningUnavailableError(userId);
  job.ts = Date.now();
  job.sig = crypto.sign('sha256', canonJob(job), { key, ...PSS }).toString('base64');
  return job;
}

// Deprecated alias, kept for exactly one release so that any call site missed
// during the rename is noisy rather than silently unsigned. Remove after the
// Phase 2 agent release.
let warnedAlias = false;
export async function signJobIfKeyed(userId, job) {
  if (!warnedAlias) {
    warnedAlias = true;
    console.warn('[signing] DEPRECATED: signJobIfKeyed() called. Use signJob(). This alias is removed after the Phase 2 agent release.');
  }
  return signJob(userId, job);
}
