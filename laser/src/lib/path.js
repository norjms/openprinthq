// Path geometry core. A path is an array of subpaths; each subpath is
// { pts: [[x,y], ...], segs: [...], closed } where segs is a list of
//   { t: 'L', p: [x,y] } or { t: 'C', c1, c2, p }
// starting from start point s. Everything is absolute, in millimetres.


export function parseSvgPath(d) {
  const tokens = [];
  const re = /([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)/g;
  let m;
  while ((m = re.exec(d))) tokens.push(m[1] ? m[1] : parseFloat(m[2]));
  const subs = [];
  let cur = null, x = 0, y = 0, sx = 0, sy = 0, cmd = null, i = 0;
  let lastC2 = null, lastQ = null;
  const num = () => {
    const v = tokens[i++];
    if (typeof v !== 'number') throw new Error('bad path data near token ' + i);
    return v;
  };
  const start = (px, py) => {
    cur = { s: [px, py], segs: [], closed: false };
    subs.push(cur);
  };
  const ensure = () => { if (!cur) start(x, y); };
  while (i < tokens.length) {
    if (typeof tokens[i] === 'string') cmd = tokens[i++];
    else if (cmd === null) throw new Error('path data must start with a command');
    const rel = cmd === cmd.toLowerCase();
    const C = cmd.toUpperCase();
    let prevC2 = lastC2, prevQ = lastQ;
    lastC2 = null; lastQ = null;
    switch (C) {
      case 'M': {
        let nx = num(), ny = num();
        if (rel) { nx += x; ny += y; }
        x = sx = nx; y = sy = ny; start(x, y);
        cmd = rel ? 'l' : 'L'; // implicit lineto
        break;
      }
      case 'L': case 'H': case 'V': {
        ensure();
        let nx = x, ny = y;
        if (C === 'L') { nx = num(); ny = num(); if (rel) { nx += x; ny += y; } }
        else if (C === 'H') { nx = num(); if (rel) nx += x; }
        else { ny = num(); if (rel) ny += y; }
        cur.segs.push({ t: 'L', p: [nx, ny] }); x = nx; y = ny;
        break;
      }
      case 'C': case 'S': {
        ensure();
        let c1;
        if (C === 'C') { c1 = [num(), num()]; if (rel) { c1[0] += x; c1[1] += y; } }
        else c1 = prevC2 ? [2 * x - prevC2[0], 2 * y - prevC2[1]] : [x, y];
        const c2 = [num(), num()], p = [num(), num()];
        if (rel) { c2[0] += x; c2[1] += y; p[0] += x; p[1] += y; }
        cur.segs.push({ t: 'C', c1, c2, p }); x = p[0]; y = p[1]; lastC2 = c2;
        break;
      }
      case 'Q': case 'T': {
        ensure();
        let q;
        if (C === 'Q') { q = [num(), num()]; if (rel) { q[0] += x; q[1] += y; } }
        else q = prevQ ? [2 * x - prevQ[0], 2 * y - prevQ[1]] : [x, y];
        const p = [num(), num()];
        if (rel) { p[0] += x; p[1] += y; }
        cur.segs.push(quadToCubic([x, y], q, p)); x = p[0]; y = p[1]; lastQ = q;
        break;
      }
      case 'A': {
        ensure();
        const rx = num(), ry = num(), rot = num(), large = num(), sweep = num();
        let px = num(), py = num();
        if (rel) { px += x; py += y; }
        for (const s of arcToCubics(x, y, rx, ry, rot, large, sweep, px, py)) cur.segs.push(s);
        x = px; y = py;
        break;
      }
      case 'Z': {
        if (cur) { cur.closed = true; x = sx; y = sy; cur = null; }
        break;
      }
      default: throw new Error('unsupported path command ' + cmd);
    }
  }
  return subs;
}

export function quadToCubic(s, q, p) {
  return {
    t: 'C',
    c1: [s[0] + (2 / 3) * (q[0] - s[0]), s[1] + (2 / 3) * (q[1] - s[1])],
    c2: [p[0] + (2 / 3) * (q[0] - p[0]), p[1] + (2 / 3) * (q[1] - p[1])],
    p: [p[0], p[1]],
  };
}

// SVG elliptical arc -> cubic Béziers (each <= 90°).
export function arcToCubics(x1, y1, rx, ry, phiDeg, fa, fs, x2, y2) {
  if (x1 === x2 && y1 === y2) return [];
  rx = Math.abs(rx); ry = Math.abs(ry);
  if (rx === 0 || ry === 0) return [{ t: 'L', p: [x2, y2] }];
  const phi = (phiDeg * Math.PI) / 180, cos = Math.cos(phi), sin = Math.sin(phi);
  const dx = (x1 - x2) / 2, dy = (y1 - y2) / 2;
  const x1p = cos * dx + sin * dy, y1p = -sin * dx + cos * dy;
  const lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry);
  if (lam > 1) { const s = Math.sqrt(lam); rx *= s; ry *= s; }
  const num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p;
  const den = rx * rx * y1p * y1p + ry * ry * x1p * x1p;
  let co = Math.sqrt(Math.max(0, num / den));
  if (!!fa === !!fs) co = -co;
  const cxp = (co * rx * y1p) / ry, cyp = (-co * ry * x1p) / rx;
  const cx = cos * cxp - sin * cyp + (x1 + x2) / 2, cy = sin * cxp + cos * cyp + (y1 + y2) / 2;
  const ang = (ux, uy, vx, vy) => {
    const a = Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy);
    return a;
  };
  const t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry);
  let dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry);
  if (!fs && dt > 0) dt -= 2 * Math.PI;
  else if (fs && dt < 0) dt += 2 * Math.PI;
  const n = Math.max(1, Math.ceil(Math.abs(dt) / (Math.PI / 2) - 1e-9));
  const step = dt / n, k = (4 / 3) * Math.tan(step / 4);
  const pt = (t) => [cx + rx * Math.cos(t) * cos - ry * Math.sin(t) * sin, cy + rx * Math.cos(t) * sin + ry * Math.sin(t) * cos];
  const der = (t) => [-rx * Math.sin(t) * cos - ry * Math.cos(t) * sin, -rx * Math.sin(t) * sin + ry * Math.cos(t) * cos];
  const out = [];
  for (let j = 0; j < n; j++) {
    const a = t1 + j * step, b = a + step;
    const pa = pt(a), pb = j === n - 1 ? [x2, y2] : pt(b), da = der(a), db = der(b);
    out.push({ t: 'C', c1: [pa[0] + k * da[0], pa[1] + k * da[1]], c2: [pb[0] - k * db[0], pb[1] - k * db[1]], p: pb });
  }
  return out;
}

// Arc given by end point and centre (Bambu Suite AC / AA): cw = clockwise on screen (y down).
export function centerArcToCubics(x1, y1, x2, y2, cx, cy, cw) {
  const r = (Math.hypot(x1 - cx, y1 - cy) + Math.hypot(x2 - cx, y2 - cy)) / 2;
  const cross = (x1 - cx) * (y2 - cy) - (y1 - cy) * (x2 - cx);
  const sweep = cw ? 1 : 0;
  const large = (cross >= 0) === (sweep === 1) ? 0 : 1;
  return arcToCubics(x1, y1, r, r, 0, large, sweep, x2, y2);
}

// Affine [a,b,c,d,e,f] : x' = a x + c y + e ; y' = b x + d y + f  (SVG convention)
export const IDENTITY = [1, 0, 0, 1, 0, 0];
export function mul(m, n) { // m ∘ n : apply n first, then m
  return [
    m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1],
    m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
    m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5],
  ];
}
export const apply = (m, p) => [m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5]];

export function transformPath(subs, m) {
  return subs.map((s) => ({
    s: apply(m, s.s), closed: s.closed,
    segs: s.segs.map((g) => (g.t === 'L' ? { t: 'L', p: apply(m, g.p) } : { t: 'C', c1: apply(m, g.c1), c2: apply(m, g.c2), p: apply(m, g.p) })),
  }));
}

export function bbox(subs) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const add = (p) => { if (p[0] < x0) x0 = p[0]; if (p[0] > x1) x1 = p[0]; if (p[1] < y0) y0 = p[1]; if (p[1] > y1) y1 = p[1]; };
  for (const s of subs) {
    add(s.s);
    let prev = s.s;
    for (const g of s.segs) {
      if (g.t === 'L') add(g.p);
      else { for (const e of cubicExtrema(prev, g.c1, g.c2, g.p)) add(e); add(g.p); }
      prev = g.p;
    }
  }
  return [x0, y0, x1, y1];
}

function cubicExtrema(p0, p1, p2, p3) {
  const out = [];
  for (let k = 0; k < 2; k++) {
    const a = -p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k];
    const b = 2 * (p0[k] - 2 * p1[k] + p2[k]);
    const c = p1[k] - p0[k];
    const ts = [];
    if (Math.abs(a) < 1e-12) { if (Math.abs(b) > 1e-12) ts.push(-c / b); }
    else {
      const disc = b * b - 4 * a * c;
      if (disc >= 0) { const sq = Math.sqrt(disc); ts.push((-b + sq) / (2 * a), (-b - sq) / (2 * a)); }
    }
    for (const t of ts) if (t > 0 && t < 1) {
      const mt = 1 - t;
      out.push([0, 1].map((j) => mt * mt * mt * p0[j] + 3 * mt * mt * t * p1[j] + 3 * mt * t * t * p2[j] + t * t * t * p3[j]));
    }
  }
  return out;
}

export function fmt(v, dp = 4) {
  if (!isFinite(v)) throw new Error('non-finite coordinate');
  let s = v.toFixed(dp);
  if (s.indexOf('.') >= 0) s = s.replace(/0+$/, '').replace(/\.$/, '');
  return s === '-0' ? '0' : s;
}

// Serialise to absolute M/L/C/Z. style: 'svg' -> "M1,2 L3,4" ; 'space' -> "M 1 2 L 3 4"
export function toD(subs, style = 'svg', dp = 4) {
  const P = style === 'svg' ? (p) => fmt(p[0], dp) + ',' + fmt(p[1], dp) : (p) => fmt(p[0], dp) + ' ' + fmt(p[1], dp);
  const sep = style === 'svg' ? '' : ' ';
  const out = [];
  for (const s of subs) {
    out.push('M' + sep + P(s.s));
    for (const g of s.segs) out.push(g.t === 'L' ? 'L' + sep + P(g.p) : 'C' + sep + P(g.c1) + ' ' + P(g.c2) + ' ' + P(g.p));
    if (s.closed) out.push('Z');
  }
  return out.join(' ');
}

export function pathFromRect(x, y, w, h, rx = 0, ry = rx) {
  rx = Math.min(Math.abs(rx), w / 2); ry = Math.min(Math.abs(ry), h / 2);
  if (!rx || !ry) return [{ s: [x, y], closed: true, segs: [{ t: 'L', p: [x + w, y] }, { t: 'L', p: [x + w, y + h] }, { t: 'L', p: [x, y + h] }] }];
  const d = `M${x + rx},${y} H${x + w - rx} A${rx},${ry} 0 0 1 ${x + w},${y + ry} V${y + h - ry} A${rx},${ry} 0 0 1 ${x + w - rx},${y + h} H${x + rx} A${rx},${ry} 0 0 1 ${x},${y + h - ry} V${y + ry} A${rx},${ry} 0 0 1 ${x + rx},${y} Z`;
  return parseSvgPath(d);
}

export function pathFromEllipse(cx, cy, rx, ry) {
  return parseSvgPath(`M${cx - rx},${cy} A${rx},${ry} 0 0 1 ${cx + rx},${cy} A${rx},${ry} 0 0 1 ${cx - rx},${cy} Z`);
}

export function isClosed(subs) { return subs.length > 0 && subs.every((s) => s.closed); }
