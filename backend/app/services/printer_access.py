"""Which of the farm's printers each person may see and send to.

One installation, one team, one set of printers everyone shares. The owner may narrow that per person on People:
all printers (the default, also for every new person) or a chosen list. Owners, the hub owner and keys that belong
to no person (the installer's, a service's) always have all of them.

The hub keeps the list (Membership.printers: None for all, else printer keys) and applies it wherever it shows or
acts on a printer: a printer outside someone's list is left out of every listing and answers 404 when asked for by
name. It also hands the list to the farm (push), which applies it in its own pages and its print host, so
OrcaSlicer only offers a person their own printers.

A printer's key is the farm's printer id when the farm has one, else its name.
"""
import hashlib
import json
import logging
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Membership, User

log = logging.getLogger("hub.printer_access")
LAST_PUSH = "farm.printer_access_pushed"     # setting: digest of the list the farm last took
_cache: dict = {"at": 0.0, "printers": []}
TTL = 30.0


def key(p: dict) -> str:
    v = p.get("id")
    return str(v) if v not in (None, "") else str(p.get("name") or "")


def clean(value) -> list[str] | None:
    """What a Membership keeps: None for all printers, else a sorted list of distinct printer keys."""
    if value is None or value == "all":
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError("printers must be \"all\" or a list of printer ids")
    out = sorted({str(x).strip()[:120] for x in value if str(x).strip()})
    return out


def _membership(db: Session, p) -> Membership | None:
    uid, tid = p.get("user_id"), p.get("team_id")
    if not uid or not tid:
        return None
    return db.execute(select(Membership).where(Membership.team_id == tid, Membership.user_id == uid)).scalar()


def allowed(db: Session, p) -> set[str] | None:
    """The printer keys this caller may use, or None for every printer."""
    if p is None or p.get("platform_admin"):
        return None
    if "allowed_printers" in p:                 # worked out once per request
        v = p["allowed_printers"]
        return None if v is None else set(v)
    m = _membership(db, p)
    v = None if (m is None or m.role == "owner" or m.printers is None) else list(m.printers)
    p["allowed_printers"] = v
    return None if v is None else set(v)


def _matches(pr: dict, keys: set[str]) -> bool:
    return key(pr) in keys or str(pr.get("name") or "") in keys


def visible(db: Session, p, printers):
    """The printers of a listing this caller may see, in the same order."""
    keys = allowed(db, p)
    if keys is None or printers is None:
        return printers
    return [x for x in printers if isinstance(x, dict) and _matches(x, keys)]


def farm_printers() -> list[dict]:
    """The farm's printers (id and name), cached briefly: used to tell a printer named in a request apart."""
    now = time.monotonic()
    if now - _cache["at"] < TTL and _cache["printers"]:
        return _cache["printers"]
    from ..clients import farm
    raw = (farm().printers() or {}).get("printers") or []
    _cache.update(at=now, printers=[{"id": x.get("id"), "name": x.get("name")} for x in raw if isinstance(x, dict)])
    return _cache["printers"]


def may_use(db: Session, p, name: str | None) -> bool:
    keys = allowed(db, p)
    if keys is None or not name:
        return True
    if name in keys:
        return True
    want = name.strip().lower()
    try:
        prs = farm_printers()
    except Exception:  # noqa: BLE001  (farm unreachable: only a key or name already in the list passes)
        return False
    return any(str(x.get("name") or "").strip().lower() == want and _matches(x, keys) for x in prs)


def require(db: Session, p, name: str | None):
    """404 for a printer outside the caller's list: it does not exist for them."""
    if not may_use(db, p, name):
        raise HTTPException(404, "printer not found")


# ---------- handing the list to the farm ----------

def payload(db: Session, team_id: int | None = None) -> dict:
    q = select(Membership, User).join(User, User.id == Membership.user_id)
    if team_id:
        q = q.where(Membership.team_id == team_id)
    people, owners = {}, set()
    for m, u in db.execute(q).all():
        email = (u.email or "").strip().lower()
        if not email:
            continue
        if m.role == "owner" or u.platform_admin:
            owners.add(email)
            people[email] = "all"
        elif email not in owners:
            # the farm's printer ids are its engine's, positive integers; a name-only key has no place there
            people[email] = "all" if m.printers is None else sorted({int(x) for x in m.printers if str(x).isdigit() and int(x) > 0})
    return {"version": 1, "people": dict(sorted(people.items())), "owners": sorted(owners)}


def push(db: Session, team_id: int | None = None, force: bool = False) -> dict:
    """Send the list to the farm when it changed since it last took it. An older farm without the endpoint
    answers 404: logged and left at that."""
    from ..clients import farm, farm_kind
    from ..clients.base import ApiError
    from ..kv import set_setting, setting
    try:
        farm_kind(team_id)
    except Exception:  # noqa: BLE001
        return {"skipped": "no farm chosen"}
    body = payload(db, team_id)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    if not force and setting(db, LAST_PUSH) == digest:
        return {"unchanged": True}
    try:
        farm().push_printer_access(body)
    except ApiError as e:
        if getattr(e, "status", None) == 404:
            log.info("The farm has no printer access endpoint yet (an older farm)")
            return {"skipped": "older farm"}
        raise
    set_setting(db, LAST_PUSH, digest)
    db.commit()
    return {"pushed": len(body["people"])}


def push_soon(db: Session):
    """After a change: the worker sends it right away (and the periodic job retries if the farm is away)."""
    from ..jobs import enqueue
    enqueue(db, "farm.push_printer_access", "printer-access")


def visible_items(db: Session, p, items, id_field: str = "printer_id", name_fields=("printer_name", "printer")):
    """Entries that each name one printer (alarms, timeline lanes, plate rows): those of the caller's printers.
    An entry that names no printer stays."""
    keys = allowed(db, p)
    if keys is None or not items:
        return items
    out = []
    for x in items:
        pid = x.get(id_field) if isinstance(x, dict) else None
        name = next((x.get(f) for f in name_fields if isinstance(x, dict) and x.get(f)), None)
        if pid is None and not name or (pid is not None and str(pid) in keys) or (name and may_use(db, p, str(name))):
            out.append(x)
    return out


def alarm_summary(db: Session, p, out: dict) -> dict:
    """The alarm badge's printers, kept to the caller's own (the counts stay the farm's)."""
    keys = allowed(db, p)
    if keys is None or not isinstance(out, dict):
        return out
    ids, names = out.get("printers") or [], out.get("printer_names") or []
    keep = [i for i, pid in enumerate(ids) if str(pid) in keys or (i < len(names) and may_use(db, p, names[i]))]
    return {**out, "printers": [ids[i] for i in keep], "printer_names": [names[i] for i in keep if i < len(names)]}
