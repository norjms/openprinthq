// ArtisanHub Farm: there is no landing page. Everyone reaching the farm is
// already signed in to the hub, so its root goes straight to the app.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { redirect } from '@sveltejs/kit';
import { base } from '$app/paths';

export function load() {
  redirect(307, base + '/app');
}
