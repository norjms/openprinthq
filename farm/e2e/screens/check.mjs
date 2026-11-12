// The layout rule every screen is held to, run inside the page:
//  - the page itself never scrolls, in either direction;
//  - nothing is hidden: no visible element sits outside the window, and no
//    element that clips its content (overflow hidden or clip) is cutting any
//    off. Scrolling panels are fine; they show a scrollbar.
// Text that is shortened with an ellipsis on purpose, and media that is cropped
// to fill its frame (camera views, thumbnails), are not counted as hidden.
// SPDX-License-Identifier: AGPL-3.0-or-later

export function layoutProblems() {
  const out = [];
  const se = document.scrollingElement || document.documentElement;
  const W = window.innerWidth, H = window.innerHeight;
  if (se.scrollHeight > H + 1) out.push(`page scrolls vertically (${se.scrollHeight} > ${H})`);
  if (se.scrollWidth > W + 1) out.push(`page scrolls horizontally (${se.scrollWidth} > ${W})`);

  const name = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    const c = typeof el.className === 'string' ? el.className.split(/\s+/).filter((x) => x && !x.startsWith('svelte-')).slice(0, 3) : [];
    if (c.length) s += '.' + c.join('.');
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    return t ? `${s} "${t}"` : s;
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const media = new Set(['IMG', 'VIDEO', 'CANVAS', 'SVG', 'IFRAME', 'PICTURE']);
  const scrolls = (cs) => /(auto|scroll)/.test(cs.overflowX + cs.overflowY);
  const clipsX = (cs) => /(hidden|clip)/.test(cs.overflowX);
  const clipsY = (cs) => /(hidden|clip)/.test(cs.overflowY);

  // 1. Elements outside the window that no scrolling panel can bring into view.
  for (const el of document.body.querySelectorAll('*')) {
    if (!visible(el) || el.closest('[data-offscreen-ok], .sr-only')) continue;
    const r = el.getBoundingClientRect();
    const outside = r.right > W + 1 || r.bottom > H + 1 || r.left < -1 || r.top < -1;
    if (!outside) continue;
    let p = el.parentElement, rescued = false;
    while (p && p !== document.body) {
      const cs = getComputedStyle(p);
      if (scrolls(cs) || clipsX(cs) || clipsY(cs)) { rescued = true; break; }
      if (cs.position === 'fixed') break;
      p = p.parentElement;
    }
    if (!rescued) out.push(`outside the window: ${name(el)} at ${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)}`);
  }

  // 2. Containers that cut their content off.
  for (const el of document.body.querySelectorAll('*')) {
    if (!visible(el) || media.has(el.tagName.toUpperCase()) || el.closest('[data-clip-ok]')) continue;
    const cs = getComputedStyle(el);
    if (cs.textOverflow === 'ellipsis' || el.closest('svg')) continue;
    const cutX = clipsX(cs) && el.scrollWidth > el.clientWidth + 2;
    const cutY = clipsY(cs) && el.scrollHeight > el.clientHeight + 2;
    if (!cutX && !cutY) continue;
    // Only a cut that hides something real counts: a cropped image or a
    // rounded frame around media is how those are drawn.
    const kids = [...el.querySelectorAll('*')].filter((k) => visible(k) && !media.has(k.tagName.toUpperCase()) && !k.closest('svg'));
    const er = el.getBoundingClientRect();
    const hidden = kids.find((k) => {
      const kr = k.getBoundingClientRect();
      const kcs = getComputedStyle(k);
      if (kcs.position === 'absolute' && k.getAttribute('aria-hidden') === 'true') return false;
      // Inside a nearer panel that scrolls or clips: that panel answers for it.
      for (let a = k.parentElement; a && a !== el; a = a.parentElement) {
        const acs = getComputedStyle(a);
        if (scrolls(acs) || clipsX(acs) || clipsY(acs)) return false;
      }
      return (cutX && (kr.right > er.right + 2 || kr.left < er.left - 2)) || (cutY && (kr.bottom > er.bottom + 2 || kr.top < er.top - 2));
    });
    if (hidden) out.push(`cut off inside ${name(el)}: ${name(hidden)}`);
  }
  return [...new Set(out)].slice(0, 25);
}
