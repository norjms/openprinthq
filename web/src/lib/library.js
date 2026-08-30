// Client for the tenant model library.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The library is our fork of GyroidVault, one container per tenant. It used to
// be reached by framing its own host; these calls go through the control-plane
// on OUR origin instead, so the request is authenticated the same way every
// other /api call is and there is no ticket, cookie or frame in the path.
//
// Three prefixes, matching the three path spaces the library serves:
//   /api/library/*        its API
//   /api/library-file/*   file content
//   /api/library-thumb/*  thumbnails

import { api, putWithProgress } from '$lib/api.js';

const API = '/api/library';

async function req(path, opts = {}) {
  const { headers: optHeaders, ...rest } = opts;
  const headers = { ...(optHeaders || {}) };
  if (rest.body !== undefined && !(rest.body instanceof FormData) && !('content-type' in headers)) {
    headers['content-type'] = 'application/json';
  }
  const res = await fetch(API + path, { headers, credentials: 'include', ...rest });
  if (!res.ok) {
    let d = null;
    try { d = await res.json(); } catch { /* not json */ }
    const msg = (d && (d.error || d.message)) || res.statusText || 'request failed';
    throw Object.assign(new Error(msg), { status: res.status });
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

/**
 * Rewrite a URL the library put in a payload onto our own origin.
 *
 * The library answers with its own absolute paths (/library-files/x for file
 * content, /uploads/y for thumbnails) because on its own host those are
 * correct. Served from here they would resolve against the app root, so every
 * image would 404 quietly. Done at the point of use rather than by rewriting
 * the JSON body, since that would mean knowing every field that can hold a URL
 * and getting the list wrong fails silently.
 */
export function libraryAsset(url) {
  if (!url) return '';
  if (url.startsWith('/library-files/')) return '/api/library-file/' + url.slice('/library-files/'.length);
  if (url.startsWith('/uploads/')) return '/api/library-thumb/' + url.slice('/uploads/'.length);
  return url;
}

const qs = (params) => {
  const p = new URLSearchParams(
    Object.entries(params || {}).filter(([, v]) => v !== '' && v != null && v !== false)
  ).toString();
  return p ? '?' + p : '';
};

export const library = {
  /** Is there a library for this tenant? Note this is NOT under /api/library:
   *  it is answered by the control-plane, so it works when there is none. */
  status: async () => {
    const res = await fetch('/api/library-status', { credentials: 'include' });
    if (!res.ok) return { available: false };
    return res.json();
  },

  me: () => req('/auth/me'),
  stats: () => req('/stats'),

  models: (params) => req('/models' + qs(params)),
  model: (id) => req(`/models/${id}`),
  updateModel: (id, data) => req(`/models/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteModel: (id, deleteDisk = false) => req(`/models/${id}?deleteDisk=${deleteDisk}`, { method: 'DELETE' }),

  categories: () => req('/categories'),
  tags: () => req('/tags'),

  files: {
    /**
     * Queue a library file for printing.
     *
     * This goes through the library's own printer integration, which speaks
     * Moonraker to a shim on the control-plane that puts the job in the
     * OpenPrintHQ queue. The indirection is a leftover from when the library
     * was a separate application and could not call our API. Keeping the call
     * behind this one function means replacing it later is a change here and
     * nowhere else.
     */
    sendToPrinter: (fileId, printerId = 'openprinthq') =>
      req(`/files/${fileId}/send-to-printer`, {
        method: 'POST',
        body: JSON.stringify({ printer_id: printerId })
      }),
    remove: (fileId, deleteDisk = false) =>
      req(`/files/${fileId}?deleteDisk=${deleteDisk}`, { method: 'DELETE' })
  },

  scan: () => req('/library/scan', { method: 'POST' }),

  browse: (path = '') => req('/browse' + qs({ path })),
  tree: () => req('/browse/tree'),
  searchFiles: (q) => req('/browse/search' + qs({ q })),

  projects: {
    list: () => req('/projects'),
    get: (id) => req(`/projects/${id}`),
    create: (data) => req('/projects', { method: 'POST', body: JSON.stringify(data) }),
    remove: (id) => req(`/projects/${id}`, { method: 'DELETE' }),
    addModel: (projectId, modelId) =>
      req(`/projects/${projectId}/models`, { method: 'POST', body: JSON.stringify({ model_id: modelId }) }),
    removeModel: (projectId, modelId) =>
      req(`/projects/${projectId}/models/${modelId}`, { method: 'DELETE' })
  },

  prints: {
    add: (modelId, data) =>
      req(`/models/${modelId}/prints`, { method: 'POST', body: JSON.stringify(data) }),
    remove: (id) => req(`/prints/${id}`, { method: 'DELETE' })
  },

  materials: () => req('/materials'),

  settings: {
    duplicates: () => req('/system/duplicates'),
    createCategory: (data) => req('/categories', { method: 'POST', body: JSON.stringify(data) }),
    deleteCategory: (id) => req(`/categories/${id}`, { method: 'DELETE' }),
    createTag: (data) => req('/tags', { method: 'POST', body: JSON.stringify(data) }),
    deleteTag: (id) => req(`/tags/${id}`, { method: 'DELETE' }),
    createMaterial: (data) => req('/materials', { method: 'POST', body: JSON.stringify(data) }),
    deleteMaterial: (id) => req(`/materials/${id}`, { method: 'DELETE' })
  }
};

/**
 * Upload a file into the tenant's library.
 *
 * The bytes go browser -> object store on a presigned URL and never through
 * the control-plane, which is the same path the rest of the app uses. They do
 * NOT go through the library: its bucket mount is deliberately read-only, so
 * its own upload endpoint would fail, and a file written into its private
 * volume instead would be invisible to the engine and uncounted against quota.
 *
 * The store is written directly, so nothing knows the object exists until
 * something scans. /api/storage/rescan re-indexes the engine AND the library,
 * which are two separate indexes over the same bucket and both blind to a
 * write made from outside.
 */
export async function makeLibraryFolder(parentPath = '', folderName) {
  const clean = String(folderName || '').trim().replace(/[\\/]/g, '');
  if (!clean) throw new Error('a folder needs a name');
  const prefix = (parentPath ? parentPath.replace(/^\/+|\/+$/g, '') + '/' : '') + clean;

  // NOT the library's own mkdir. Its bucket mount is read-only by design, so
  // that endpoint fails with "Failed to create directory", which reads like a
  // permissions bug rather than the deliberate constraint it is.
  //
  // On an object store a folder is not a thing that gets created: it exists
  // exactly as long as some key carries its prefix. A zero-byte marker is what
  // makes an empty one visible through the mount, and it is also what the
  // folder would have contained anyway once a file landed in it.
  const signed = await api.presign({ method: 'PUT', key: `${prefix}/.keep` });
  if (!signed?.url) throw new Error('object storage is not configured for this deployment');
  await putWithProgress(signed.url, new Blob([]));
  await api.rescan().catch(() => {});
  return { path: prefix };
}

/**
 * The object-store key behind a library file.
 *
 * The library reports two shapes for the same object: `url` as
 * `/library-files/<path>` and `library_path` as `/library/<path>`. Both are
 * views of the bucket mounted at /library, so the key is whatever follows.
 */
export function objectKeyFor(file) {
  const raw = file?.url || file?.library_path || '';
  for (const prefix of ['/library-files/', '/library/']) {
    if (raw.startsWith(prefix)) return raw.slice(prefix.length);
  }
  return '';
}

/**
 * Delete a file from the tenant's storage.
 *
 * Through the OBJECT STORE, not the library. Its bucket mount is read-only by
 * design, so the library's own delete cannot unlink anything and fails in a way
 * that reads like a permissions bug. Deleting the object is what actually
 * removes the file; the rescan is what removes it from both indexes.
 */
export async function deleteLibraryObject(key) {
  if (!key) throw new Error('no object key for that file');
  const signed = await api.presign({ method: 'DELETE', key });
  if (!signed?.url) throw new Error('object storage is not configured for this deployment');
  const res = await fetch(signed.url, { method: 'DELETE' });
  // S3 answers 204 for a delete, and also for a key that was never there.
  if (!res.ok && res.status !== 404) {
    throw new Error(`the object store refused the delete (${res.status})`);
  }
  await api.rescan().catch(() => {});
}

/**
 * Copy or move an object inside the tenant's storage.
 *
 * Server-side, through the control-plane, which drives the store's own
 * CopyObject. Doing it here would mean downloading the file and uploading it
 * again, which for a large plate is the whole file twice through the browser
 * for something the store does internally.
 */
export async function copyLibraryObject(from, to, { move = false } = {}) {
  const res = await fetch('/api/storage/copy', {
    method: 'POST',
    credentials: 'include',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ from, to, move })
  });
  if (!res.ok) {
    let d = null; try { d = await res.json(); } catch { /* not json */ }
    throw new Error((d && d.error) || res.statusText || 'copy failed');
  }
  return res.json();
}

export async function uploadToLibrary(file, folderPath = '', onProgress) {
  const key = (folderPath ? folderPath.replace(/^\/+|\/+$/g, '') + '/' : '') + file.name;
  const signed = await api.presign({ method: 'PUT', key });
  if (!signed?.url) throw new Error('object storage is not configured for this deployment');
  await putWithProgress(signed.url, file, onProgress);
  // Never fatal: the object is stored either way and the next scan finds it.
  await api.rescan().catch(() => {});
  return { key: signed.key };
}

/** Which file of a model to preview: a real mesh if there is one, else the
 *  first g-code. Ordering matters more than it looks, since a model usually
 *  holds both and the mesh is the one worth showing. */
export function previewFile(model) {
  const files = model?.files || [];
  const by = (t) => files.find((f) => f.file_type === t);
  return by('stl') || by('3mf') || by('gcode') || by('bgcode') || files[0] || null;
}

export const PREVIEWABLE = new Set(['stl', '3mf', 'gcode', 'bgcode']);
