#!/usr/bin/env node
// Provision a command-signing key pair for every account that lacks one.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Phase 1 of the connector command-signing rollout.
//
// signJob() self-heals a missing key pair on first use, so this script is an
// optimisation rather than a prerequisite: it moves key generation off the hot
// path and out of the first job's latency budget. Roughly 100ms of RSA-2048
// keygen per account, done once, here, instead of in front of a user.
//
// Idempotent. Safe to re-run. Makes no change to accounts that already have a
// key, and never rotates an existing one, because rotation would break every
// paired connector.
//
//   DATABASE_URL=... node scripts/backfill-signing-keys.mjs [--dry-run]

import pg from 'pg';
import { generateKeyPair, encryptPrivate, fingerprint } from '../control-plane/src/signing.js';

const DRY = process.argv.includes('--dry-run');

if (!process.env.OPHQ_GATEWAY_SECRET) {
  console.error('FATAL: OPHQ_GATEWAY_SECRET is not set.');
  console.error('It derives the key-encryption key. Running without the value the');
  console.error('control-plane uses would write private keys that the control-plane');
  console.error('cannot decrypt, and every signed command would then fail.');
  process.exit(1);
}

const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });

const { rows } = await pool.query(`
  SELECT u.id, u.email
    FROM users u
    LEFT JOIN signing_keys k ON k.user_id = u.id
   WHERE k.user_id IS NULL
   ORDER BY u.id
`);

console.log(`${rows.length} account(s) without a signing key${DRY ? ' (dry run)' : ''}`);

let done = 0, failed = 0;
for (const u of rows) {
  try {
    if (DRY) { console.log(`  would provision user ${u.id} <${u.email}>`); done++; continue; }
    const { publicPem, privatePem } = generateKeyPair();
    // ON CONFLICT DO NOTHING: another process may have provisioned this account
    // between the SELECT and now. Losing that race must not overwrite a key a
    // connector has already pinned.
    const res = await pool.query(
      `INSERT INTO signing_keys (user_id, public_pem, private_enc, created_at)
       VALUES ($1, $2, $3, now())
       ON CONFLICT (user_id) DO NOTHING`,
      [u.id, publicPem, encryptPrivate(privatePem)]
    );
    if (res.rowCount === 0) { console.log(`  user ${u.id}: already provisioned by another process, skipped`); continue; }
    console.log(`  user ${u.id} <${u.email}>  fingerprint ${fingerprint(publicPem)}`);
    done++;
  } catch (err) {
    failed++;
    console.error(`  user ${u.id}: FAILED ${err.message}`);
  }
}

// Verify round-trip on a sample, so a broken key-encryption key is caught here
// rather than by every command failing in production.
if (!DRY && done > 0) {
  const { decryptPrivate } = await import('../control-plane/src/signing.js');
  const { rows: sample } = await pool.query('SELECT user_id, private_enc FROM signing_keys ORDER BY created_at DESC LIMIT 5');
  for (const r of sample) {
    try { decryptPrivate(r.private_enc); }
    catch { console.error(`  VERIFY FAILED: user ${r.user_id} private key does not decrypt. Check OPHQ_GATEWAY_SECRET.`); failed++; }
  }
}

console.log(`provisioned ${done}, failed ${failed}`);
await pool.end();
process.exit(failed > 0 ? 1 : 0);
