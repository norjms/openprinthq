// Renders the gallery at the two desktop sizes and checks that the page never scrolls and nothing spills sideways.
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { extname, join } from 'node:path';

const dir = new URL('../gallery/dist/', import.meta.url).pathname;
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff' };
const server = createServer((req, res) => {
  const p = join(dir, decodeURIComponent(req.url.split('?')[0]).replace(/\/$/, '/index.html'));
  if (!p.startsWith(dir) || !existsSync(p)) { res.writeHead(404).end(); return; }
  res.writeHead(200, { 'content-type': types[extname(p)] ?? 'application/octet-stream' }).end(readFileSync(p));
}).listen(0);
const url = `http://127.0.0.1:${server.address().port}/`;
const browser = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
mkdirSync('test-results', { recursive: true });
let failed = false;
for (const [w, h] of [[1920, 1080], [1280, 860]]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.screenshot({ path: `test-results/gallery-${w}x${h}.png` });
  const r = await page.evaluate(() => ({ sh: document.documentElement.scrollHeight, sw: document.documentElement.scrollWidth, ih: innerHeight, iw: innerWidth, fonts: [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family) }));
  const ok = r.sh <= r.ih && r.sw <= r.iw;
  console.log(`${w}x${h}: ${ok ? 'fits' : 'SCROLLS'} (${r.sw}x${r.sh}); fonts loaded: ${[...new Set(r.fonts)].join(', ')}`);
  failed ||= !ok;
}
await browser.close(); server.close();
process.exit(failed ? 1 : 0);
