"""Phone app notifications through a push relay (openprinthq-push-relay), encrypted end to end.

Apple and Google take push messages only from whoever holds the app's keys, so the hub hands each alert to a push
relay that holds them. Before it leaves the hub the alert is sealed for one phone with that phone's public key
(X25519, HKDF-SHA256, ChaCha20-Poly1305: docs/push.md); only the phone has the private key. The relay, Apple and
Google see a push token and bytes they cannot read. The hub keeps a phone's public key and push token
(PushDevice) and nothing about what was sent beyond the alert job itself, as for every other channel.

Choices:
- The installation (team setting push_mode): "on" (the default) sends through the relay; "off" sends nothing to
  the relay, and people who turned push on get their alerts by email instead.
- The relay (team setting push_relay_url, else HUB_PUSH_RELAY_URL): any relay, so an installation can use its own.
- Each person (User.prefs["push"]): "on" when they turn notifications on in the app; "off" when they opt out, which
  sends their alerts by email instead and keeps filament mapping to the hub's web pages. Not set: neither.
"""
import base64
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..jobs import event
from ..kv import setting
from ..models import Membership, PushDevice, User

log = logging.getLogger("hub.push")
INFO = b"artisanhub-push-v1"
RAW = serialization.Encoding.Raw, serialization.PublicFormat.Raw
MAX_PLAIN = 1800          # bytes of JSON; the sealed envelope must fit Apple's 4 KB with room to spare
MODES = ("on", "off")
CHOICES = ("on", "off")


def now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- the envelope

def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def public_key_ok(key: str) -> bool:
    try:
        X25519PublicKey.from_public_bytes(b64d(key))
        return True
    except Exception:  # noqa: BLE001
        return False


def seal(device_public: str, message: dict, *, _eph: X25519PrivateKey | None = None,
         _nonce: bytes | None = None) -> str:
    """0x01 || one-time public key || nonce || ChaCha20-Poly1305(JSON), base64url. Matches the relay's
    reference/envelope.py and its test vectors."""
    dev = b64d(device_public)
    eph = _eph or X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(*RAW)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(dev))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_pub + dev, info=INFO).derive(shared)
    nonce = _nonce or os.urandom(12)
    ct = ChaCha20Poly1305(key).encrypt(nonce, json.dumps(message, separators=(",", ":")).encode(), None)
    return b64e(b"\x01" + eph_pub + nonce + ct)


def message(kind: str, title: str, body: str, link: str | None, ref: str | None, alert_id: int | None = None) -> dict:
    m = {"k": kind, "t": (title or "")[:200], "b": body or "", "l": link or "", "id": ref or "", "ts": int(time.time())}
    if alert_id:
        m["a"] = alert_id
    while len(json.dumps(m, separators=(",", ":")).encode()) > MAX_PLAIN and m["b"]:
        m["b"] = m["b"][: max(0, len(m["b"]) - 100)].rstrip() + "…" if len(m["b"]) > 100 else ""
    return m


def collapse(ref: str | None) -> str | None:
    """A newer alert about the same thing replaces the older one on the phone. A hash: the relay sees no names."""
    return hashlib.sha256(ref.encode()).hexdigest()[:24] if ref else None


# ---------------------------------------------------------------- settings

def relay_url(db: Session) -> str:
    return str(setting(db, "push_relay_url") or settings().push_relay_url or "").rstrip("/")


def mode(db: Session) -> str:
    m = setting(db, "push_mode") or "on"
    return m if m in MODES else "on"


def choice(user: User | None) -> str | None:
    c = ((user.prefs or {}).get("push") if user else None)
    return c if c in CHOICES else None


def set_choice(db: Session, user: User, value: str | None):
    prefs = dict(user.prefs or {})
    if value is None:
        prefs.pop("push", None)
    else:
        prefs["push"] = value
    user.prefs = prefs
    if value != "on":
        for d in db.execute(select(PushDevice).where(PushDevice.user_id == user.id)).scalars():
            db.delete(d)


_status_cache: dict[str, tuple[float, dict]] = {}


def relay_status(url: str, fresh: bool = False) -> dict:
    """The relay's own /status: what it keeps and whether its delivery log is on. Cached for five minutes."""
    if not url:
        return {"reachable": False, "error": "no relay address"}
    hit = _status_cache.get(url)
    if hit and not fresh and time.time() - hit[0] < 300:
        return hit[1]
    try:
        r = httpx.get(url + "/status", timeout=8)
        r.raise_for_status()
        d = {"reachable": True, **r.json()}
    except Exception as e:  # noqa: BLE001
        d = {"reachable": False, "error": str(e)[:200]}
    _status_cache[url] = (time.time(), d)
    return d


def disclaimer(db: Session) -> dict:
    """What a person is told before they turn notifications on, and on the settings page. The same text in the app
    and on the web."""
    url = relay_url(db)
    st = relay_status(url) if url else {"reachable": False}
    host = httpx.URL(url).host if url else ""
    logged = st.get("delivery_log")
    points = [
        "Your hub encrypts every notification for your phone before it leaves the hub. Only this phone has the key "
        "that opens it; it is made on the phone and never leaves it.",
        f"The push relay{f' ({host})' if host else ''} passes the sealed message to Apple or Google. It cannot read "
        "it, and neither can Apple, Google or whoever runs the relay.",
        "The relay keeps nothing: no database, no files, no record of who sent what. It sees your phone's push "
        "address and the size of each message while passing it on.",
        "Your hub keeps your phone's public key and push address so it can reach you, and forgets them when you turn "
        "notifications off or remove the phone.",
    ]
    if logged is True:
        points.append("This relay is in testing and writes one line per delivery (time, a short code for the "
                      "phone, size, result; never the message). That log is turned off for general use.")
    elif logged is False:
        points.append("This relay's delivery log is off.")
    points.append("Rather not use the relay? Choose email instead: your alerts come by email from your hub, and "
                  "filament mapping stays on the hub's web pages.")
    return {"title": "How phone notifications work", "points": points, "relay": url, "relay_status": st,
            "source": st.get("source") or "https://github.com/norjms/openprinthq-push-relay"}


# ---------------------------------------------------------------- devices

def register(db: Session, user_id: int | None, key_id: int | None, body: dict) -> PushDevice:
    token = str(body["token"]).strip()
    h = hashlib.sha256(token.encode()).hexdigest()
    d = db.execute(select(PushDevice).where(PushDevice.token_hash == h)).scalar()
    if d is None:
        old = None
        if key_id:     # the same app install with a new token: replace its old row
            old = db.execute(select(PushDevice).where(PushDevice.api_key_id == key_id)).scalar()
        d = old or PushDevice(created_at=now())
        if old is None:
            db.add(d)
    d.user_id, d.api_key_id = user_id, key_id
    d.platform, d.token, d.token_hash = body["platform"], token, h
    d.public_key = body["public_key"]
    d.apns_env = body.get("apns_env") or "production"
    d.apns_topic = body.get("apns_topic")
    d.label = (body.get("label") or "")[:120] or None
    d.app_version = (body.get("app_version") or "")[:40] or None
    d.last_seen_at, d.last_error = now(), None
    db.flush()
    return d


def view(d: PushDevice) -> dict:
    iso = lambda t: t.isoformat() if t else None  # noqa: E731
    return {"id": d.id, "platform": d.platform, "label": d.label, "app_version": d.app_version,
            "created_at": iso(d.created_at), "last_seen_at": iso(d.last_seen_at), "last_sent_at": iso(d.last_sent_at),
            "last_error": d.last_error}


# ---------------------------------------------------------------- delivery

def _members(db: Session) -> list[User]:
    from ..tenant import require_team
    tid = require_team(db)
    return list(db.execute(select(User).join(Membership, Membership.user_id == User.id)
                           .where(Membership.team_id == tid)).scalars())


def send_one(url: str, d: PushDevice, sealed: str, urgent: bool, ref: str | None) -> str:
    body = {"platform": d.platform, "token": d.token, "sealed": sealed, "urgent": bool(urgent),
            "collapse": collapse(ref), "apns_env": d.apns_env or "production"}
    if d.apns_topic:
        body["apns_topic"] = d.apns_topic
    r = httpx.post(url + "/v1/send", json=body, timeout=15)
    if r.status_code == 200:
        return "sent"
    if r.status_code == 410:
        return "gone"
    try:
        why = r.json().get("error") or r.json().get("detail") or r.text
    except Exception:  # noqa: BLE001
        why = r.text
    return f"failed: {r.status_code} {str(why)[:150]}"


def deliver(db: Session, p: dict) -> str:
    """One alert to every phone that turned notifications on; by email to people who chose email (and to everyone
    with notifications on when the installation has them off or no relay is set). Answers a summary; raises when
    nothing reached anyone."""
    url, m = relay_url(db), mode(db)
    title, body, link = p.get("title") or "", p.get("message") or "", p.get("link")
    msg = message(p.get("kind") or "", title, body, link, p.get("ref"), p.get("alert_id"))
    from .alert_inbox import wants
    kind = p.get("kind") or ""
    users = [u for u in _members(db) if not kind or kind == "test" or wants(u, kind)]
    on = {u.id for u in users if choice(u) == "on"}
    email_to = {u.email for u in users if u.email and choice(u) == "off"}
    sent = gone = failed = 0
    if m == "on" and url:
        for d in db.execute(select(PushDevice).where(PushDevice.user_id.in_(on or {0}))).scalars().all():
            try:
                res = send_one(url, d, seal(d.public_key, msg), p.get("urgent"), p.get("ref"))
            except Exception as e:  # noqa: BLE001
                res = f"failed: {type(e).__name__}"
            if res == "sent":
                sent += 1
                d.last_sent_at, d.last_error = now(), None
            elif res == "gone":
                gone += 1
                db.delete(d)
            else:
                failed += 1
                d.last_error = res[:200]
    else:
        email_to |= {u.email for u in users if u.email and u.id in on}
    mailed = 0
    if email_to:
        from ..tasks import send_mail
        from . import emails
        mail = emails.farm_alert(title, body, link, settings().public_url)
        send_mail(mail.subject, mail.text(), db=db, to=sorted(email_to), html=mail.html())
        mailed = len(email_to)
    if gone:
        event(db, "push", f"Forgot {gone} phone(s) the push service no longer knows")
    out = f"push {sent} sent" + (f", {failed} failed" if failed else "") + (f", {mailed} by email" if mailed else "")
    if failed and not sent and not mailed:
        raise RuntimeError(out)
    return out


def test(db: Session, user: User) -> str:
    url = relay_url(db)
    if mode(db) != "on" or not url:
        raise RuntimeError("phone notifications are off for this installation, or no relay is set")
    devs = db.execute(select(PushDevice).where(PushDevice.user_id == user.id)).scalars().all()
    if not devs:
        raise RuntimeError("no phone has turned notifications on for you yet")
    msg = message("test", "Test notification", "Your hub reaches this phone, end to end encrypted.",
                  settings().public_url.rstrip("/") + "/farm", "push-test")
    out = []
    for d in devs:
        res = send_one(url, d, seal(d.public_key, msg), False, None)
        if res == "gone":
            db.delete(d)
        out.append(f"{d.label or d.platform}: {res}")
    return "; ".join(out)
