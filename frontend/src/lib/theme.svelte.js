import { THEMES, DEFAULT_THEME, resolveTheme, applyTheme } from 'openprinthq-ui/themes';
import { api } from './api.js';

// The person's look: a named theme from openprinthq-ui (glass-dark is the only one) and whether to reduce
// transparency. Saved on their hub account so it follows them between devices, and in the browser so the first
// paint (app.html) is already right. Older saved skins resolve to the default theme.
export { THEMES };
// Reduce transparency: follow the device, or always solid panels (on), or always glass (off).
export const REDUCE = { auto: 'Match my device', on: 'On', off: 'Off' };
export const look = $state({ theme: DEFAULT_THEME, reduce: 'auto' });

function store(k, v) { try { localStorage.setItem(k, v); } catch {} }

export function apply(theme = look.theme, reduce = look.reduce) {
  look.theme = resolveTheme(theme);
  look.reduce = REDUCE[reduce] ? reduce : 'auto';
  // applyTheme takes transparency as glass on/off; "reduce transparency on" means glass off.
  applyTheme(document.documentElement, look.theme, look.reduce === 'on' ? 'off' : look.reduce === 'off' ? 'on' : 'auto');
  store('hub.theme', look.theme);
  store('hub.reduce', look.reduce);
}

export function initTheme(prefs) {
  let theme = DEFAULT_THEME, reduce = 'auto';
  try { theme = localStorage.getItem('hub.theme') || theme; reduce = localStorage.getItem('hub.reduce') || reduce; } catch {}
  if (prefs?.theme) theme = prefs.theme;
  if (prefs?.reduce_transparency) reduce = prefs.reduce_transparency;
  apply(theme, reduce);
}

export async function setLook(theme, reduce) {
  apply(theme ?? look.theme, reduce ?? look.reduce);
  try { await api.put('/api/me/prefs', { theme: look.theme, reduce_transparency: look.reduce }); } catch {}
}
