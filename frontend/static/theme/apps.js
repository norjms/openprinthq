/* OpenPrintHQ shared look for the supporting apps. Added by npmplus right before </head>, so it runs before the
   app's own (deferred) code: it marks <html> with the app, skin and light/dark mode, and switches the app's own
   light/dark setting to match.
   Where the look comes from, first match wins:
   1. the ah_look cookie the hub sets on its own parent domain (apps on the same domain, the sign-in pages);
   2. /_ahtheme/look, which npmplus answers for apps behind Authentik forward auth (Spoolman, Manyfold);
   3. what this app saw last time (localStorage), else the openprinthq skin following the device's light/dark. */
(function () {
  var me = document.currentScript, app = (me && me.getAttribute('data-app')) || 'app';
  var html = document.documentElement, SKINS = { openprinthq: 1, classic: 1 }, MODES = { system: 1, light: 1, dark: 1 };
  var mq = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : null;
  var look = { skin: 'openprinthq', mode: 'system' }, applied = null;

  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function put(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function parse(v) {
    var p = String(v || '').split('.');
    return SKINS[p[0]] ? { skin: p[0], mode: MODES[p[1]] ? p[1] : 'system' } : null;
  }
  function cookie() { var m = document.cookie.match(/(?:^|;\s*)ah_look=([^;]+)/); return m ? parse(decodeURIComponent(m[1])) : null; }
  function isDark() { return look.mode === 'dark' || (look.mode === 'system' && !!(mq && mq.matches)); }

  // Each app's own light/dark switch, so its built-in colours agree with ours.
  var hooks = {
    spoolman: function (dark, first) { return own('colorMode', dark ? 'dark' : 'light', first); },
    inventree: function (dark, first) {
      html.setAttribute('data-mantine-color-scheme', dark ? 'dark' : 'light');
      return own('mantine-color-scheme-value', dark ? 'dark' : 'light', first);
    },
    manyfold: function (dark) {
      var t = dark ? 'dark' : 'light';
      html.setAttribute('data-bs-theme', t);
      var nav = document.getElementById('main-navbar');
      if (nav) nav.setAttribute('data-bs-theme', t);
    },
    authentik: function (dark) {
      html.setAttribute('data-theme', dark ? 'dark' : 'light');
      html.setAttribute('data-theme-choice', dark ? 'dark' : 'light');
    }
  };
  // Apps that read their light/dark once at start: set it before they start; if it changes later, reload once.
  function own(key, val, first) {
    if (get(key) === val) return false;
    put(key, val);
    return !first;
  }

  function apply(first) {
    var dark = isDark(), sig = look.skin + '.' + (dark ? 'dark' : 'light');
    html.setAttribute('data-ah-app', app);
    html.setAttribute('data-ah-skin', look.skin);
    html.setAttribute('data-ah-mode', dark ? 'dark' : 'light');
    var reload = hooks[app] ? hooks[app](dark, first) : false;
    if (reload && applied !== null && applied !== sig) {
      try { if (sessionStorage.getItem('ah.reloaded') !== sig) { sessionStorage.setItem('ah.reloaded', sig); location.reload(); } } catch (e) {}
    }
    applied = sig;
  }
  function set(l, first) {
    if (!l) return;
    look = l;
    put('ah.look', l.skin + '.' + l.mode);
    apply(first);
  }

  var fromCookie = cookie();
  set(fromCookie || parse(get('ah.look')) || look, true);
  if (!fromCookie && app !== 'authentik') {
    try {
      fetch('/_ahtheme/look', { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) { return r.status === 200 ? r.json() : null; })
        .then(function (j) { if (j && SKINS[j.skin]) set({ skin: j.skin, mode: MODES[j.mode] ? j.mode : 'system' }); })
        .catch(function () {});
    } catch (e) {}
  }
  if (mq && mq.addEventListener) mq.addEventListener('change', function () { if (look.mode === 'system') apply(); });
  // Manyfold swaps pages with Turbo and re-renders its navbar; the apps' own code can also reset these attributes.
  document.addEventListener('turbo:load', function () { apply(); });
  document.addEventListener('DOMContentLoaded', function () { apply(); });
  if (window.MutationObserver) {
    var busy = false;
    new MutationObserver(function () {
      if (busy) return;
      var want = isDark() ? 'dark' : 'light';
      var drift = html.getAttribute('data-ah-mode') !== want ||
        (app === 'inventree' && html.getAttribute('data-mantine-color-scheme') !== want) ||
        (app === 'manyfold' && html.getAttribute('data-bs-theme') !== want) ||
        (app === 'authentik' && html.getAttribute('data-theme') !== want);
      if (drift) { busy = true; apply(); busy = false; }
    }).observe(html, { attributes: true, attributeFilter: ['data-ah-mode', 'data-mantine-color-scheme', 'data-bs-theme', 'data-theme'] });
  }
})();
