// The last breadcrumb for a detail page (a printer's or a file's name). A detail
// page sets it for its own path; the app shell shows it only on that path.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { writable } from 'svelte/store';

export const crumbDetail = writable({ path: '', label: '' });

export function setCrumb(label) {
  if (typeof window === 'undefined') return;
  crumbDetail.set({ path: window.location.pathname, label: label || '' });
}
