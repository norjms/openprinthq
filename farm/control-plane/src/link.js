// OpenPrintHQ Link (B48). When the farm runs away from its printers (a cloud machine), a printer site runs
// OpenPrintHQ Link, which gives each printer it finds an address the farm reaches through the Link server. A scan
// of the site's network from here finds nothing: the farm is not on that network. So the farm asks the Link
// server which printers it sees instead, with a read-only key (OPHQ_LINK_TOKEN, the Link server's
// LINK_STATUS_TOKEN), and offers them where it offers what a scan found.

export function linkSettings(env = process.env) {
  return {
    url: String(env.OPHQ_LINK_URL || '').replace(/\/+$/, ''),
    token: String(env.OPHQ_LINK_TOKEN || '')
  };
}

export function linkConfigured(env = process.env) {
  const s = linkSettings(env);
  return Boolean(s.url && s.token);
}

// The Link server's status as one list of printers: each with its name, kind, model and serial as the site
// reported them, the address the farm reaches it at (ip_address), its site, and its address on the site's own
// network (found_at), which is what a person knows it by.
export function linkPrinters(status) {
  const out = [];
  for (const site of status?.sites || []) {
    for (const p of site.printers || []) {
      if (!p || !p.overlay) continue;
      out.push({
        name: p.name || '',
        family: p.family || '',
        model: p.model || '',
        serial: p.serial || '',
        ip_address: p.overlay,
        site: site.name || '',
        found_at: p.ip || '',
        online: Boolean(site.connected && p.online),
        link: true
      });
    }
  }
  return out;
}

export async function fetchLinkPrinters(env = process.env, fetchImpl = fetch) {
  const { url, token } = linkSettings(env);
  if (!url || !token) return { enabled: false, printers: [] };
  const r = await fetchImpl(url + '/api/status', {
    headers: { authorization: 'Bearer ' + token, accept: 'application/json' },
    signal: AbortSignal.timeout(5000)
  });
  if (!r.ok) throw new Error('OpenPrintHQ Link answered ' + r.status);
  return { enabled: true, printers: linkPrinters(await r.json()) };
}
