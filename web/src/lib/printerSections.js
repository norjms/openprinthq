// Printer detail page — section catalogue and layout resolution.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The printer page is a stack of independent sections. Each user can reorder
// them and hide the ones they don't care about, either as a default that
// applies to every printer or as an override for one specific printer.
//
// Layouts live in the per-user appearance config under `printerSections`,
// alongside the left-nav prefs (`nav`), so they persist server-side and follow
// the user between browsers. Nothing here touches the printer record itself.
//
//   printerSections = {
//     order:  ['job', 'temps', ...],        // per-user default
//     hidden: ['gcode'],
//     byPrinter: { '7': { order: [...], hidden: [...] } }   // per-printer override
//   }
//
// An absent / empty layout means "built-in defaults", so a user who has never
// customised anything renders exactly what they rendered before this existed.

// Every printer type gets the same shell. A section that a given machine can't
// do simply isn't offered — the availability set the page passes in decides
// that — so `variant` is 'both' throughout and kept only so a future
// vendor-specific section has somewhere to declare itself.
//
// `scope` is likewise vestigial at one value ('page'); it earned its keep while
// the Bambu dashboard had its own internal ordering, and costs nothing to leave
// in place for the next nested layout.
//
// `lockHide` marks a section that can be reordered but never hidden, because
// it carries identity or safety-critical controls (the emergency stop, printer
// alerts). `width: 'half'` marks a card that pairs up two-per-row when its
// neighbour is also a half.

export const PRINTER_SECTIONS = [
  {
    key: 'header', scope: 'page', variant: 'both', lockHide: true,
    label: 'Printer header',
    hint: 'Name, model, connection state, settings and the emergency stop.'
  },
  {
    key: 'alerts', scope: 'page', variant: 'both', lockHide: true,
    label: 'Printer alerts',
    hint: 'Faults reported by the printer. Only appears when something is wrong.'
  },
  {
    key: 'camera', scope: 'page', variant: 'both',
    label: 'Camera',
    hint: 'Live view, still capture, and the timelapse and feed switches.'
  },
  {
    key: 'progress', scope: 'page', variant: 'both',
    label: 'Printing progress',
    hint: 'Plate preview, layer and ETA, pause / stop and skipping objects.'
  },
  {
    key: 'control', scope: 'page', variant: 'both',
    label: 'Control',
    hint: 'Temperatures, fans, lamp, the move wheel, bed steps and the extruder.'
  },
  {
    key: 'filament', scope: 'page', variant: 'both',
    label: 'Filament & hotends',
    hint: 'AMS slots, loading, drying and the nozzle hardware fitted.'
  },
  {
    key: 'console', scope: 'page', variant: 'both',
    label: 'Klipper console',
    hint: 'Live gcode_store output with a command line.'
  },
  {
    key: 'power', scope: 'page', variant: 'both',
    label: 'Power',
    hint: 'Smart plugs wired to this printer.'
  },
  {
    key: 'maintenance', scope: 'page', variant: 'both',
    label: 'Maintenance',
    hint: 'Service reminders and usage counters.'
  },
  {
    key: 'klipper-tuning', scope: 'page', variant: 'both',
    label: 'Klipper tuning',
    hint: 'Homing, mesh, PID, pressure advance, input shaping and live factors.'
  },
  {
    key: 'eject', scope: 'page', variant: 'both',
    label: 'Bed ejection',
    hint: 'Automatic bed clearing and continuous printing.'
  },
  {
    key: 'gcode', scope: 'page', variant: 'both',
    label: 'G-code console',
    hint: 'Send raw G-code to the printer.'
  }
];

const BY_KEY = new Map(PRINTER_SECTIONS.map((s) => [s.key, s]));

export function sectionDef(key) {
  return BY_KEY.get(key) || null;
}

const uniq = (arr) => {
  const seen = new Set();
  return arr.filter((k) => (seen.has(k) ? false : (seen.add(k), true)));
};

// A stored layout is only ever { order, hidden } of known section keys.
// Unknown keys (an older/newer client, a hand-edited config) are dropped rather
// than trusted, so a stale entry can never hide a section that no longer maps
// to anything.
export function normalizeLayout(raw) {
  const o = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const keys = (a) => (Array.isArray(a) ? uniq(a.filter((x) => typeof x === 'string' && BY_KEY.has(x))) : []);
  return { order: keys(o.order), hidden: keys(o.hidden) };
}

const isPlainObject = (v) => !!v && typeof v === 'object' && !Array.isArray(v);
const isLayoutSet = (l) => l.order.length > 0 || l.hidden.length > 0;

// Normalize the whole `printerSections` namespace: a default layout plus any
// per-printer overrides. Empty overrides are dropped so they fall through to
// the default instead of pinning an accidental blank layout.
export function normalizePrinterSections(raw) {
  const p = isPlainObject(raw) ? raw : {};
  const byPrinter = {};
  if (isPlainObject(p.byPrinter)) {
    for (const [id, value] of Object.entries(p.byPrinter)) {
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) continue;
      const layout = normalizeLayout(value);
      if (isLayoutSet(layout)) byPrinter[id] = layout;
    }
  }
  return { ...normalizeLayout(p), byPrinter };
}

export const EMPTY_LAYOUT = { order: [], hidden: [] };

// Which layout actually applies to this printer, and where it came from.
// A per-printer override wins outright — it is a complete layout, not a patch
// on top of the default, so switching the default later never silently
// reshuffles a printer the user has already arranged by hand.
export function resolveLayout(printerSections, printerId) {
  const ps = normalizePrinterSections(printerSections);
  const own = ps.byPrinter[String(printerId)];
  if (own) return { layout: own, scope: 'printer' };
  return { layout: { order: ps.order, hidden: ps.hidden }, scope: 'global' };
}

export function hasOverride(printerSections, printerId) {
  return !!normalizePrinterSections(printerSections).byPrinter[String(printerId)];
}

// Resolve a layout into the ordered list of sections to render.
//
// `available` is the set of section keys that make sense for this printer right
// now (no camera → no camera section). Sections the user hid are still returned,
// flagged `hidden`, because edit mode has to offer a way to bring them back.
// Saved keys come first in their saved order; anything the user has never seen
// (a section added by a later release) falls in at its catalogue position.
export function orderedSections(layout, { variant, scope, available }) {
  const l = normalizeLayout(layout);
  const avail = available instanceof Set ? available : new Set(available || []);
  const applies = (s) =>
    s.scope === scope &&
    (s.variant === 'both' || s.variant === variant) &&
    avail.has(s.key);

  const catalogue = PRINTER_SECTIONS.filter(applies);
  const placed = new Set();
  const out = [];

  for (const key of l.order) {
    const def = BY_KEY.get(key);
    if (def && applies(def) && !placed.has(key)) { out.push(def); placed.add(key); }
  }
  // Sections the user has never arranged — a new one shipped in a later release,
  // or one that only just became relevant to this printer — slot in directly
  // after whatever precedes them in the catalogue, so they land beside their
  // natural neighbour instead of always at the bottom. Walking the catalogue
  // forwards means a run of several new sections keeps its own order too.
  for (let i = 0; i < catalogue.length; i++) {
    const def = catalogue[i];
    if (placed.has(def.key)) continue;
    let at = 0;
    for (let j = i - 1; j >= 0; j--) {
      const idx = out.findIndex((s) => s.key === catalogue[j].key);
      if (idx >= 0) { at = idx + 1; break; }
    }
    out.splice(at, 0, def);
    placed.add(def.key);
  }

  const hidden = new Set(l.hidden);
  return out.map((def) => ({
    key: def.key,
    def,
    hidden: !def.lockHide && hidden.has(def.key)
  }));
}

// Build the layout to persist from an edited draft. The order is written in
// full (every section the user could see), which is what makes a per-printer
// override self-contained.
export function layoutFromDraft(draft) {
  return {
    order: draft.map((s) => s.key),
    hidden: draft.filter((s) => s.hidden && !s.def?.lockHide).map((s) => s.key)
  };
}

// Merge an edited page-scope + dashboard-scope draft back into one layout,
// preserving any keys that weren't on screen (e.g. the Bambu blocks while
// editing a Klipper printer) so editing one printer can't wipe another's.
export function mergeLayout(previous, ...layouts) {
  const prev = normalizeLayout(previous);
  const edited = layouts.map(normalizeLayout);
  const editedKeys = new Set(edited.flatMap((l) => l.order));

  const order = [...edited.flatMap((l) => l.order)];
  for (const key of prev.order) if (!editedKeys.has(key)) order.push(key);

  const hidden = new Set(edited.flatMap((l) => l.hidden));
  for (const key of prev.hidden) if (!editedKeys.has(key)) hidden.add(key);

  return normalizeLayout({ order, hidden: [...hidden] });
}
