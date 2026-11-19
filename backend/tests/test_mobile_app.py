"""Phone app support: the look (tokens from the shared stylesheet) and the Start screen tiles."""
import pytest

from test_mobile_api import H, _client, _key, _user

CSS = """
@font-face { font-family: "IBM Plex Sans"; font-weight: 300; font-display: swap; src: url(fonts/plex-300.woff2) format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-weight: 400; font-display: swap; src: url(fonts/plex-400.woff2) format("woff2"); }
@font-face { font-family: "IBM Plex Mono"; font-weight: 400; font-display: swap; src: url(fonts/mono-400.woff2) format("woff2"); }
@font-face { font-family: "Inter"; font-weight: 400; font-display: swap; src: url(fonts/inter-400.woff2) format("woff2"); }
html[data-ah-skin] {
  --ah-font: "IBM Plex Sans", system-ui, sans-serif; --ah-mono: "IBM Plex Mono", monospace;
  --ah-bg: #f0f0f0; --ah-text: #333333; --ah-accent: #3b82f6; --ah-accent-text: #ffffff;
  --ah-radius: 8px; --ah-input-bg: var(--ah-bg); --ah-accent-soft: color-mix(in srgb, var(--ah-accent) 14%, transparent);
}
html[data-ah-skin="openprinthq"][data-ah-mode="dark"] { --ah-bg: #1e1e1e; --ah-text: #ffffff; --ah-accent: #38bdf8; }
html[data-ah-skin="classic"] { --ah-font: "Inter", sans-serif; --ah-accent: #ea580c; --ah-radius: 14px; }
html[data-ah-skin="classic"][data-ah-mode="dark"] { --ah-bg: #0f0e0d; }
html[data-ah-skin="classic"] .x { --ah-bg: #123456; }
html[data-ah-mode="dark"] { color-scheme: dark; }
"""


@pytest.fixture
def css(monkeypatch):
    from app.services import look
    monkeypatch.setattr(look, "load_css", lambda: CSS)


def test_tokens_cascade_and_resolve():
    from app.services import look
    wl = look.tokens(CSS, "openprinthq", "light")
    wd = look.tokens(CSS, "openprinthq", "dark")
    cd = look.tokens(CSS, "classic", "dark")
    assert wl["bg"] == "#f0f0f0" and wd["bg"] == "#1e1e1e" and wd["accent"] == "#38bdf8"
    assert wd["input-bg"] == "#1e1e1e" or wd["input-bg"] == "#f0f0f0"     # var() resolved to a colour
    assert cd["accent"] == "#ea580c" and cd["bg"] == "#0f0e0d" and cd["text"] == "#333333"
    pal = look.palette(cd)
    assert "accent_soft" not in pal and "font" not in pal and pal["accent_text"] == "#ffffff"


def test_font_files_and_skin_look():
    from app.services import look
    files = look.font_files(CSS)
    assert [f["weight"] for f in files["IBM Plex Sans"]] == [300, 400]
    assert files["IBM Plex Sans"][0]["url"] == "/theme/fonts/plex-300.woff2"
    assert "ttf" not in files["IBM Plex Sans"][0]                       # no TrueType copy next to it
    sl = look.skin_look(CSS, "classic")
    assert sl["fonts"]["body"]["family"] == "Inter" and sl["fonts"]["mono"]["family"] == "IBM Plex Mono"
    assert sl["web_radius_px"] == 14


def test_real_stylesheet_when_present():
    from app.services import look
    css = look.load_css()
    if css is None:
        pytest.skip("frontend stylesheet not in this checkout")
    for skin in look.SKINS:
        sl = look.skin_look(css, skin)
        for mode in look.MODES:
            for k in ("bg", "panel", "text", "muted", "accent", "ok", "warn", "bad"):
                assert k in sl["colors"][mode], (skin, mode, k)
        assert sl["fonts"]["body"]["files"], skin
    assert any(f["weight"] == 300 for f in look.font_files(css)["Inter"])
    if look._cache.get("dir"):
        assert all(f.get("ttf") for f in look.skin_look(css, "openprinthq")["fonts"]["body"]["files"])


def test_look_endpoint_follows_owner_and_etag(css):
    from app.db import session_scope
    from app.models import User
    uid, _ = _user()
    with session_scope() as db:
        db.get(User, uid).prefs = {"skin": "classic", "mode": "dark"}
    raw = _key(["read"], user_id=uid)
    c = _client()
    r = c.get("/api/mobile/v1/look", headers=H(raw))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["skin"] == "classic" and d["mode"] == "dark" and d["colors"]["dark"]["bg"] == "#0f0e0d"
    assert d["fonts"]["body"]["family"] == "Inter" and d["wordmark"].startswith("/theme/")
    r2 = c.get("/api/mobile/v1/look", headers={**H(raw), "If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304
    r3 = c.get("/api/mobile/v1/look?skin=openprinthq&all=true", headers=H(raw))
    assert r3.json()["skin"] == "openprinthq" and set(r3.json()["all"]) == {"openprinthq", "classic"}
    assert c.get("/api/mobile/v1/look?skin=legacy_farm", headers=H(raw)).status_code == 400
    assert c.get("/api/mobile/v1/look?skin=nope", headers=H(raw)).status_code == 400


def test_tiles_one_call(monkeypatch):
    from app.routers import mobile

    class WT:
        def printers(self):
            return {"printers": [{"name": "H2D", "state": "printing", "busy": True, "online": True, "progress": 64},
                                 {"name": "mk3s01", "state": "idle", "online": True},
                                 {"name": "A350", "state": "offline", "online": False}]}
    monkeypatch.setattr(mobile, "_farm", lambda: WT())
    raw = _key(["read"])
    r = _client().get("/api/mobile/v1/tiles", headers=H(raw))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["farm"]["printing"] == 1 and d["farm"]["idle"] == 1 and d["farm"]["offline"] == 1
    assert d["farm"]["now"][0]["name"] == "H2D"
    for k in ("plate_checks", "queue", "orders", "filament", "parts", "quotes", "store", "maintenance"):
        assert k in d, k
    assert "farm" not in d["errors"]


def test_tiles_without_the_farm(monkeypatch):
    from app.routers import mobile

    def boom():
        raise RuntimeError("The farm is down")
    monkeypatch.setattr(mobile, "_farm", boom)
    r = _client().get("/api/mobile/v1/tiles", headers=H(_key(["read"])))
    assert r.status_code == 200 and "farm" in r.json()["errors"] and "queue" in r.json()
