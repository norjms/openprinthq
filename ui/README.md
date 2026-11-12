# artisanhub-ui

The shared look of every ArtisanHub app: the hub, ArtisanHub Farm, OpenPrintHQ, the sign-in, the installer and the
companion apps. The look is "glass over a live feed": translucent cards over a dark blue page.

One source, `tokens/tokens.json`, generates:

| File | For |
|---|---|
| `dist/theme.css` | CSS custom properties (`--ah-*`) for every web app |
| `dist/tailwind.css` | a Tailwind 4 `@theme` mapping those properties |
| `dist/artisanhub_ui.dart` | the Flutter phone app |
| `dist/artisanhub_ui.h` | the OrcaSlicer recolour |
| `dist/tokens.json` | anything else |

Plus `src/lib/base.css` (shared classes), `src/lib/fonts.css` (Manrope, Sora and JetBrains Mono, all SIL Open Font
License) and Svelte 5 components: AppShell, Sidebar, GlassCard, WidgetCard, Button, Chip, Segmented, Toggle, Field,
GridTable, ProgressBar, Banner and Icon.

## Use it

Pin a tag in `package.json`:

```json
"artisanhub-ui": "github:norjms/artisanhub-ui#v0.1.0"
```

Then, once in the app's root layout:

```js
import 'artisanhub-ui/fonts.css'; // leave out where fonts must not load, such as the offline installer
import 'artisanhub-ui/theme.css';
import 'artisanhub-ui/base.css';
import { applyTheme } from 'artisanhub-ui/themes';
applyTheme(document.documentElement, savedTheme, savedTransparency);
```

The outer element gets the class `ah-root`; `AppShell` sets it for desktop pages.

## Themes

`glass-dark` is the only theme. Themes are named so that a light or high-contrast theme can be added in
`tokens/tokens.json` and `src/lib/themes.js` without changing apps. `resolveTheme` maps any older saved value, such
as the hub's former skins, to the default. Transparency follows the device's "reduce transparency" setting unless
`data-transparency` is `on` or `off`; off swaps glass for solid panels.

## Rules the values keep

- Text on glass passes 4.5:1 over every stop of the page gradient; muted text under 18px is 0.85 for that reason.
- Chips, badges and banners sit on a dark underlay so their text passes over the lightest page stop and camera feeds.
- Accent `#8fd3ff` is live data, primary actions and selection; `#ffb38a` needs attention; `#e66767` is failure only.
- No em dashes in copy. Icons are stroke SVG, never emoji.

## Develop

```sh
npm install
npm test               # builds, checks the golden files, contrast, themes, copy rules
npm run check          # svelte-check
npm run gallery        # builds the gallery
node scripts/gallery-check.mjs   # renders it at 1920 x 1080 and 1280 x 860; fails if the page scrolls
```

A change to `tokens/tokens.json` changes `dist/`: rerun the build, review, and copy `dist/*` to `test/golden/`.

## License

MIT. The bundled fonts keep their own SIL Open Font License.
