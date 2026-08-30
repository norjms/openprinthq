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

  scan: () => req('/library/scan', { method: 'POST' })
};

/** Which file of a model to preview: a real mesh if there is one, else the
 *  first g-code. Ordering matters more than it looks, since a model usually
 *  holds both and the mesh is the one worth showing. */
export function previewFile(model) {
  const files = model?.files || [];
  const by = (t) => files.find((f) => f.file_type === t);
  return by('stl') || by('3mf') || by('gcode') || by('bgcode') || files[0] || null;
}

export const PREVIEWABLE = new Set(['stl', '3mf', 'gcode', 'bgcode']);
