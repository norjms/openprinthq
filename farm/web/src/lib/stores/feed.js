// Set by a page that draws a live camera feed behind the whole app (the printer
// view). The app shell then leaves its own background clear so the feed shows.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { writable } from 'svelte/store';

export const liveFeed = writable(false);
