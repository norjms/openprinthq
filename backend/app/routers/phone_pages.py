"""Public pages for the phone app (2026-10-22).

- /pair?code=...   What a Connect a phone QR code opens. Any camera can read it (it is a plain https link). The page
                   opens the OpenPrintHQ app straight on the confirmation, or offers to install it. Nothing is used up
                   here: only the app's Connect button makes a key.
- /get-app         Install page: the Android APK (or the Play listing once there is one) and the iPhone app (TestFlight
                   or App Store link once there is one).
- /get-app/openprinthq.apk   The newest APK from android_apk_dir (also at /get-app/artisan-hub.apk, its address before
                             the rename, for links already sent).
- /.well-known/assetlinks.json                Android App Links: a verified app opens /pair links without the browser.
- /.well-known/apple-app-site-association      iOS Universal Links, only when ios_team_id is set (paid program).
- /l/<code>        What a spool or location label opens (2026-10-23): S<spool>, L<location>, P<product>, O<order>.
                   Signed out it shows only what kind of thing it is and its code; signed in to the hub it shows the
                   name too. Open in app and Get the app buttons; the app resolves the code with /api/mobile/v1/scan.
"""
import html
import json
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db

pages = APIRouter(include_in_schema=False)

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
# The app build names its file openprinthq-<version>.apk; builds from before the rename named it artisan-hub-<version>.apk.
APK_RE = re.compile(r"^(?:openprinthq|artisan-hub)-(\d+(?:\.\d+)*)\.apk$")


def _hub() -> str:
    return settings().public_url.rstrip("/")


def _app_link(code: str) -> str:
    """The app's own link (custom scheme): opens the app's confirmation screen. Every app version reads it."""
    return f"artisanhub://pair?hub={quote(_hub(), safe='')}&code={code}"


def _package() -> str:
    """The intent's package part, only when an app id is configured."""
    app = settings().app_id.strip()
    return f"package={app};" if app else ""


def _intent_link(code: str) -> str:
    """Android: open the app if it is installed, otherwise go to the install page (keeping the code)."""
    fallback = quote(f"{_hub()}/get-app?code={code}", safe="")
    return (f"intent://pair?hub={quote(_hub(), safe='')}&code={code}"
            f"#Intent;scheme=artisanhub;{_package()}S.browser_fallback_url={fallback};end")


def newest_apk() -> tuple[Path, str] | None:
    if not settings().android_apk_dir:
        return None
    d = Path(settings().android_apk_dir)
    best = None
    try:
        for f in d.iterdir():
            m = APK_RE.match(f.name)
            if m and f.is_file():
                v = tuple(int(x) for x in m.group(1).split("."))
                if best is None or v > best[0]:
                    best = (v, f, m.group(1))
    except OSError:
        return None
    return (best[1], best[2]) if best else None


CSS = """
:root{color-scheme:light dark;--bg:#f4f5f7;--card:#fff;--text:#16181d;--muted:#5d6470;--line:#dde0e6;--accent:#f0612a;--accent-text:#fff;--ok:#1f8a4c;--warn:#b4541a}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--card:#1c1f24;--text:#eceef2;--muted:#a0a7b3;--line:#2d3138;--accent:#ff7440;--accent-text:#16181d;--ok:#4cc683;--warn:#f0a167}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text)}
body{font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;min-height:100vh;display:flex;justify-content:center;align-items:flex-start;padding:max(24px,env(safe-area-inset-top)) 16px max(24px,env(safe-area-inset-bottom))}
main{width:100%;max-width:26rem}
.brand{display:flex;align-items:center;gap:.6rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-size:.95rem;margin:8px 0 20px}
.logo{width:2rem;height:2rem;border-radius:8px;background:var(--accent);color:var(--accent-text);display:grid;place-items:center;font-weight:800}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px}
h1{font-size:1.35rem;line-height:1.25;margin:0 0 6px}p{margin:0 0 12px}.muted{color:var(--muted)}.small{font-size:.875rem}
.btn{display:flex;align-items:center;justify-content:center;width:100%;min-height:56px;border-radius:12px;border:1px solid var(--line);background:transparent;color:var(--text);font:inherit;font-weight:600;text-decoration:none;margin-top:12px;padding:0 16px;cursor:pointer}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-text)}
.section{margin-top:22px;padding-top:18px;border-top:1px solid var(--line)}
.section h2{font-size:1rem;margin:0 0 6px}
ol{margin:6px 0 0;padding-left:1.2rem}li{margin:4px 0}
.note{border-radius:12px;padding:12px 14px;background:color-mix(in srgb,var(--accent) 12%,transparent);margin-top:14px}
.err{color:var(--warn);font-weight:600}
.hide{display:none}
code{font-size:.85em;word-break:break-all}
"""

# Platform detection runs in the page (iPadOS reports itself as a Mac with touch).
PLATFORM_JS = """
var ua=navigator.userAgent||"";var ios=/iPhone|iPad|iPod/.test(ua)||(/Macintosh/.test(ua)&&navigator.maxTouchPoints>1);
var android=/Android/.test(ua);var plat=ios?"ios":android?"android":"desktop";
document.documentElement.setAttribute("data-platform",plat);
document.querySelectorAll("[data-show]").forEach(function(el){if(el.getAttribute("data-show").split(" ").indexOf(plat)<0)el.classList.add("hide");});
"""


def _page(title: str, body: str, head: str = "", script: str = "") -> HTMLResponse:
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex"><title>{html.escape(title)}</title>{head}<style>{CSS}</style></head>
<body><main><div class="brand"><span class="logo">A</span>OpenPrintHQ</div>{body}</main>
<script>{PLATFORM_JS}{script}</script></body></html>"""
    return HTMLResponse(doc, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                      "X-Robots-Tag": "noindex"})


def _ios_head(code: str | None) -> str:
    sid = settings().ios_app_store_id.strip()
    if not sid.isdigit():
        return ""
    arg = f", app-argument={html.escape(_app_link(code))}" if code else ""
    return f'<meta name="apple-itunes-app" content="app-id={sid}{arg}">'


def _install_ios() -> str:
    s = settings()
    if s.ios_store_url:
        return (f'<a class="btn primary" href="{html.escape(s.ios_store_url)}">Get OpenPrintHQ for iPhone</a>'
                '<p class="muted small" style="margin-top:10px">After it installs, come back to this page and tap '
                '<b>Open in OpenPrintHQ</b>, or scan the code again.</p>')
    return ('<p>The iPhone app is not in the App Store yet. For now it is installed from a Mac with Xcode, so ask '
            'whoever runs this hub to put it on your iPhone.</p>'
            '<p class="muted small">Once it is installed, scan the code again or tap <b>Open in OpenPrintHQ</b>.</p>')


def _install_android(code: str | None) -> str:
    s = settings()
    if s.android_store_url:
        return f'<a class="btn primary" href="{html.escape(s.android_store_url)}">Get it on Google Play</a>'
    apk = newest_apk()
    if not apk:
        return '<p>The Android app is not available to download from this hub right now. Ask whoever runs it.</p>'
    after = ('<li>Come back here and tap <b>Open in OpenPrintHQ</b>.</li>' if code else
             '<li>Open OpenPrintHQ and tap <b>Scan QR code</b>.</li>')
    return (f'<a class="btn primary" href="/get-app/openprinthq.apk" download>Download the app (version {html.escape(apk[1])})</a>'
            '<ol class="small"><li>Open the downloaded file.</li>'
            '<li>If Android asks, allow installing apps from your browser (<b>Settings</b>, then turn on '
            '<b>Allow from this source</b>), go back and tap <b>Install</b>.</li>'
            f'{after}</ol>')


def _check_code(code: str, request: Request, db: Session) -> tuple[str | None, str | None]:
    """(team name, problem). Looks the code up without using it."""
    from .mobile import _open_pairing, _pair_limit
    from ..models import Team
    if not CODE_RE.match(code or ""):
        return None, "This link is missing its code. Scan the QR code in the hub again."
    try:
        _pair_limit(request)
        pp = _open_pairing(db, code)
    except HTTPException as e:
        msg = str(e.detail)
        return None, msg[:1].upper() + msg[1:] + "."
    team = db.get(Team, pp.team_id)
    return (team.name if team else ""), None


@pages.get("/pair")
def pair_page(request: Request, code: str = "", db: Session = Depends(get_db)):
    host = html.escape(_hub().split("://", 1)[-1])
    team, problem = _check_code(code, request, db)
    if problem:
        body = (f'<div class="card"><h1>This code can\'t be used</h1><p class="err">{html.escape(problem)}</p>'
                f'<p class="muted small">In the hub: Settings, API keys, Connect a phone, Show QR code.</p></div>')
        return _page("Connect a phone", body)
    app_link = html.escape(_app_link(code))
    intent = html.escape(_intent_link(code))
    who = f" for <b>{html.escape(team)}</b>" if team else ""
    body = f"""<div class="card">
<h1>Connect this phone</h1>
<p class="muted">To <b>{host}</b>{who}. The app shows exactly what it will be able to do, and nothing connects until you
tap <b>Connect</b> there.</p>
<div data-show="android"><a id="open-android" class="btn primary" href="{intent}">Open in OpenPrintHQ</a></div>
<div data-show="ios"><a id="open-ios" class="btn primary" href="{app_link}">Open in OpenPrintHQ</a></div>
<div data-show="desktop" class="note">Open this link on your phone: scan the QR code in the hub with the phone's
camera.</div>
<div id="no-app" class="note hide"><b>Didn't open?</b> The app may not be installed on this phone yet.</div>
<div class="section" data-show="android"><h2>Don't have the app?</h2>{_install_android(code)}</div>
<div class="section" data-show="ios"><h2>Don't have the app?</h2>{_install_ios()}</div>
<p class="muted small" style="margin-top:18px">This code works once and for 10 minutes.</p>
</div>"""
    # Android: try the app at once (Chrome falls back to the install page when it is missing). iPhone: Safari only
    # opens another app from a tap, so wait for one; if the page is still showing afterwards, point at the install part.
    script = """
var tried=false;function missed(){if(!document.hidden){var n=document.getElementById("no-app");if(n)n.classList.remove("hide");}}
if(plat==="android"){setTimeout(function(){if(!tried){tried=true;location.href=document.getElementById("open-android").href;}},300);}
["open-android","open-ios"].forEach(function(id){var a=document.getElementById(id);if(a)a.addEventListener("click",function(){tried=true;setTimeout(missed,1800);});});
"""
    return _page("Connect a phone", body, head=_ios_head(code), script=script)


LABEL_CODE_RE = re.compile(r"^[A-Za-z0-9]{2,20}$")


def _label_app_link(code: str) -> str:
    return f"artisanhub://l/{quote(code, safe='')}?hub={quote(_hub(), safe='')}"


def _label_intent(code: str) -> str:
    fallback = quote(f"{_hub()}/get-app", safe="")
    return (f"intent://l/{quote(code, safe='')}?hub={quote(_hub(), safe='')}"
            f"#Intent;scheme=artisanhub;{_package()}S.browser_fallback_url={fallback};end")


def _signed_in_summary(request: Request, db: Session, code: str) -> dict | None:
    """The code's name for someone signed in to the hub (a web session), else None. Never raises."""
    if not request.session.get("uid"):
        return None
    try:
        from .. import tenant
        from ..auth import current
        from ..services import scan
        p = current(request, db)
        if "view" not in (p.get("perms") or []):
            return None
        tenant.set_team(p["team_id"])
        tenant.bind_session(db, p["team_id"])
        r = scan.resolve(db, code)
        return {"title": r.get("title"), "subtitle": r.get("subtitle"), "kind": r.get("kind")}
    except Exception:  # noqa: BLE001
        return None


@pages.get("/l/{code}")
def label_page(code: str, request: Request, db: Session = Depends(get_db)):
    from ..services import scan
    if not LABEL_CODE_RE.match(code or ""):
        return _page("OpenPrintHQ", '<div class="card"><h1>This code can\'t be read</h1>'
                     '<p class="muted">Scan the label again with the OpenPrintHQ app.</p></div>')
    pub = scan.public_summary(code)
    who = _signed_in_summary(request, db, code)
    kinds = {"spool": "Filament spool", "location": "Location", "printer": "Printer", "ams": "AMS unit",
             "ams_slot": "AMS slot", "external": "External spool holder", "product": "Product", "order": "Order",
             "barcode": "Product barcode", "unknown": "OpenPrintHQ code"}
    title = html.escape((who or {}).get("title") or pub["title"])
    sub = html.escape((who or {}).get("subtitle") or kinds.get(pub["kind"], "OpenPrintHQ code"))
    c = html.escape(pub["code"])
    body = f"""<div class="card">
<p class="muted small">{html.escape(kinds.get((who or pub).get("kind") or pub["kind"], "OpenPrintHQ code"))} - <code>{c}</code></p>
<h1>{title}</h1>
<p class="muted">{sub}</p>
<div data-show="android"><a class="btn primary" href="{html.escape(_label_intent(pub['code']))}">Open in OpenPrintHQ</a></div>
<div data-show="ios"><a class="btn primary" href="{html.escape(_label_app_link(pub['code']))}">Open in OpenPrintHQ</a></div>
<div data-show="desktop" class="note">Scan this label with the OpenPrintHQ app on your phone to see it and act on it.</div>
<a class="btn" href="/get-app">Get the app</a>
<p class="muted small" style="margin-top:18px">{'Signed in to ' + html.escape(_hub().split('://', 1)[-1]) if who else 'Sign in to the hub or use the app to see the details.'}</p>
</div>"""
    return _page(f"{pub['code']} - OpenPrintHQ", body)


@pages.get("/get-app")
def get_app(request: Request, code: str = ""):
    code = code if CODE_RE.match(code or "") else ""
    back = ""
    if code:
        back = (f'<div data-show="android"><a class="btn" href="{html.escape(_intent_link(code))}">I\'ve installed it, '
                f'open OpenPrintHQ</a></div><div data-show="ios"><a class="btn" href="{html.escape(_app_link(code))}">'
                f'I\'ve installed it, open OpenPrintHQ</a></div>')
    body = f"""<div class="card">
<h1>Get the OpenPrintHQ app</h1>
<p class="muted">Watch the farm, plate checks, orders and stock from your phone.</p>
<div data-show="android desktop"><div class="section"><h2>Android</h2>{_install_android(code or None)}</div></div>
<div data-show="ios desktop"><div class="section"><h2>iPhone</h2>{_install_ios()}</div></div>
{back}
</div>"""
    return _page("Get the OpenPrintHQ app", body, head=_ios_head(code or None))


@pages.get("/get-app/openprinthq.apk")
@pages.get("/get-app/artisan-hub.apk", include_in_schema=False)
def get_apk():
    apk = newest_apk()
    if not apk:
        raise HTTPException(404, "no Android app on this hub")
    return FileResponse(apk[0], media_type="application/vnd.android.package-archive",
                        filename=f"openprinthq-{apk[1]}.apk", headers={"Cache-Control": "no-cache"})


@pages.get("/.well-known/assetlinks.json")
def assetlinks():
    fps = [f.strip().upper() for f in settings().android_cert_sha256.split(",") if f.strip()]
    if not settings().app_id.strip():
        fps = []
    data = [{"relation": ["delegate_permission/common.handle_all_urls"],
             "target": {"namespace": "android_app", "package_name": settings().app_id,
                        "sha256_cert_fingerprints": fps}}] if fps else []
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=3600"})


@pages.get("/.well-known/apple-app-site-association")
def apple_app_site_association():
    team = settings().ios_team_id.strip()
    if not team or not settings().app_id.strip():
        raise HTTPException(404, "not set up")
    app = f"{team}.{settings().app_id}"
    data = {"applinks": {"details": [{"appIDs": [app], "components": [{"/": "/pair", "comment": "Connect a phone"},
                                                                      {"/": "/l/*", "comment": "Spool and location labels"}]}]}}
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=3600"})
