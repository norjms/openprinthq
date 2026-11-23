# Your shop's logo

A hub can show its shop's own logo in place of the OpenPrintHQ wordmark. Upload it on **Settings, Shop and crafts**:
an SVG or a PNG, wide rather than tall, at most 256 KB. The hub keeps it in its data folder (`brand/`), so it stays
through updates and backups and needs nothing from whoever installed the hub. The phone app draws it; the hub's own
pages show the shop's name.

An SVG is refused when it could run or load anything: scripts, event handlers, `foreignObject`, entities, or links
and styles that reach outside the file. Fonts it names are used when the device has them. Without an uploaded logo
the phone app draws `HUB_WORDMARK` (`docs/configuration.md`).

## `GET /api/brand/v1`

No authentication. `{"wordmark": "/api/brand/v1/wordmark?v=<version>"}`, or `{"wordmark": null}` without a logo.
The version changes with the file.

## `GET /api/brand/v1/wordmark`

No authentication. The logo, as `image/svg+xml` or `image/png`, served with a policy that lets it run and load
nothing. 404 without one.

## `PUT /api/brand/v1/wordmark` and `DELETE /api/brand/v1/wordmark`

Multipart, field `file`. Changing the logo needs the hub owner, or the settings permission in the installation's
first team. 401 not signed in, 403 not allowed, 422 not an acceptable file (the reason says which rule).
