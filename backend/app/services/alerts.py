"""Farm alerts: email, the phone app (push, end to end encrypted: services/push.py) and other phone channels
(ntfy, Telegram, Slack), event rules and quiet hours.

Channel credentials live in the team's "Phone alerts" connection (encrypted). Which events go to which channel,
and the quiet hours, are the team setting `alert_rules`. During quiet hours only urgent events go out (or none,
or everything, per `quiet_mode`). Sending happens in the `alerts.send` job so a slow channel never holds up a
webhook or a plate check.
"""
import logging
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from ..config import settings
from ..jobs import enqueue, event
from ..kv import setting

log = logging.getLogger("hub.alerts")

CHANNELS = ("push", "email", "ntfy", "telegram", "slack")
# event: (label, urgent)
EVENTS = {
    "print_done": ("Print finished, plate waiting", False),
    "print_failed": ("Print failed", True),
    "failure_detected": ("Camera saw a failure", True),
    "alarm": ("Printer alarm", True),
    "printer_offline": ("Printer went offline", False),
    "waiting_filament": ("Job waiting for filament", False),
    "waiting_ready": ("Job waiting for a printer to be ready (plate to clear)", False),
    "order_printed": ("Order fully printed", False),
    "order_problem": ("Order couldn't go to the farm", True),
    "order_late": ("Order may miss its ship date", False),
    "maintenance_due": ("Maintenance due", False),
    "quote_ordered": ("Custom print ordered, needs review", False),
    "print_started": ("Print started", False),
    "print_paused": ("Print paused", False),
    "order_new": ("New paid order", False),
    "order_shipped": ("Order shipped", False),
    "order_cancelled": ("Order cancelled or refunded", False),
}
# Updates rather than problems: only to the phone app by default.
APP_ONLY = ("print_started", "print_paused", "order_new", "order_shipped", "order_cancelled")
PHONE = ["ntfy", "telegram", "slack"]
DEFAULT_RULES = {
    "events": {k: ["push"] + ([] if k in APP_ONLY else PHONE + (["email"] if urgent or k == "order_problem" else []))
               for k, (_, urgent) in EVENTS.items()},
    "quiet_start": "22:00", "quiet_end": "07:00", "timezone": "America/Chicago",
    "quiet_mode": "urgent",       # urgent: only urgent events; none: nothing; off: no quiet hours
}


def rules(db) -> dict:
    r = dict(DEFAULT_RULES)
    saved = setting(db, "alert_rules") or {}
    r.update({k: v for k, v in saved.items() if k in DEFAULT_RULES and k != "events"})
    ev = {k: list(v) for k, v in DEFAULT_RULES["events"].items()}
    known = saved.get("channels_known") or []
    for k, v in (saved.get("events") or {}).items():
        if k in EVENTS:
            ev[k] = [c for c in v if c in CHANNELS]
            if "push" not in known and "push" not in ev[k]:     # saved before the app channel existed: on
                ev[k].insert(0, "push")
    r["events"] = ev
    return r


def clean_rules(body: dict) -> dict:
    out = {}
    for k in ("quiet_start", "quiet_end"):
        if k in body:
            v = str(body[k] or "").strip()
            try:
                dtime.fromisoformat(v)
            except ValueError:
                raise ValueError(f"{k.replace('_', ' ')} must look like 22:00")
            out[k] = v[:5]
    if "timezone" in body:
        try:
            ZoneInfo(body["timezone"])
        except Exception:  # noqa: BLE001
            raise ValueError("unknown time zone")
        out["timezone"] = body["timezone"]
    if "quiet_mode" in body:
        if body["quiet_mode"] not in ("urgent", "none", "off"):
            raise ValueError("quiet mode is urgent, none or off")
        out["quiet_mode"] = body["quiet_mode"]
    if "events" in body:
        out["events"] = {k: [c for c in (v or []) if c in CHANNELS] for k, v in (body["events"] or {}).items() if k in EVENTS}
        out["channels_known"] = list(CHANNELS)
    return out


def quiet_now(r: dict, at: datetime | None = None) -> bool:
    if r.get("quiet_mode") == "off":
        return False
    tz = ZoneInfo(r.get("timezone") or "UTC")
    t = (at or datetime.now(timezone.utc)).astimezone(tz).time()
    a, b = dtime.fromisoformat(r["quiet_start"]), dtime.fromisoformat(r["quiet_end"])
    return (a <= t < b) if a < b else (t >= a or t < b)


def _conn():
    from .connections import get
    return get("alerts", required=False) or {}


def configured() -> list[str]:
    c = _conn()
    # The app channel is always there: services/push.py sends to whoever turned it on, or emails them instead.
    out = ["push"] + (["email"] if settings().smtp_host else [])
    if c.get("ntfy_url"):
        out.append("ntfy")
    if c.get("telegram_bot_token") and c.get("telegram_chat_id"):
        out.append("telegram")
    if c.get("slack_webhook_url"):
        out.append("slack")
    return out


def notify(db, kind: str, title: str, message: str, link: str | None = None, photo: str | None = None,
           ref: str | None = None, subject: dict | None = None) -> list[str]:
    """Record an alert in the inbox (services/alert_inbox.py) and queue it to its channels. Answers the channels it
    will go to (empty: muted, quiet hours or nothing configured); the inbox gets it either way."""
    if kind not in EVENTS:
        raise ValueError(kind)
    from . import alert_inbox
    rec = alert_inbox.record(db, kind, title, message, link, ref, subject, EVENTS[kind][1])
    link = settings().public_url.rstrip("/") + f"/alerts/{rec.id}"
    r = rules(db)
    chans = [c for c in r["events"].get(kind, []) if c in configured()]
    urgent = EVENTS[kind][1]
    if chans and quiet_now(r) and (r["quiet_mode"] == "none" or (r["quiet_mode"] == "urgent" and not urgent)):
        event(db, "farm", f"Quiet hours, not sent: {title}")
        return []
    if not chans:
        return []
    enqueue(db, "alerts.send", ref=ref, payload={"kind": kind, "title": title, "message": message, "link": link,
                                                 "photo": photo, "channels": chans, "urgent": urgent, "ref": ref,
                                                 "alert_id": rec.id},
            max_attempts=2)
    return chans


def photo_path(name: str | None) -> Path | None:
    if not name:
        return None
    p = Path(settings().data_dir) / "farm" / Path(name).name
    return p if p.exists() else None


def deliver(db, p: dict) -> dict:
    c = _conn()
    title, msg, link = p.get("title") or "", p.get("message") or "", p.get("link")
    pic = photo_path(p.get("photo"))
    data = pic.read_bytes() if pic else None
    out = {}
    for ch in p.get("channels") or []:
        try:
            if ch == "push":
                from .push import deliver as push_deliver
                out[ch] = push_deliver(db, p)
                continue
            if ch == "email":
                from ..config import settings
                from ..tasks import send_mail
                from . import emails
                mail = emails.farm_alert(title, msg, link, settings().public_url)
                send_mail(mail.subject, mail.text(), db=db, html=mail.html())
            elif ch == "ntfy":
                _ntfy(c, title, msg, link, data, p.get("urgent"))
            elif ch == "telegram":
                _telegram(c, title, msg, link, data)
            elif ch == "slack":
                _slack(c, title, msg, link)
            out[ch] = "sent"
        except Exception as e:  # noqa: BLE001
            out[ch] = f"failed: {str(e)[:200]}"
            log.warning("alert via %s failed: %s", ch, e)
    bad = {k: v for k, v in out.items() if v != "sent" and k != "push" or (k == "push" and v.startswith("failed"))}
    if bad:
        event(db, "farm", f"Alert '{title}' not delivered: {bad}", level="warn")
    return out


def _ntfy(c, title, msg, link, data, urgent):
    h = {"Title": _ascii(title), "Priority": "high" if urgent else "default", "Tags": "printer"}
    if c.get("ntfy_token"):
        h["Authorization"] = f"Bearer {c['ntfy_token']}"
    if link:
        h["Click"] = link
    url = c["ntfy_url"]
    if data:
        h["Filename"] = "photo.jpg"
        h["Message"] = _ascii(msg)
        r = httpx.put(url, content=data, headers=h, timeout=30)
    else:
        r = httpx.post(url, content=msg.encode(), headers=h, timeout=30)
    r.raise_for_status()


def _ascii(s: str) -> str:
    return (s or "").encode("ascii", "replace").decode()[:900]


def _telegram(c, title, msg, link, data):
    base = f"https://api.telegram.org/bot{c['telegram_bot_token']}"
    text = f"{title}\n{msg}" + (f"\n{link}" if link else "")
    if data:
        r = httpx.post(f"{base}/sendPhoto", data={"chat_id": c["telegram_chat_id"], "caption": text[:1000]},
                       files={"photo": ("photo.jpg", data, "image/jpeg")}, timeout=30)
    else:
        r = httpx.post(f"{base}/sendMessage", json={"chat_id": c["telegram_chat_id"], "text": text[:4000]}, timeout=30)
    r.raise_for_status()


def _slack(c, title, msg, link):
    text = f"*{title}*\n{msg}" + (f"\n<{link}|Open in Artisan Hub>" if link else "")
    r = httpx.post(c["slack_webhook_url"], json={"text": text}, timeout=30)
    r.raise_for_status()


def send_test() -> tuple[bool, str]:
    from ..db import SessionLocal
    chans = [c for c in configured() if c not in ("email", "push")]
    if not chans:
        return False, "no phone channel is filled in"
    db = SessionLocal()
    try:
        out = deliver(db, {"title": "Artisan Hub test alert", "message": "Farm alerts reach this channel.",
                           "channels": chans, "link": settings().public_url.rstrip("/") + "/farm"})
        db.commit()
    finally:
        db.close()
    ok = all(v == "sent" for v in out.values())
    return ok, ", ".join(f"{k}: {v}" for k, v in out.items())
