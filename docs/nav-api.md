# Sidebar API

`GET /api/nav/v1` returns the hub's sidebar as data, so that the farm, and any other app that shares the
sidebar, draws the same one. The list itself is `frontend/static/nav.json`, the file the hub's own sidebar is built
from; the built hub also serves that file at `/nav.json`.

Anyone may call it. Signed in (session cookie or API key), the answer also carries the installation's name and the
installed farm's own links.

```json
{
  "version": 1,
  "product": "OpenPrintHQ",
  "installation": "My workshop",
  "farm": { "kind": "openprinthq", "label": "Farm", "app": "https://hub.example/apps/farm" },
  "groups": [
    {
      "label": "Print",
      "items": [
        { "id": "farm", "label": "Farm overview", "href": "/farm", "icon": "grid", "badge": "plate_checks", "app": "hub" },
        { "id": "printers", "label": "Printers", "href": "https://hub.example/apps/farm/app/printers", "icon": "printer",
          "farm_path": "/app/printers", "app": "farm" }
      ]
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `groups[].items[].href` | Where the item goes. A path on the hub, or for `app: "farm"` the farm's own page. |
| `app` | Which app serves the page: `hub`, `farm`, or `laser` (OpenPrintHQ Laser at `/apps/laser`). |
| `frame` | For a desktop app's item: the app's own address, which the hub's page at `href` (`/open/<id>`) shows. Another app drawing the sidebar links to `href` like any hub page. |
| `farm_path` | The item's page inside the farm (Printers, Cameras, Print queue, Filament, and the Farm group). The hub links there when the installation's farm is OpenPrintHQ with a public address, as an installed farm at `/apps/farm` has; otherwise to its own page at `href`. |
| `icon` | A stroke icon name from `openprinthq-ui`. |
| `badge` | The name of a count in `GET /api/stats` shown next to the item when it is not zero. |
| `perm` | The permission the item needs; leave the item out for people without it. |
| `match` | Older paths that also count as this item's page. |
| `installation` | The installation's name, or `null` when the caller is not signed in. |
| `groups[].craft` | The optional craft the group belongs to (`docs/crafts.md`). Groups without it are always there. |
| `farm` | The installation's farm: `kind` (`openprinthq`, or `null` before first-run setup), a display `label`, and `app`, the farm's own address or `null`. |

The **Farm** group, after Print, holds the pages only the farm has: Files, Slicer, Projects, Statistics, Print
reports, Timelapses and Farm settings. In `nav.json` its items carry `"app": "farm"` and a `farm_path` but no `href`.
The answer has the group only under the same rule as the Print group's farm links: signed in, with the installation's
farm OpenPrintHQ with a public address (an installed farm). Otherwise the group, and any group left
empty, is left out.

Groups of an optional craft (Laser Crafting, Sublimation, Graphic Art) come after Farm and are in the answer only
while the installation has that craft on, for everybody, signed in or not. Laser Crafting's Convert files item has
`"app": "laser"` and `href` `/apps/laser/`, the OpenPrintHQ Laser app the installer adds with Laser Crafting.
Each desktop app the installer runs (`docs/apps.md`) is an item in its craft's group, after the group's own items,
with `href` `/open/<id>` (the hub's page showing it, `"app": "hub"`), `frame` `/apps/<id>/`, the icon `tv`, and
`"experimental": true` when the installer says so.

The groups and items only change with a new `version` when a field changes meaning; new items may appear at any time.
