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
| `app` | `hub` or `farm`: which app serves the page. |
| `farm_path` | The item's page inside the farm (Printers, Cameras, Print queue, Filament, and the Farm group). The hub links there when the installation's farm is OpenPrintHQ with a public address, as an installed farm at `/apps/farm` has; otherwise to its own page at `href`. |
| `icon` | A stroke icon name from `openprinthq-ui`. |
| `badge` | The name of a count in `GET /api/stats` shown next to the item when it is not zero. |
| `perm` | The permission the item needs; leave the item out for people without it. |
| `match` | Older paths that also count as this item's page. |
| `installation` | The installation's name, or `null` when the caller is not signed in. |
| `farm` | The installation's farm: `kind` (`openprinthq`, or `null` before first-run setup), a display `label`, and `app`, the farm's own address or `null`. |

The **Farm** group, after Print, holds the pages only the farm has: Files, Slicer, Projects, Statistics, Print
reports, Timelapses and Farm settings. In `nav.json` its items carry `"app": "farm"` and a `farm_path` but no `href`.
The answer has the group only under the same rule as the Print group's farm links: signed in, with the installation's
farm OpenPrintHQ with a public address (an installed farm). Otherwise the group, and any group left
empty, is left out.

The groups and items only change with a new `version` when a field changes meaning; new items may appear at any time.
