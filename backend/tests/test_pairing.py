"""Pairing a phone by QR code: make, preview, confirm once, tie to the phone, expire, cancel, limits."""
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from test_mobile_api import H, _client, _key, _user


def _maker(scopes=("read", "write", "api_keys")):
    uid, _ = _user()
    return _key(list(scopes), user_id=uid), uid


def _make(c, raw, **body):
    r = c.post("/api/mobile/v1/pairings", headers=H(raw), json=body or {})
    assert r.status_code == 200, r.text
    d = r.json()
    q = parse_qs(urlparse(d["url"]).query)
    u = urlparse(d["url"])
    return d, q["code"][0], f"{u.scheme}://{u.netloc}"


def test_qr_preview_confirm_and_tie_to_phone():
    c = _client()
    raw, _ = _maker()
    d, code, hub = _make(c, raw, access="act", days=30)
    # A plain https link to this hub's /pair page, so any phone camera opens it.
    assert urlparse(d["url"]).path == "/pair" and hub.startswith("http") and "hub=" not in d["url"]
    assert 'viewBox="' in d["qr_svg"] and 'width="' not in d["qr_svg"].split(">", 1)[0]
    assert d["qr_svg"].lstrip().startswith("<svg") and d["state"] == "waiting"

    # Preview: what the phone shows; nothing used up.
    for _ in range(2):
        p = c.get(f"/api/mobile/v1/pair/{code}")
        assert p.status_code == 200, p.text
    j = p.json()
    assert j["team"] and j["access_label"] == "View and act" and j["days"] == 30 and len(j["can"]) == 2

    # Confirm needs the phone's install id.
    assert c.post(f"/api/mobile/v1/pair/{code}/claim", json={"device": "iPhone"}).status_code == 400

    r = c.post(f"/api/mobile/v1/pair/{code}/claim", json={"device": "James  iPhone"}, headers={"X-Hub-Instance": "ah-phone-0001"})
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    assert r.json()["name"] == "James iPhone (QR)" and r.json()["scopes"] == ["read", "write"] and r.json()["expires_at"]

    # The key works on that phone only.
    assert c.get("/api/mobile/v1/me", headers=H(key, "ah-phone-0001")).status_code == 200
    assert c.get("/api/mobile/v1/me", headers=H(key, "ah-other-0002")).status_code == 401

    # Used once only; the hub page sees it connected.
    again = c.post(f"/api/mobile/v1/pair/{code}/claim", json={}, headers={"X-Hub-Instance": "ah-phone-0003"})
    assert again.status_code == 410
    assert c.get(f"/api/mobile/v1/pair/{code}").status_code == 410
    st = c.get(f"/api/mobile/v1/pairings/{d['id']}", headers=H(raw)).json()
    assert st["state"] == "connected" and st["device"] == "James iPhone" and st["key_id"]


def test_expired_cancelled_and_unknown_codes():
    from app.db import session_scope
    from app.models import PhonePairing
    c = _client()
    raw, _ = _maker()
    d, code, _ = _make(c, raw)
    with session_scope() as db:
        pp = db.get(PhonePairing, d["id"])
        pp.expires_at = pp.expires_at - timedelta(minutes=11)
    r = c.get(f"/api/mobile/v1/pair/{code}")
    assert r.status_code == 410 and "expired" in r.json()["detail"]
    assert c.get(f"/api/mobile/v1/pairings/{d['id']}", headers=H(raw)).json()["state"] == "expired"

    d2, code2, _ = _make(c, raw)
    assert c.delete(f"/api/mobile/v1/pairings/{d2['id']}", headers=H(raw)).status_code == 200
    assert c.post(f"/api/mobile/v1/pair/{code2}/claim", json={}, headers={"X-Hub-Instance": "ah-phone-0004"}).status_code == 410

    assert c.get("/api/mobile/v1/pair/not-a-real-code").status_code == 404


def test_access_choices_and_limits():
    c = _client()
    raw, _ = _maker()
    _, code, _ = _make(c, raw, access="view", days=None)
    p = c.get(f"/api/mobile/v1/pair/{code}").json()
    assert p["access_label"] == "View only" and p["days"] is None
    r = c.post(f"/api/mobile/v1/pair/{code}/claim", json={"device": "Pixel"}, headers={"X-Hub-Instance": "ah-phone-0005"}).json()
    assert r["scopes"] == ["read"] and r["expires_at"] is None

    # "Everything" never includes the owner scope, raw relay, or making more keys.
    _, code, _ = _make(c, raw, access="all")
    r = c.post(f"/api/mobile/v1/pair/{code}/claim", json={}, headers={"X-Hub-Instance": "ah-phone-0006"}).json()
    assert "api_keys" not in r["scopes"] and "owner" not in r["scopes"] and not any(s.startswith("relay") for s in r["scopes"])

    # A view-only maker cannot hand out "act".
    viewer, _ = _maker(("read", "api_keys"))
    assert c.post("/api/mobile/v1/pairings", headers=H(viewer), json={"access": "act"}).status_code == 403
    assert c.post("/api/mobile/v1/pairings", headers=H(raw), json={"access": "root"}).status_code == 400
    assert c.post("/api/mobile/v1/pairings", headers=H(raw), json={"days": 0}).status_code == 400
    # Making a pairing needs the api_keys permission.
    plain, _ = _maker(("read", "write"))
    assert c.post("/api/mobile/v1/pairings", headers=H(plain), json={}).status_code == 403


def test_pairing_is_rate_limited():
    from app.routers import mobile
    mobile._pair_hits.clear()
    c = _client()
    codes = [c.get("/api/mobile/v1/pair/nope-%d" % i).status_code for i in range(31)]
    assert codes[:30] == [404] * 30 and codes[30] == 429
    mobile._pair_hits.clear()


# ---------------------------------------------------------------- the /pair page, install page and app links

APP = "com.example.hub"
FP = ":".join(["AB"] * 32)


def test_pair_page_opens_the_app_or_offers_install(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), "app_id", APP)
    c = _client()
    raw, _ = _maker()
    d, code, hub = _make(c, raw, access="act", days=30)
    r = c.get(f"/pair?code={code}")          # public: no hub sign-in
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), r.text
    t = r.text
    assert "Open in OpenPrintHQ" in t and "have the app?" in t
    assert f"artisanhub://pair?hub=" in t and f"code={code}" in t                     # iPhone: the app's own link
    assert "intent://pair?" in t and f"package={APP};" in t               # Android: app or install page
    assert "S.browser_fallback_url=" in t and "get-app%3Fcode%3D" in t
    assert r.headers.get("cache-control") == "no-store"
    # Looking at the page uses nothing up: the app can still preview and claim.
    assert c.get(f"/api/mobile/v1/pair/{code}").status_code == 200


def test_pair_page_bad_used_and_missing_codes():
    c = _client()
    assert "missing its code" in c.get("/pair").text
    assert "missing its code" in c.get("/pair?code=<script>").text
    r = c.get("/pair?code=" + "x" * 32)
    assert r.status_code == 200 and "not from this hub" in r.text and "Open in OpenPrintHQ" not in r.text
    raw, _ = _maker()
    d, code, _ = _make(c, raw)
    c.delete(f"/api/mobile/v1/pairings/{d['id']}", headers=H(raw))
    assert "cancelled" in c.get(f"/pair?code={code}").text


def test_get_app_page_and_apk(tmp_path, monkeypatch):
    from app.config import settings
    (tmp_path / "artisan-hub-0.2.0.apk").write_bytes(b"old")
    (tmp_path / "artisan-hub-0.10.1.apk").write_bytes(b"newest")
    (tmp_path / "notes.txt").write_text("x")
    monkeypatch.setattr(settings(), "android_apk_dir", str(tmp_path))
    c = _client()
    r = c.get("/get-app?code=" + "a" * 32)
    assert r.status_code == 200 and "version 0.10.1" in r.text and "App Store yet" in r.text
    assert "intent://pair?" in r.text                                   # back to the app after installing
    a = c.get("/get-app/artisan-hub.apk")
    assert a.status_code == 200 and a.content == b"newest"
    assert a.headers["content-type"] == "application/vnd.android.package-archive"
    monkeypatch.setattr(settings(), "ios_store_url", "https://testflight.apple.com/join/abc")
    monkeypatch.setattr(settings(), "ios_app_store_id", "123456")
    r = c.get("/get-app")
    assert "testflight.apple.com/join/abc" in r.text and 'name="apple-itunes-app" content="app-id=123456"' in r.text
    monkeypatch.setattr(settings(), "android_apk_dir", str(tmp_path / "none"))
    assert c.get("/get-app/artisan-hub.apk").status_code == 404


def test_app_link_files(monkeypatch):
    from app.config import settings
    c = _client()
    assert c.get("/.well-known/assetlinks.json").json() == []                         # neutral defaults: no app links
    assert "package=" not in c.get("/get-app?code=" + "a" * 32).text
    monkeypatch.setattr(settings(), "app_id", APP)
    monkeypatch.setattr(settings(), "android_cert_sha256", FP)
    a = c.get("/.well-known/assetlinks.json")
    assert a.status_code == 200
    t = a.json()[0]["target"]
    assert t["package_name"] == APP and len(t["sha256_cert_fingerprints"][0].split(":")) == 32
    assert c.get("/.well-known/apple-app-site-association").status_code == 404     # no paid Apple team yet
    s = settings()
    old = s.ios_team_id
    s.ios_team_id = "ABCDE12345"
    try:
        j = c.get("/.well-known/apple-app-site-association").json()
        assert j["applinks"]["details"][0]["appIDs"] == [f"ABCDE12345.{APP}"]
        assert j["applinks"]["details"][0]["components"][0]["/"] == "/pair"
    finally:
        s.ios_team_id = old
