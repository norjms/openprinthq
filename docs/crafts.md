# Crafts

A hub shows tools for the crafts its makers do. **3D Printing** is always on: printers, the print queue, filament,
slicing and the farm are the hub's core. The other crafts are optional, chosen once for the whole installation:

| Craft | `id` | What it adds |
|---|---|---|
| 3D Printing | `3d_printing` | Always on. |
| Laser Crafting | `laser` | A Laser Crafting group in the sidebar: an overview page at `/crafts/laser` and Convert files, the OpenPrintHQ Laser app at `/apps/laser`. |
| Sublimation | `sublimation` | A Sublimation group with a page at `/crafts/sublimation`. Tools are coming. |
| Graphic Art | `graphic_art` | A Graphic Art group with a page at `/crafts/graphic-art`. Tools are coming. |

A craft that is off is left out of the sidebar for everybody, and out of `GET /api/nav/v1` (`docs/nav-api.md`).
Turning a craft on installs nothing. Laser Crafting's tools are a separate app, OpenPrintHQ Laser, which the
OpenPrintHQ installer adds at `/apps/laser` when Laser Crafting is chosen there. On a hub without it, the Laser
Crafting overview says the tools are not installed.

## Choosing

- At first-run setup: the installer passes `crafts` to `POST /api/setup/v1/initialize` (`docs/setup-api.md`).
- Later: Settings, Crafts, or the API below. Changing the crafts needs the hub owner, or the settings permission in
  the installation's first team (which the installer's key has).

## `GET /api/crafts/v1`

Signed in (session cookie or API key).

```json
{
  "crafts": [
    {"id": "3d_printing", "label": "3D Printing", "summary": "...", "always_on": true, "enabled": true},
    {"id": "laser", "label": "Laser Crafting", "summary": "...", "always_on": false, "enabled": true},
    {"id": "sublimation", "label": "Sublimation", "summary": "...", "always_on": false, "enabled": false},
    {"id": "graphic_art", "label": "Graphic Art", "summary": "...", "always_on": false, "enabled": false}
  ],
  "enabled": ["3d_printing", "laser"],
  "can_change": true
}
```

## `PUT /api/crafts/v1`

```json
{"enabled": ["laser"]}
```

`enabled` lists the optional crafts to have on; any not listed are turned off. Naming `3d_printing` is allowed and
changes nothing. The answer is the same as `GET`.

| Status | Meaning |
|---|---|
| 401 | Not signed in |
| 403 | Not the hub owner or an admin of the first team |
| 422 | An unknown craft |
