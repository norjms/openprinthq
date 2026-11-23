# OpenPrintHQ Laser

The Laser Crafting tools of [OpenPrintHQ](https://github.com/norjms/openprinthq). First tool: **Convert files**,
which moves laser projects between vendor formats, since designers often publish a model only in their own
laser's format. Files are converted in the browser tab and are never uploaded.

OpenPrintHQ shows Laser Crafting only on hubs that do laser work: the installer asks which crafts the hub is for
and adds this app, at `/apps/laser` under the hub's address, when Laser Crafting is chosen. The page draws the hub's
sidebar from the hub's `GET /api/nav/v1`.

## Formats

| Format | Read | Write | Notes |
|---|---|---|---|
| Bambu Suite `.lac` | yes | beta | R1 55 W and H2D 10 W presets. Writer matches Suite 1.4 structure; not yet opened in Suite. |
| xTool Studio `.xs` (v2) | yes | yes | 2D (P2S) and 3D inner engraving (F2 Ultra UV). Output compared with files Studio 1.10 saved. |
| xTool Studio `.xcs` (v1) | yes | yes | Same as above, single-JSON format. |
| Falcon Design Space `.fds` | crystal (3D) | crystal, beta | 2D Falcon layouts not supported yet (no sample). |
| SVG | yes | yes | Red stroke = cut, blue = score, filled = fill engrave. |
| OBJ / STL | yes | yes | 3D models. |

Operations (cut, score, fill engrave), plates, materials, positions and 3D model size carry over.
Power, speed and kerf do not: outputs start from the target machine's default settings.

## Format notes (reverse engineered)

- **Bambu `.lac`**: zip. `2D/2dmodel.json` holds objects with SVG-like `path_data`, but cubic curves
  are written end point first (`C ex ey c1x c1y c2x c2y`) and arcs use `AC`/`AA ex ey cx cy`
  (clockwise / counterclockwise with the arc centre). Operations per object live in
  `Metadata2D/project_settings.json`; plates are `making_batch_list[].making_plate_list[]`.
- **xTool `.xcs`**: one JSON document. `PATH` objects carry `dPath` plus an affine `transform`.
  Settings per object are duplicated under `device.data` (a serialised `Map`).
- **xTool `.xs`**: zip, `.format` = `v2`. Displays are chunked in `canvases/<id>/displays-N.json`
  without settings; settings are profiles (`profiles.json`) plus per-binding patches in
  `devices/device-<id>.json`. Long `dPath` strings move to `vectors/svg/` keyed by SHA-256;
  3D `modelData` moves to `resources/<sha256 of compact JSON>.json`.
- **Falcon `.fds`**: sequence of `uint32 LE length` + Qt `qCompress` blobs (`uint32 BE size` + zlib)
  holding JSON: device library, 2D project, crystal engraving (OBJ embedded as base64).
- **xTool 3D placement**: `matrix` is column-major, OBJ units to mm, Y up. `x`/`y` are the model's
  centre on the bed (world X/Z), `z` its centre height (world Y); `sizeX/sizeY/sizeZ` are world
  X/Z/Y extents.

## Development

```sh
npm install
npm test                     # round trips that need no vendor files
sh tools/build.sh            # static site in dist/
```

`npm run test:samples` runs the longer regression suite against real projects saved by each vendor's software.
Those files are other people's designs and are not in this repository: point `LC_SAMPLES` at a folder holding
`lantern.lac`, `dougong.lac`, `demo.xcs`, `demo.xs`, `solar.xcs`, `solar.xs` and `angel.fds`.

The templates in `src/templates/` were built from such projects with `tools/extract_templates.py`. They hold the
vendor software's machine presets and default settings, with no model geometry and nothing about the machine or
account that saved them.

## Running it

The image (`ghcr.io/norjms/openprinthq-laser`) is nginx serving the page at `/apps/laser/`, with
`/apps/laser/healthz` for the hub to check that the app is installed. The OpenPrintHQ installer runs it behind
the hub's proxy, which asks the hub's sign-in first. To try it on its own:

```sh
docker build -t openprinthq-laser .
docker run --rm -p 8080:80 openprinthq-laser     # http://localhost:8080/apps/laser/
```

## Adding a format

Add a module in `src/formats/` exporting `id`, `label`, `extensions`, `sniff(bytes, name)`,
`read(bytes, name) -> Doc` and optionally `write(doc, {template}) -> [{name, bytes}]`, then register
it in `src/lib/registry.js`. The shared model is documented in `src/lib/model.js`.

## License

GNU Affero General Public License v3.0 (`LICENSE`). `web/vendor/openprinthq-ui` is MIT, JSZip is MIT or GPLv3,
and the Manrope and Sora fonts are under the SIL Open Font License (copied into the image beside them).
Contributions need the [contributor licence agreement](https://github.com/norjms/openprinthq/blob/main/CLA.md).
