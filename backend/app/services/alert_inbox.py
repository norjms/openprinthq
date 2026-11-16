"""The alerts inbox: every alert the hub raises is kept here, so a person can open it from a notification, an
email or the dashboard, then fix it or dismiss it. One record per problem (`ref`): a repeat updates it rather
than adding another. Problems that go away on their own close their alert (the job started, the printer became
ready). Updates that need nothing from anyone close after a day; anything left open closes after two weeks.

Each person also chooses which kinds reach them (User.prefs["alerts_off"]): the inbox still lists everything, but
their phone and their email only get the kinds they kept.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Alert, User

# How the choices are grouped on the "What I get" screens. Every alert kind is in exactly one group.
GROUPS = [
    ("problems", "Problems", ["alarm", "print_failed", "failure_detected", "printer_offline", "order_problem"]),
    ("needs_you", "Needs you", ["waiting_filament", "waiting_ready", "print_done", "maintenance_due", "quote_ordered",
                                "order_late"]),
    ("prints", "Print updates", ["print_started", "print_paused"]),
    ("orders", "Orders", ["order_new", "order_printed", "order_shipped", "order_cancelled"]),
]
INFO = {"print_started", "print_paused", "order_new", "order_printed", "order_shipped", "order_cancelled"}
# Alerts with a guided fix in the app and on the web; the others offer "Go to it". Filament and ready have their
# own screens in the clients; "guided" ones are described by the hub (services/alert_fixes.py).
FIXES = {"waiting_filament": "filament", "waiting_ready": "ready",
         **{k: "guided" for k in ("print_done", "print_failed", "failure_detected", "alarm", "printer_offline",
                                  "maintenance_due", "order_problem", "order_late", "quote_ordered")}}
INFO_TTL = timedelta(days=1)
OPEN_TTL = timedelta(days=14)


def now():
    return datetime.now(timezone.utc)


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def record(db: Session, kind: str, title: str, message: str, link: str | None, ref: str | None,
           subject: dict | None, urgent: bool) -> Alert:
    a = None
    if ref:
        a = db.execute(select(Alert).where(Alert.ref == ref, Alert.state == "open")
                       .order_by(Alert.id.desc())).scalars().first()
    t = now()
    if a:
        a.title, a.message, a.link, a.urgent = title, message, link, urgent
        a.count = (a.count or 1) + 1
        a.updated_at = t
        if subject:
            a.subject = {**(a.subject or {}), **subject}
    else:
        a = Alert(kind=kind, ref=ref, title=title, message=message, link=link, subject=subject or {}, urgent=urgent,
                  state="open", count=1, created_at=t, updated_at=t)
        db.add(a)
    db.flush()
    return a


def close(db: Session, a: Alert, state: str, actor: str | None = None, how: str | None = None):
    if a.state != "open":
        return
    a.state, a.closed_at, a.closed_by, a.fix = state, now(), actor, (how or "")[:300] or None
    a.updated_at = a.closed_at


def resolve(db: Session, ref: str, how: str):
    """The problem behind `ref` has gone away: close its open alert."""
    for a in db.execute(select(Alert).where(Alert.ref == ref, Alert.state == "open")).scalars():
        close(db, a, "resolved", "hub", how)


def resolve_prefix(db: Session, prefix: str, how: str):
    """Close every open alert whose ref starts with `prefix` (all of a printer's alarms, say)."""
    for a in db.execute(select(Alert).where(Alert.ref.startswith(prefix), Alert.state == "open")).scalars():
        close(db, a, "resolved", "hub", how)


def expire(db: Session):
    t = now()
    for a in db.execute(select(Alert).where(Alert.state == "open")).scalars():
        age = t - _aware(a.updated_at or a.created_at)
        if (a.kind in INFO and age > INFO_TTL) or age > OPEN_TTL:
            close(db, a, "resolved", "hub", "closed after a while with nothing to do")


def view(a: Alert, full: bool = False) -> dict:
    iso = lambda d: _aware(d).isoformat() if d else None  # noqa: E731
    out = {"id": a.id, "kind": a.kind, "title": a.title, "message": a.message, "state": a.state,
           "urgent": bool(a.urgent), "count": a.count or 1, "created_at": iso(a.created_at),
           "updated_at": iso(a.updated_at), "closed_at": iso(a.closed_at), "closed_by": a.closed_by, "fix": a.fix,
           "fixable": FIXES.get(a.kind), "subject": a.subject or {}}
    if full:
        out["link"] = a.link
    return out


def counts(db: Session) -> dict:
    rows = db.execute(select(Alert.urgent).where(Alert.state == "open")).scalars().all()
    return {"open": len(rows), "urgent": sum(1 for u in rows if u)}


# ---------------------------------------------------------------- each person's choices

def kinds() -> list[str]:
    return [k for _, _, ks in GROUPS for k in ks]


def off_for(user: User | None) -> set[str]:
    return set(((user.prefs or {}).get("alerts_off") or []) if user else [])


def wants(user: User | None, kind: str) -> bool:
    return kind not in off_for(user)


def choices(user: User | None) -> dict:
    from .alerts import EVENTS
    off = off_for(user)
    return {"groups": [{"id": gid, "label": label,
                        "kinds": [{"kind": k, "label": EVENTS[k][0], "urgent": EVENTS[k][1], "on": k not in off}
                                  for k in ks if k in EVENTS]} for gid, label, ks in GROUPS]}


def set_choices(user: User, on: dict):
    known = set(kinds())
    off = off_for(user)
    for k, v in (on or {}).items():
        if k not in known:
            continue
        if v:
            off.discard(k)
        else:
            off.add(k)
    prefs = dict(user.prefs or {})
    prefs["alerts_off"] = sorted(off)
    user.prefs = prefs
