// Named themes. glass-dark is the only one shipped; a light or high-contrast theme is added here and in
// tokens/tokens.json, and every app picks it up through the same data-theme attribute.
export const THEMES = { 'glass-dark': 'Glass dark' };
export const DEFAULT_THEME = 'glass-dark';

/** Any saved value that is not a known theme (an old hub skin such as "legacy_farm") becomes the default. */
export function resolveTheme(saved) {
  return Object.hasOwn(THEMES, saved) ? saved : DEFAULT_THEME;
}

/** transparency: 'auto' follows the device, 'on' keeps glass, 'off' uses solid panels. */
export function applyTheme(el, theme, transparency = 'auto') {
  el.dataset.theme = resolveTheme(theme);
  if (transparency === 'auto') delete el.dataset.transparency;
  else el.dataset.transparency = transparency;
}
