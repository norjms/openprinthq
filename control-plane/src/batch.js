// OpenPrintHQ control-plane — temperature-staggered batch orchestrator
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Sends one file to many printers, but serialises the heat-up phase so several
// machines never draw peak heater current at once. Each printer's queue item is
// created "held" (manual_start) and released only when its power circuit has a
// free preheating slot — i.e. the previous printer(s) on that circuit already
// reached bed + chamber target (or timed out). Printers on different circuits
// preheat in parallel. State lives in the DB so a control-plane restart resumes.

import {
  getInstanceForUser, getCircuits,
  createBatchRun, getBatchById, getActiveBatchForUser,
  listRunningBatches, updateBatchRun
} from './db.js';

const ENGINE_PORT = 8000;
function engineBase(inst) {
  return inst && inst.subdomain ? `http://ophq-${inst.subdomain}:${ENGINE_PORT}` : null;
}
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
const circuitKey = (s) => (s.circuit && String(s.circuit).trim()) || `__solo_${s.printerId}`;

async function eng(base, path, opts = {}) {
  const headers = { accept: 'application/json' };
  if (opts.body !== undefined) headers['content-type'] = 'application/json';
  const res = await fetch(base + path, { ...opts, headers });
  if (!res.ok) {
    let d; try { d = await res.json(); } catch { d = null; }
    const msg = (d && (d.detail?.message || d.detail || d.error)) || res.statusText;
    throw Object.assign(new Error(typeof msg === 'string' ? msg : 'engine error'), { status: res.status });
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

// Has this printer finished its heat-up surge? Requires the bed to be actively
// heating to a real target and within tolerance, and (if it has a chamber
// heater) the chamber too. A just-released print reports bed_target 0 until it
// starts, so that correctly reads as "still preheating".
function reachedTemp(status, tol) {
  const t = (status && status.temperatures) || {};
  const bedT = num(t.bed_target), bed = num(t.bed);
  const chT = num(t.chamber_target), ch = num(t.chamber);
  if (bedT <= 0) return false;
  const bedOk = bed >= bedT - tol;
  const chOk = chT <= 0 ? true : ch >= chT - tol;
  return bedOk && chOk;
}

async function releaseStep(base, step) {
  if (step.released) return;
  if (step.queueItemId == null) { step.released = true; step.reachedTemp = true; step.error = 'no queue item'; return; }
  try {
    await eng(base, `/api/v1/queue/${step.queueItemId}`, {
      method: 'PATCH', body: JSON.stringify({ manual_start: false })
    });
    step.released = true;
    step.releasedAt = new Date().toISOString();
  } catch (e) {
    // Item may have already been started/removed — treat as released so the
    // circuit keeps flowing rather than stalling forever.
    step.released = true;
    step.releasedAt = new Date().toISOString();
    step.error = e.message;
  }
}

// Create the held queue items and the batch record, then release the first
// slot(s) per circuit. Returns the persisted batch row.
export async function startBatch(user, opts) {
  const inst = await getInstanceForUser(user.id);
  const base = engineBase(inst);
  if (!base) { const err = new Error('no running instance'); err.status = 409; throw err; }

  const staggered = opts.staggered !== false;
  const circuits = await getCircuits(user.id);
  const steps = [];
  for (const p of opts.printers) {
    let queueItemId = null, error = null;
    try {
      const item = await eng(base, '/api/v1/queue/', {
        method: 'POST',
        body: JSON.stringify({
          library_file_id: opts.fileId,
          printer_id: p.id,
          // Staggered: hold every item; the orchestrator releases them. Not
          // staggered: everything starts at once (normal multi-send).
          manual_start: staggered
        })
      });
      queueItemId = item?.id ?? null;
    } catch (e) { error = e.message; }
    steps.push({
      printerId: p.id, printerName: p.name || `Printer ${p.id}`,
      circuit: circuits[p.id] || null,
      queueItemId, released: !staggered, releasedAt: staggered ? null : new Date().toISOString(),
      reachedTemp: false, reachedAt: null, timedOut: false, error
    });
  }

  const batch = await createBatchRun(user.id, {
    fileId: opts.fileId, fileName: opts.fileName,
    staggered, maxPreheat: Math.max(1, opts.maxPreheat || 1),
    tolerance: opts.tolerance ?? 3.0, maxWaitSecs: opts.maxWaitSecs ?? 900,
    steps
  });

  if (!staggered) {
    await updateBatchRun(batch.id, { status: 'completed' });
    return { ...batch, status: 'completed' };
  }
  // Release the initial preheating slot(s) per circuit, then let the tick loop
  // take over. Run one tick now so the first printers start immediately.
  await tickBatch(batch, base);
  return await getBatchById(batch.id);
}

// Advance a batch by evaluating temps + timeouts and releasing any circuit
// slots that have freed. `base` optional (resolved if absent).
async function tickBatch(batch, base) {
  if (batch.status !== 'running') return;
  if (!base) {
    const inst = await getInstanceForUser(batch.user_id);
    base = engineBase(inst);
    if (!base) return;
  }
  const steps = batch.steps || [];
  const tol = batch.tolerance ?? 3.0;
  const maxWaitMs = (batch.max_wait_secs ?? 900) * 1000;
  const now = Date.now();

  // Refresh temp/timeout status for every released-but-not-done step.
  for (const s of steps) {
    if (!s.released || s.reachedTemp || s.timedOut) continue;
    if (s.queueItemId == null) { s.reachedTemp = true; continue; }
    try {
      const st = await eng(base, `/api/v1/printers/${s.printerId}/status`);
      if (reachedTemp(st, tol)) { s.reachedTemp = true; s.reachedAt = new Date().toISOString(); }
    } catch { /* transient — retry next tick */ }
    if (!s.reachedTemp && s.releasedAt && now - Date.parse(s.releasedAt) > maxWaitMs) {
      s.timedOut = true;  // frees the slot so the batch keeps moving
    }
  }

  // Per circuit, keep the number of actively-preheating printers under the cap.
  const byCircuit = new Map();
  steps.forEach((s, i) => {
    const k = circuitKey(s);
    if (!byCircuit.has(k)) byCircuit.set(k, []);
    byCircuit.get(k).push(i);
  });
  const cap = Math.max(1, batch.max_preheat || 1);
  for (const idxs of byCircuit.values()) {
    let preheating = idxs.filter((i) => steps[i].released && !steps[i].reachedTemp && !steps[i].timedOut).length;
    for (const i of idxs) {
      if (steps[i].released) continue;
      if (preheating >= cap) break;
      await releaseStep(base, steps[i]);
      preheating++;
    }
  }

  const allReleased = steps.every((s) => s.released);
  await updateBatchRun(batch.id, { steps, status: allReleased ? 'completed' : 'running' });
}

// Global tick — scan every running batch. Guarded against overlap.
let ticking = false;
export async function tickAll() {
  if (ticking) return;
  ticking = true;
  try {
    const running = await listRunningBatches();
    for (const b of running) {
      try { await tickBatch(b); } catch (e) { console.error('batch tick failed', b.id, e.message); }
    }
  } finally { ticking = false; }
}

export function startOrchestrator(intervalMs = 8000) {
  setInterval(() => { tickAll().catch((e) => console.error('tickAll', e.message)); }, intervalMs);
}

// Manual override: release the next held printer immediately, ignoring temps.
export async function advanceBatch(batch) {
  const inst = await getInstanceForUser(batch.user_id);
  const base = engineBase(inst);
  if (!base) return batch;
  const steps = batch.steps || [];
  const next = steps.find((s) => !s.released);
  if (next) await releaseStep(base, next);
  const allReleased = steps.every((s) => s.released);
  await updateBatchRun(batch.id, { steps, status: allReleased ? 'completed' : 'running' });
  return await getBatchById(batch.id);
}

// Cancel: delete the still-held (unreleased) queue items and close the batch.
// Already-released items stay in the normal queue for the user to manage.
export async function cancelBatch(batch) {
  const inst = await getInstanceForUser(batch.user_id);
  const base = engineBase(inst);
  const steps = batch.steps || [];
  if (base) {
    for (const s of steps) {
      if (!s.released && s.queueItemId != null) {
        try { await eng(base, `/api/v1/queue/${s.queueItemId}`, { method: 'DELETE' }); s.cancelled = true; }
        catch { /* best effort */ }
      }
    }
  }
  await updateBatchRun(batch.id, { steps, status: 'cancelled' });
  return await getBatchById(batch.id);
}

// Enrich the active batch with a live temp snapshot for printers still preheating,
// so the UI can show progress toward target.
export async function activeBatchForUser(user) {
  const batch = await getActiveBatchForUser(user.id);
  if (!batch) return null;
  const inst = await getInstanceForUser(user.id);
  const base = engineBase(inst);
  const steps = batch.steps || [];
  const snaps = {};
  if (base) {
    await Promise.all(steps
      .filter((s) => s.released && !s.reachedTemp && !s.timedOut)
      .map(async (s) => {
        try {
          const st = await eng(base, `/api/v1/printers/${s.printerId}/status`);
          const t = (st && st.temperatures) || {};
          snaps[s.printerId] = {
            bed: num(t.bed), bedTarget: num(t.bed_target),
            chamber: num(t.chamber), chamberTarget: num(t.chamber_target)
          };
        } catch { /* ignore */ }
      }));
  }
  return { ...batch, temps: snaps };
}
