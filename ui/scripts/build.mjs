// Generates every output from tokens/tokens.json. Deterministic: the same input always gives the same bytes.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';

const root = new URL('..', import.meta.url).pathname;
const t = JSON.parse(readFileSync(root + 'tokens/tokens.json', 'utf8'));
const out = root + 'dist/';
mkdirSync(out, { recursive: true });

const THEME = 'glass-dark';
const colors = t.color.tokens.map((x) => [x.name, typeof x.value === 'string' ? x.value : x.value[THEME]]);
const spacing = t.spacing.tokens.map((x) => [x.name, x.value]);
const radius = t.radius.tokens.map((x) => [x.name, x.value]);
const fam = t.type.families;
const head = (c) => `${c} Generated from tokens/tokens.json by scripts/build.mjs. Do not edit.\n`;

// CSS custom properties, one block per theme, plus the reduced transparency fallback.
const vars = [
  ...colors.map(([n, v]) => `  --ah-${n}: ${v};`),
  ...spacing.map(([n, v]) => `  --ah-${n}: ${v};`),
  ...radius.map(([n, v]) => `  --ah-${n}: ${v};`),
  `  --ah-font-sans: ${fam.sans};`,
  `  --ah-font-display: ${fam.display};`,
  `  --ah-font-mono: ${fam.mono};`,
  '  --ah-page-gradient: radial-gradient(80% 75% at 55% 35%, var(--ah-page-top) 0%, var(--ah-page-mid) 50%, var(--ah-page) 100%);',
  '  --ah-blur: blur(24px);',
  '  --ah-card-pad: clamp(12px, 1.6vh, 20px);',
  '  --ah-focus: 0 0 0 2px var(--ah-page), 0 0 0 4px var(--ah-accent);',
  '  color-scheme: dark;',
].join('\n');
const css = `${head('/*')}*/
:root,
[data-theme="${THEME}"] {
${vars}
}

@media (prefers-reduced-transparency: reduce) {
  :root:not([data-transparency="on"]) { --ah-glass: var(--ah-solid-panel); --ah-blur: none; }
}
[data-transparency="off"] { --ah-glass: var(--ah-solid-panel); --ah-blur: none; }
`;
writeFileSync(out + 'theme.css', css);

// Tailwind 4 theme: maps the custom properties into Tailwind's namespaces.
const tw = `${head('/*')}*/
@theme inline {
${colors.map(([n]) => `  --color-ah-${n}: var(--ah-${n});`).join('\n')}
${spacing.map(([n]) => `  --spacing-ah-${n.replace('space-', '')}: var(--ah-${n});`).join('\n')}
${radius.map(([n]) => `  --radius-ah-${n.replace('radius-', '')}: var(--ah-${n});`).join('\n')}
  --font-ah-sans: var(--ah-font-sans);
  --font-ah-display: var(--ah-font-display);
  --font-ah-mono: var(--ah-font-mono);
}
`;
writeFileSync(out + 'tailwind.css', tw);

// Dart, for the Flutter phone app.
function argb(v) {
  let m = v.match(/^#([0-9a-f]{6})$/i);
  if (m) return '0xFF' + m[1].toUpperCase();
  m = v.match(/^rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/);
  if (!m) throw new Error('unsupported colour ' + v);
  const h = (n) => Number(n).toString(16).padStart(2, '0').toUpperCase();
  return '0x' + h(Math.round(Number(m[4]) * 255)) + h(m[1]) + h(m[2]) + h(m[3]);
}
const camel = (s) => s.replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase());
const dart = `${head('//')}// ignore_for_file: constant_identifier_names
import 'dart:ui';

class AhColors {
  AhColors._();
${colors.map(([n, v]) => `  static const Color ${camel(n)} = Color(${argb(v)});`).join('\n')}
}

class AhRadius {
  AhRadius._();
${radius.map(([n, v]) => `  static const double ${camel(n.replace('radius-', ''))} = ${parseFloat(v)};`).join('\n')}
}

class AhSpace {
  AhSpace._();
${spacing.map(([n, v]) => `  static const double s${n.replace('space-', '')} = ${parseFloat(v)};`).join('\n')}
}

class AhFonts {
  AhFonts._();
  static const String sans = 'Manrope';
  static const String display = 'Sora';
  static const String mono = 'JetBrains Mono';
}
`;
writeFileSync(out + 'artisanhub_ui.dart', dart);

// C++ header, for recolouring OrcaSlicer. Colours as 0xAARRGGBB.
const cpp = `${head('//')}#pragma once
#include <cstdint>

namespace artisanhub_ui {
${colors.map(([n, v]) => `constexpr std::uint32_t ${n.replace(/-/g, '_')} = ${argb(v)}u;`).join('\n')}
constexpr const char* font_sans = "Manrope";
constexpr const char* font_mono = "JetBrains Mono";
}  // namespace artisanhub_ui
`;
writeFileSync(out + 'artisanhub_ui.h', cpp);

writeFileSync(out + 'tokens.json', JSON.stringify(Object.fromEntries(colors), null, 1) + '\n');
console.log(`built ${colors.length} colours, ${spacing.length} spacing, ${radius.length} radii`);
