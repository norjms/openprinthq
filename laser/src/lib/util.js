// Environment helpers that work in the browser and in Node (tests).

export function JSZip() {
  const Z = globalThis.JSZip;
  if (!Z) throw new Error('JSZip not loaded');
  return Z;
}

export async function loadZip(bytes) {
  return JSZip().loadAsync(bytes);
}

async function streamThrough(bytes, stream) {
  const out = new Response(new Blob([bytes]).stream().pipeThrough(stream));
  return new Uint8Array(await out.arrayBuffer());
}
// zlib-wrapped deflate (RFC 1950), which the Compression Streams API calls "deflate".
export const zlibInflate = (bytes) => streamThrough(bytes, new DecompressionStream('deflate'));
export const zlibDeflate = (bytes) => streamThrough(bytes, new CompressionStream('deflate'));

export async function sha256Hex(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export const uuid = () => crypto.randomUUID();

const ALNUM = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
export function rid(prefix) {
  const r = crypto.getRandomValues(new Uint8Array(12));
  return prefix + ':' + [...r].map((b) => ALNUM[b % ALNUM.length]).join('');
}

export const utf8 = (bytes) => new TextDecoder('utf-8').decode(bytes);
export const enc = (text) => new TextEncoder().encode(text);

export function b64ToBytes(b64) {
  if (typeof Buffer !== 'undefined') return new Uint8Array(Buffer.from(b64, 'base64'));
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
export function bytesToB64(bytes) {
  if (typeof Buffer !== 'undefined') return Buffer.from(bytes).toString('base64');
  let s = '';
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  return btoa(s);
}
export function dataUrlToBytes(url) {
  const i = url.indexOf(',');
  return b64ToBytes(url.slice(i + 1));
}

export const safeName = (s) => (s || 'untitled').normalize('NFC').replace(/[^\p{L}\p{N}._ -]+/gu, '_').replace(/\s+/g, '_').replace(/_+/g, '_').replace(/^[_.]+|_+$/g, '').slice(0, 80) || 'untitled';

// Round floats the way xTool Studio writes .xs files.
export function r6(o, skip = 'dPath') {
  if (typeof o === 'number') {
    if (Number.isInteger(o)) return o;
    const v = Math.round(o * 1e6) / 1e6;
    return Object.is(v, -0) ? 0 : v;
  }
  if (Array.isArray(o)) return o.map((v) => r6(v, skip));
  if (o && typeof o === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(o)) out[k] = k === skip ? v : r6(v, skip);
    return out;
  }
  return o;
}
