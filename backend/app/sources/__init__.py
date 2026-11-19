"""Model-site registry: link detection, preview, search, import and license re-checks for every platform.

Add a model by URL (docs/add-by-url.md): MakerWorld, Printables, Thingiverse and Creality Cloud (richest: print
profiles, filament, files), Thangs, MyMiniFactory and Cults3D (details, photos, license; files from the browser
add-on), and any other link (a direct model file, or what the page says about itself) as platform "web"."""
import re
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.base import ApiError
from . import printables, thingiverse, creality, thangs, myminifactory, cults3d, web
from .base import PLATFORM_LABEL, account, preview_dict, import_normalized

PLATFORMS = ["makerworld", "printables", "thingiverse", "creality", "thangs", "myminifactory", "cults3d"]
ACCOUNT_KEY = {"makerworld": "mw_auth", "thingiverse": "thingiverse_auth", "creality": "creality_auth",
               "cults3d": "cults3d_auth"}
# Sites whose files the hub cannot download itself (sign-in or bot check): the add-on sends them.
BROWSER_FILES = {"thangs", "myminifactory", "cults3d"}
# Hosts each site is recognised on (the add-on's host permissions are the same list).
SITE_HOSTS = {"makerworld": ["makerworld.com"], "printables": ["printables.com"], "thingiverse": ["thingiverse.com"],
              "creality": ["crealitycloud.com"], "thangs": ["thangs.com"], "myminifactory": ["myminifactory.com"],
              "cults3d": ["cults3d.com"]}


def _host_is(url: str, platform: str) -> bool:
    from urllib.parse import urlsplit
    h = (urlsplit(url).hostname or "").lower()
    return any(h == d or h.endswith("." + d) for d in SITE_HOSTS[platform])


def detect(url: str) -> tuple[str, str]:
    """(platform, id). Any other http(s) link is ("web", <hash of the link>)."""
    from ..clients.makerworld_api import parse_design_id
    u = (url or "").strip()
    if not re.match(r"^https?://", u, re.I):
        if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}/", u, re.I):
            u = "https://" + u
        else:
            raise ValueError("Paste a link to a model page (MakerWorld, Printables, Thingiverse, Creality Cloud, Thangs, "
                             "MyMiniFactory, Cults3D) or to a model file")
    if _host_is(u, "makerworld"):
        return "makerworld", str(parse_design_id(u))
    for key, mod in (("printables", printables), ("thingiverse", thingiverse), ("creality", creality),
                     ("thangs", thangs), ("myminifactory", myminifactory), ("cults3d", cults3d)):
        if _host_is(u, key):
            i = mod.parse_id(u)
            if i:
                return key, i
            raise ValueError(f"That {PLATFORM_LABEL[key]} link is not a model page; open the model and copy its link")
    i = web.parse_id(u)
    if not i:
        raise ValueError("Paste a model page link or a link to a model file")
    return "web", i


def status(db: Session) -> dict:
    out = {}
    needs = {"makerworld": "file downloads", "thingiverse": "everything (App Token)", "creality": "file downloads",
             "printables": None, "thangs": None, "myminifactory": None,
             "cults3d": "reading models on the server (API key); files always come through the browser add-on"}
    for p in PLATFORMS:
        a = account(db, ACCOUNT_KEY[p]) if p in ACCOUNT_KEY else {}
        out[p] = {"label": PLATFORM_LABEL[p], "connected": bool(a.get("access_token") or a.get("token")),
                  "account": a.get("account"), "name": a.get("name"), "connected_at": a.get("connected_at"),
                  "needs_account_for": needs[p], "files_in_browser": p in BROWSER_FILES}
    return out


def fetch(db: Session, platform: str, ext_id: str, url: str | None = None) -> dict:
    if platform == "printables":
        return printables.fetch(ext_id)
    if platform == "thingiverse":
        return thingiverse.fetch(account(db, "thingiverse_auth").get("token"), ext_id)
    if platform == "creality":
        return creality.fetch(ext_id)
    if platform == "thangs":
        return thangs.fetch(ext_id)
    if platform == "myminifactory":
        return myminifactory.fetch(ext_id)
    if platform == "cults3d":
        return cults3d.fetch(account(db, "cults3d_auth"), ext_id)
    if platform == "web":
        if not url:
            raise ApiError("web", 400, "the link is needed for a web import")
        return web.fetch(url)
    raise ValueError(platform)


# ---------- what the browser add-on read off the page ----------
def _s(v, n: int) -> str | None:
    return v.strip()[:n] if isinstance(v, str) and v.strip() else None


def from_capture(platform: str, ext_id: str, url: str, cap: dict) -> dict:
    """A normalized model from the add-on's page capture (title, description, images, designer, license, tags,
    files). Used when the hub cannot read the site itself (Cults3D's bot check, a site that refuses servers).
    Everything is length-limited, and photos are later fetched through safe_fetch like any other link."""
    cap = cap if isinstance(cap, dict) else {}
    imgs = [i for i in (cap.get("images") or []) if isinstance(i, str) and i.startswith("https://")][:12]
    des = cap.get("designer") if isinstance(cap.get("designer"), dict) else {"name": cap.get("designer")}
    desc = _s(cap.get("description"), 20000) or ""
    files = []
    for f in (cap.get("files") or [])[:60]:
        if isinstance(f, dict) and _s(f.get("name"), 300):
            from .base import file_kind
            files.append({"id": _s(str(f.get("id") or f["name"]), 120), "name": _s(f["name"], 300),
                          "size": f.get("size") if isinstance(f.get("size"), int) else None, "kind": file_kind(f["name"])})
    lic = _s(cap.get("license"), 200)
    return {"platform": platform, "id": ext_id, "url": url, "title": _s(cap.get("title"), 300) or url,
            "description_html": "".join(f"<p>{escape(p)}</p>" for p in re.split(r"\n\s*\n", desc) if p.strip()),
            "tags": [t[:60] for t in (cap.get("tags") or []) if isinstance(t, str) and t.strip()][:20],
            "category": _s(cap.get("category"), 120),
            "designer": {"name": _s(des.get("name"), 120), "handle": _s(des.get("name"), 120), "url": _s(des.get("url"), 500)},
            "license_raw": lic, "license_text": _s(cap.get("license_url"), 500) or lic, "photos": imgs, "profiles": [],
            "files": files, "supplies": [], "stats": {}, "updated": None, "api_url": None,
            "paid": bool(cap.get("paid")), "files_in_browser": True, "download_page": url, "from_browser": True,
            "raw": {"captured_by": "OpenPrintHQ browser add-on", "capture": {k: cap.get(k) for k in (
                "title", "designer", "license", "license_url", "tags", "category", "images", "paid", "page_url")}}}


def fetch_or_capture(db: Session, platform: str, ext_id: str, url: str | None, capture: dict | None) -> dict:
    """The hub's own read of the site; the add-on's page capture when the site refuses the hub (bot check,
    Cults3D without an API key) and a capture came with the request."""
    try:
        return fetch(db, platform, ext_id, url)
    except ApiError as e:
        if capture and e.status in (401, 403, 429, 503):
            n = from_capture(platform, ext_id, url or "", capture)
            n["capture_reason"] = e.message
            return n
        raise


def _web_url(n: dict, url: str | None) -> str | None:
    return url or n.get("url")


def preview(db: Session, url: str, capture: dict | None = None) -> dict:
    platform, ext_id = detect(url)
    if platform == "makerworld":
        return None   # handled by the MakerWorld router (richer, BOM-aware)
    n = fetch_or_capture(db, platform, ext_id, url, capture)
    logged = {"printables": True, "thingiverse": bool(account(db, "thingiverse_auth").get("token")),
              "creality": bool(account(db, "creality_auth").get("token")), "thangs": True, "myminifactory": True,
              "cults3d": bool(account(db, "cults3d_auth").get("token")), "web": True}[platform]
    d = preview_dict(db, n, logged, platform == "creality")
    d["paid"] = bool(n.get("paid") or n.get("premium"))
    d["files_in_browser"] = bool(n.get("files_in_browser"))
    d["download_page"] = n.get("download_page")
    d["direct_file"] = bool(n.get("direct_file"))
    d["from_browser"] = bool(n.get("from_browser"))
    if n.get("capture_reason"):
        d["capture_reason"] = n["capture_reason"]
    return d


def search(db: Session, platform: str, q: str, licenses: list[str] | None, sort: str, offset: int, limit: int) -> dict:
    if platform == "printables":
        return printables.search(q, licenses, sort, offset, limit)
    if platform == "thingiverse":
        return thingiverse.search(account(db, "thingiverse_auth").get("token"), q, licenses, sort, offset, limit)
    if platform == "creality":
        return creality.search(q, licenses, sort, offset, limit)
    raise ValueError(platform)


def run_import(db: Session, platform: str, ext_id: str, profile_id=None, download: bool = True, actor: str | None = None,
               url: str | None = None, capture: dict | None = None):
    from ..services import safe_fetch
    if platform in ("thangs", "myminifactory", "cults3d", "web"):
        n = fetch_or_capture(db, platform, ext_id, url, capture)
        html = n.pop("_html", None)
        if html is None and platform == "thangs":
            html = thangs.page_html(n)
        ff = None
        if download and platform == "web" and n.get("files") and not n.get("from_browser"):
            ff = lambda prof: web.files_for(n)   # noqa: E731
        return import_normalized(db, n, profile_id, ff, html, safe_fetch.image_bytes, actor)
    n = fetch(db, platform, ext_id)
    if platform == "printables":
        ff = (lambda prof: printables.files_for(n)) if download else None
        html = printables.page_html(n)
        getb = printables.HTTP.bytes
    elif platform == "thingiverse":
        tok = account(db, "thingiverse_auth").get("token")
        ff = (lambda prof: thingiverse.files_for(tok, n)) if download else None
        html = thingiverse.page_html(n)
        getb = thingiverse.HTTP.bytes
    else:
        acct = account(db, "creality_auth")
        if download and acct.get("token"):
            ff = lambda prof: creality.files_for(acct, n, prof)   # noqa: E731
        elif download:
            ff = lambda prof: (_ for _ in ()).throw(ApiError("creality", 401, "connect your Creality Cloud account in Settings to download files"))  # noqa: E731
        else:
            ff = None
        html = n.pop("_html", None)
        getb = creality.HTTP.bytes
    n.pop("_html", None)
    return import_normalized(db, n, profile_id, ff, html, getb, actor)


def current_license(db: Session, platform: str, ext_id: str) -> str | None:
    """For the weekly re-check: the license the site shows today (None if the model is gone)."""
    try:
        return fetch(db, platform, ext_id).get("license_raw")
    except ApiError as e:
        if e.status == 404:
            return None
        raise


def existing_by_url(db: Session, url: str):
    from ..models import Model
    from .web import canonical
    c = canonical(url)
    return db.execute(select(Model).where(Model.source_url.in_([url.strip(), c]))).scalars().first()
