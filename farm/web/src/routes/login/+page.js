// ArtisanHub Farm has no sign-in page of its own: the hub's sign-in (Authelia)
// sits in front of it, so anyone here is already signed in. Old links land in
// the app.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { redirect } from '@sveltejs/kit';
import { base } from '$app/paths';

export function load() {
  redirect(307, base + '/app');
}
