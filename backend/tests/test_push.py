"""Phone app notifications: the envelope, registering a phone, delivery through the relay, email for people who
opt out, the installation switch, and the new print and order alerts."""
import json
import secrets as _s
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi.testclient import TestClient

VECTORS = json.loads((Path(__file__).parent / "push_vectors.json").read_text())
RELAY = "https://relay.example.test"


def _client():
    from app.main import app
    return TestClient(app, follow_redirects=False)


def _person(role="owner"):
    from app.db import session_scope
    from app.models import Membership, User
    from app.tenant import unscoped
    tag = _s.token_hex(3)
    with session_scope() as db, unscoped():
        u = User(sub=f"p-{tag}", email=f"p-{tag}@x.example", name=f"P{tag}")
        db.add(u)
        db.flush()
        db.add(Membership(team_id=1, user_id=u.id, role=role))
        return u.id, u.email


def _key(user_id, scopes=("read", "write", "admin")):
    from app.auth import new_api_key
    from app.db import session_scope
    with session_scope() as db:
        return new_api_key(db, "phone", list(scopes), team_id=1, user_id=user_id)


def H(raw):
    return {"Authorization": f"Bearer {raw}"}


def _phone():
    k = X25519PrivateKey.generate()
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    from app.services.push import b64e
    return k, b64e(pub)


def _open(priv, envelope):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    from app.services.push import INFO, b64d
    raw = b64d(envelope)
    eph, nonce, ct = raw[1:33], raw[33:45], raw[45:]
    dev = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=eph + dev, info=INFO).derive(
        priv.exchange(X25519PublicKey.from_public_bytes(eph)))
    return json.loads(ChaCha20Poly1305(key).decrypt(nonce, ct, None))


@pytest.fixture
def relay(monkeypatch):
    """A stand-in relay: records what the hub sends; tokens starting 'gone' answer 410."""
    sent = []

    def post(url, json=None, timeout=None, **kw):
        sent.append((url, json))
        code = 410 if json["token"].startswith("gone") else 200
        return httpx.Response(code, json={"ok": code == 200}, request=httpx.Request("POST", url))

    def get(url, timeout=None, **kw):
        return httpx.Response(200, json={"service": "openprinthq-push-relay", "delivery_log": True, "source": "https://example.test/src"},
                              request=httpx.Request("GET", url))

    from app.services import push
    monkeypatch.setattr(push.httpx, "post", post)
    monkeypatch.setattr(push.httpx, "get", get)
    push._status_cache.clear()
    from app.db import session_scope
    from app.kv import set_setting
    with session_scope() as db:
        set_setting(db, "push_relay_url", RELAY)
        set_setting(db, "push_mode", "on")
    yield sent
    with session_scope() as db:
        set_setting(db, "push_relay_url", None)
        set_setting(db, "push_mode", None)


@pytest.fixture
def mails(monkeypatch):
    out = []
    import app.tasks as tasks
    monkeypatch.setattr(tasks, "send_mail", lambda subject, body, db=None, to=None, **kw: out.append((subject, to)))
    return out


def test_seal_matches_relay_vectors():
    from app.services.push import b64d, seal
    for v in VECTORS:
        eph = X25519PrivateKey.from_private_bytes(b64d(v["ephemeral_private"]))
        assert seal(v["device_public"], v["message"], _eph=eph, _nonce=b64d(v["nonce"])) == v["envelope"]


def test_register_deliver_and_forget(relay):
    uid, _ = _person()
    raw = _key(uid)
    c = _client()
    priv, pub = _phone()
    st = c.get("/api/mobile/v1/push/state", headers=H(raw)).json()
    assert st["available"] and st["choice"] is None and st["devices"] == []
    assert any("cannot read" in p for p in st["disclaimer"]["points"])
    assert any("in testing" in p for p in st["disclaimer"]["points"])      # the stand-in relay logs deliveries
    r = c.post("/api/mobile/v1/push/devices", headers=H(raw), json={
        "platform": "apns", "token": "ab" * 32, "public_key": pub, "label": "James's iPhone", "app_version": "0.9.0"})
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["choice"] == "on" and len(st["devices"]) == 1 and st["this_device"] == st["devices"][0]["id"]

    from app.db import session_scope
    from app.services import push
    with session_scope() as db:
        out = push.deliver(db, {"kind": "print_done", "title": "H2D finished Gear housing",
                                "message": "Clear the plate.", "link": "https://hub/farm", "ref": "check:9"})
    assert out.startswith("push 1 sent")
    url, body = relay[-1]
    assert url == RELAY + "/v1/send" and body["platform"] == "apns" and body["collapse"]
    assert "Gear housing" not in json.dumps(body)                              # the relay sees sealed bytes only
    msg = _open(priv, body["sealed"])
    assert msg["t"] == "H2D finished Gear housing" and msg["k"] == "print_done" and msg["l"] == "https://hub/farm"

    # Apple says the token is gone: the hub forgets the phone.
    c.post("/api/mobile/v1/push/devices", headers=H(raw), json={"platform": "fcm", "token": "gone" + "x" * 30, "public_key": pub})
    with session_scope() as db:
        push.deliver(db, {"kind": "alarm", "title": "x", "message": "y"})
    assert c.get("/api/mobile/v1/push/state", headers=H(raw)).json()["devices"] == []


def test_opt_out_goes_to_email(relay, mails):
    uid, email = _person()
    raw = _key(uid)
    c = _client()
    _, pub = _phone()
    c.post("/api/mobile/v1/push/devices", headers=H(raw), json={"platform": "apns", "token": "cd" * 32, "public_key": pub})
    st = c.put("/api/mobile/v1/push/choice", headers=H(raw), json={"choice": "off"}).json()
    assert st["choice"] == "off" and st["devices"] == [] and st["mapping_in_app"] is False
    from app.db import session_scope
    from app.services import push
    n = len(relay)
    with session_scope() as db:
        out = push.deliver(db, {"kind": "print_failed", "title": "Print failed", "message": "Check the plate."})
    assert "by email" in out and any(email in (to or []) for _, to in mails)
    assert all(b["token"] != "cd" * 32 for _, b in relay[n:])


def test_installation_off_sends_email_instead(relay, mails):
    uid, email = _person()
    raw, admin = _key(uid), _key(_person()[0])
    c = _client()
    _, pub = _phone()
    c.post("/api/mobile/v1/push/devices", headers=H(raw), json={"platform": "apns", "token": "ef" * 32, "public_key": pub})
    assert c.put("/api/push/settings", headers=H(admin), json={"mode": "off"}).json()["available"] is False
    n = len(relay)
    from app.db import session_scope
    from app.services import push
    with session_scope() as db:
        push.deliver(db, {"kind": "order_new", "title": "New order #1001", "message": ""})
    assert len(relay) == n and any(email in (to or []) for _, to in mails)
    # and no new phones while it is off
    assert c.post("/api/mobile/v1/push/devices", headers=H(raw),
                  json={"platform": "apns", "token": "12" * 32, "public_key": pub}).status_code == 409


def test_relay_address_must_be_https(relay):
    admin = _key(_person()[0])
    r = _client().put("/api/push/settings", headers=H(admin), json={"relay_url": "http://relay.example.com"})
    assert r.status_code == 422


def test_bad_public_key_refused(relay):
    raw = _key(_person()[0])
    r = _client().post("/api/mobile/v1/push/devices", headers=H(raw),
                       json={"platform": "apns", "token": "aa" * 32, "public_key": "x" * 40})   # 30 bytes
    assert r.status_code == 422


def test_push_is_on_for_every_alert_by_default():
    from app.db import session_scope
    from app.kv import set_setting
    from app.services import alerts
    assert all("push" in v for v in alerts.DEFAULT_RULES["events"].values())
    assert alerts.DEFAULT_RULES["events"]["order_new"] == ["push"]
    with session_scope() as db:
        old = alerts.rules(db)
        # rules saved before the app channel existed still get it
        set_setting(db, "alert_rules", {"events": {"alarm": ["email"]}})
        assert alerts.rules(db)["events"]["alarm"] == ["push", "email"]
        set_setting(db, "alert_rules", {"events": {"alarm": ["email"]}, "channels_known": list(alerts.CHANNELS)})
        assert alerts.rules(db)["events"]["alarm"] == ["email"]
        set_setting(db, "alert_rules", None)
        assert old


def test_order_and_print_updates(monkeypatch):
    from app.services import alerts, farm
    seen = []
    monkeypatch.setattr(alerts, "notify", lambda db, kind, title, message, **kw: seen.append((kind, title)))
    from types import SimpleNamespace as NS
    o = NS(id=5, number="1001", customer="Ada", total=24.5, currency="USD", status="processing", is_test=False)
    farm.order_alert(None, o, None)
    o.status = "completed"
    farm.order_alert(None, o, "processing")
    o.status = "refunded"
    farm.order_alert(None, o, "completed")
    farm.order_alert(None, NS(**{**o.__dict__, "is_test": True, "status": "processing"}), None)
    assert [k for k, _ in seen] == ["order_new", "order_shipped", "order_cancelled"]
    assert seen[0][1] == "New order #1001"
