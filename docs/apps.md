# Desktop apps

The OpenPrintHQ installer can run slicers and design apps as desktops in a browser tab: OrcaSlicer, Bambu Studio
and more, each at `/apps/<id>` of the hub's name, behind the hub's sign-in. The installer tells the hub which it
runs, and the hub needs no code of its own for a new app.

## What the hub is told

`PUT /api/apps/v1`, with the installer's key (the hub owner, or the settings permission in the installation's first
team, as for the crafts):

```json
{"apps": [{"id": "bambustudio", "title": "Bambu Studio", "craft": "3d_printing", "path": "/apps/bambustudio",
           "experimental": false}]}
```

Each `id` is lower-case letters and digits, named once; `craft` is one of the crafts (`docs/crafts.md`); `path` is
`/apps/<id>`. A list that does not check out is refused whole (422). The list replaces the one before.
`GET /api/apps/v1` returns the apps, each with `craft_on`, to anyone signed in.

## Where they show

Each app is an item in its craft's sidebar group (`GET /api/nav/v1`, `docs/nav-api.md`), after the group's own
items: a 3D printing app in the Print group, another craft's in that craft's group, so only while the craft is on.
The item opens the hub's page for the app, `/open/<id>`: the hub's sidebar and header stay, and the app's desktop
(`/apps/<id>/`, the item's `frame`) is shown below a bar with its name and Pop out (a window of its own, moved there
until it closes), Open in a new tab, and Full screen (Escape leaves it). Its height can be dragged and is kept for
each app in that browser. `"experimental": true` marks an app the installer still calls experimental. A file the
farm sends to OrcaSlicer opens this page with `?sent=<file name>`, which it names while the desktop opens it.

## Who may open them

The hub's owners always may. Anyone else may when they are in the app's group in the hub's user directory,
`<id>-users`, which the owner turns on or off per person on People (`GET /api/platform/apps`,
`PUT /api/platform/users/{id}/apps/{app}`, each change logged). OrcaSlicer keeps its own switch and endpoints.
