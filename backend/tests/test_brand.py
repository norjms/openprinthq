"""The shop's own logo (docs/brand.md): uploaded in Settings, served by the hub, drawn by the phone app."""
import pytest
from fastapi.testclient import TestClient

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="420" height="64" viewBox="0 0 420 64">'
       b'<defs><linearGradient id="g"><stop offset="0" stop-color="#5b7393"/></linearGradient></defs>'
       b'<text x="10" y="44" fill="url(#g)">ARTISAN PRINTS</text></svg>')
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def client():
    from app.main import app
    from app.services import brand
    brand.remove()
    yield TestClient(app)
    brand.remove()


def _key(scopes=("read", "write", "admin")):
    from app.auth import new_api_key
    from app.db import session_scope
    from app.tenant import use_team
    with session_scope() as db, use_team(1):
        return {"Authorization": f"Bearer {new_api_key(db, 'brand test', list(scopes), team_id=1)}"}


def _put(client, data, media, headers=None):
    return client.put("/api/brand/v1/wordmark", files={"file": ("logo", data, media)}, headers=headers or _key())


def test_brand_01_an_uploaded_logo_is_served_and_the_phone_draws_it(client):
    """BRAND-01: the owner uploads an SVG; anyone can load it, safely; the phone's look names it, versioned."""
    from app.services import look
    assert client.get("/api/brand/v1").json() == {"wordmark": None}
    assert look._wordmark() == "/theme/openprinthq-wordmark.svg"
    r = _put(client, SVG, "image/svg+xml")
    assert r.status_code == 200, r.text
    url = r.json()["wordmark"]
    assert url.startswith("/api/brand/v1/wordmark?v=")
    got = client.get(url)
    assert got.status_code == 200 and got.content == SVG
    assert got.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in got.headers["content-security-policy"] and "sandbox" in got.headers["content-security-policy"]
    assert got.headers["x-content-type-options"] == "nosniff"
    assert look._wordmark() == url


def test_brand_02_a_png_replaces_an_svg_and_removing_goes_back(client):
    """BRAND-02: a new logo replaces the old one, with a new version; removing it brings back the default."""
    first = _put(client, SVG, "image/svg+xml").json()["wordmark"]
    second = _put(client, PNG, "image/png").json()["wordmark"]
    assert first != second
    assert client.get("/api/brand/v1/wordmark").headers["content-type"] == "image/png"
    assert client.delete("/api/brand/v1/wordmark", headers=_key()).json() == {"wordmark": None}
    assert client.get("/api/brand/v1/wordmark").status_code == 404


@pytest.mark.parametrize("data,media,why", [
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "image/svg+xml", "scripts"),
    (b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"></svg>', "image/svg+xml", "event handlers"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/a.png"/></svg>', "image/svg+xml",
     "outside the file"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>', "image/svg+xml", "foreignObject"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import "x.css";</style></svg>', "image/svg+xml",
     "outside the file"),
    (b"not a png", "image/png", "not a PNG"),
    (b"GIF89a", "image/gif", "SVG or a PNG"),
    (b"<svg>" + b" " * (256 * 1024), "image/svg+xml", "256 KB"),
])
def test_brand_03_unsafe_or_wrong_files_are_refused(client, data, media, why):
    """BRAND-03: only SVG and PNG, at most 256 KB, and an SVG that can run or load nothing."""
    r = _put(client, data, media)
    assert r.status_code == 422 and why in r.text, r.text
    assert client.get("/api/brand/v1").json() == {"wordmark": None}


def test_brand_04_only_the_owner_or_a_first_team_admin_changes_it(client, monkeypatch):
    """BRAND-04: reading is open; changing needs the settings permission in the first team."""
    assert _put(client, SVG, "image/svg+xml", _key(("read",))).status_code == 403
    assert client.delete("/api/brand/v1/wordmark", headers=_key(("read",))).status_code == 403
    from app.config import settings
    monkeypatch.setattr(settings(), "dev_auth_bypass", False)
    assert client.put("/api/brand/v1/wordmark", files={"file": ("l", SVG, "image/svg+xml")}).status_code == 401
    assert client.get("/api/brand/v1").status_code == 200
