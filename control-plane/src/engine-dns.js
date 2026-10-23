// Resolvers for tenant engine containers.
//
// OPHQ_ENGINE_DNS (space or comma separated) gives every tenant engine its own
// DNS servers. It exists for deployments where the LAN resolver answers a public
// hostname with a private address (split-horizon DNS) while an egress firewall
// keeps tenant containers off private addresses: an engine webhook to such a
// hostname would otherwise resolve to the private address and time out. With
// public resolvers the name resolves to its public address instead. Container
// names on the engine's networks still resolve through Docker's embedded DNS.
//
// Setting it also clears the search domain (--dns-search .). A host search
// domain that has a public wildcard record would otherwise turn any unknown
// short name into a public address instead of a lookup failure.
//
// Unset, an engine keeps whatever the container already had (or Docker's
// default), so a deployment that does not need this is unchanged.

export function parseEngineDns(value) {
  return String(value || '').split(/[\s,]+/).filter(Boolean);
}

// docker run arguments. `inspect` is the current container's `docker inspect`
// object when an engine is being recreated, so its settings carry forward.
export function engineDnsArgs(servers, inspect = null) {
  if (servers.length) {
    return [...servers.flatMap((d) => ['--dns', d]), '--dns-search', '.'];
  }
  const hc = inspect?.HostConfig || {};
  return [
    ...(hc.Dns || []).flatMap((d) => ['--dns', d]),
    ...(hc.DnsSearch || []).flatMap((d) => ['--dns-search', d]),
  ];
}
