"""Printer maintenance: work orders (tasks) by hand, from recurring plans (every N print hours and/or days),
and from printer alarms. Finishing a task takes its spare parts out of InvenTree stock.

Linked with the farm's maintenance: every farm maintenance item is a hub plan (farm_schedule_id; its task,
interval and due status come from the farm, the spare parts are the hub's). Finishing a linked task in the hub
marks the item done in the farm (its counter restarts); other tasks are written to the farm's maintenance log. An
item done in the farm closes the hub's open task. A task can take its printer out of service in the farm (no new
jobs go to it) until it is done or dismissed.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..jobs import enqueue, event
from ..models import FarmJob, MaintPlan, MaintTask

EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def now():
    return datetime.now(timezone.utc)


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def print_hours(db: Session, printer: str, since: datetime | None) -> float:
    """Hours this printer spent printing (jobs that ended) since a moment, from the farm mirror."""
    q = select(FarmJob).where(FarmJob.printer_name == printer, FarmJob.status.in_(("completed", "failed", "cancelled")),
                              FarmJob.started_at.is_not(None), FarmJob.completed_at.is_not(None))
    total = 0.0
    for j in db.execute(q).scalars():
        st, en = _aware(j.started_at), _aware(j.completed_at)
        if since:
            since_a = _aware(since)
            if en <= since_a:
                continue
            st = max(st, since_a)
        if en > st:
            total += (en - st).total_seconds() / 3600
    return total


def clean_parts(parts) -> list:
    out = []
    for p in parts or []:
        try:
            pid, qty = int(p["part_id"]), float(p.get("qty") or 1)
        except (KeyError, TypeError, ValueError):
            raise ValueError("each part needs a part_id and a quantity")
        if qty <= 0:
            raise ValueError("part quantities must be above 0")
        out.append({"part_id": pid, "qty": qty, "name": (p.get("name") or "")[:200]})
    return out


def plan_status(db: Session, p: MaintPlan) -> dict:
    """The farm's own count when it has one, else the hours the hub saw this printer print since last done."""
    if p.farm_schedule_id and p.farm_hours_since is not None:
        hrs = p.farm_hours_since
    else:
        hrs = print_hours(db, p.printer_name, p.last_done_at) if p.every_hours else None
    days = (now() - _aware(p.last_done_at)).total_seconds() / 86400 if _aware(p.last_done_at) > EPOCH else None
    due = (p.every_hours and hrs is not None and hrs >= p.every_hours) or \
        (p.every_days and days is not None and days >= p.every_days) or p.farm_status == "overdue"
    return {"hours_since": round(hrs, 1) if hrs is not None else None,
            "days_since": round(days, 1) if days is not None else None, "due": bool(due)}


def _farm():
    from ..clients import farm
    return farm()


def _can(capability: str) -> bool:
    """Whether the farm can do this (a client without capability info is taken to do everything)."""
    has = getattr(_farm(), "has", None)
    return True if has is None else bool(has(capability))


def _farm_on() -> bool:
    from .connections import is_connected
    from ..clients import farm_connected
    return farm_connected()


def _ts(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def link_farm(db: Session) -> dict:
    """Bring the farm's maintenance items in as linked plans, and close hub tasks done in the farm."""
    rows = _farm().maint_schedules()
    plans = {p.farm_schedule_id: p for p in db.execute(select(MaintPlan).where(MaintPlan.farm_schedule_id.is_not(None))).scalars()}
    seen, added, closed = set(), 0, 0
    for r in rows:
        sid = r["id"]
        seen.add(sid)
        p = plans.get(sid)
        if not p:
            p = MaintPlan(farm_schedule_id=sid, printer_name=r["printer_name"], title=r["task_name"], parts=[],
                          active=bool(r.get("enabled", 1)))
            db.add(p)
            added += 1
        p.printer_name, p.title = r["printer_name"], r["task_name"]
        p.every_hours, p.every_days = r.get("interval_hours"), r.get("interval_days")
        p.notes, p.category, p.farm_status = r.get("description") or p.notes, r.get("category"), r.get("status")
        p.farm_hours_since = r.get("hours_since_last") if r.get("status") != "no_data" else None
        done_at = _ts(r.get("last_completed_at"))
        p.last_done_at = done_at or EPOCH
        if not r.get("enabled", 1):
            p.active = False
        db.flush()
        if done_at:
            for t in db.execute(select(MaintTask).where(MaintTask.plan_id == p.id, MaintTask.state == "open")).scalars():
                if done_at > _aware(t.created_at):
                    t.state, t.done_at, t.done_by = "done", done_at, r.get("last_completed_by") or "farm"
                    t.farm_note = "Done in the farm; spare parts were not taken from InvenTree"
                    _release(db, t)
                    closed += 1
    for sid, p in plans.items():
        if sid not in seen and p.active:
            p.active, p.notes = False, ((p.notes or "") + "\n(removed from the farm)").strip()
    return {"schedules": len(rows), "added": added, "closed_by_farm": closed}


def check_plans(db: Session) -> dict:
    """Open a task for every active plan that is due and has none open."""
    from .alerts import notify
    opened, linked = [], None
    if _farm_on():
        try:
            linked = link_farm(db)
        except Exception as e:  # noqa: BLE001
            linked = {"error": (getattr(e, "message", None) or str(e))[:300]}
    for p in db.execute(select(MaintPlan).where(MaintPlan.active.is_(True))).scalars():
        if db.execute(select(MaintTask).where(MaintTask.plan_id == p.id, MaintTask.state == "open")).first():
            continue
        st = plan_status(db, p)
        if not st["due"]:
            continue
        why = []
        if p.every_hours and st["hours_since"] is not None and st["hours_since"] >= p.every_hours:
            why.append(f"{st['hours_since']:.0f} print hours")
        if p.every_days and st["days_since"] is not None and st["days_since"] >= p.every_days:
            why.append(f"{st['days_since']:.0f} days")
        if not why:
            why.append("The farm says it is overdue")
        t = MaintTask(printer_name=p.printer_name, title=p.title, source="plan", plan_id=p.id, due_at=now(),
                      parts=list(p.parts or []), notes=p.notes, created_by="schedule")
        db.add(t)
        db.flush()
        opened.append(t.id)
        event(db, "farm", f"Maintenance due on {p.printer_name}: {p.title} ({' and '.join(why)} since last time)")
        notify(db, "maintenance_due", f"{p.printer_name}: {p.title}", f"Due after {' and '.join(why)} since last time.",
               link=_link(), ref=f"alert:maint:{t.id}", subject={"task": t.id, "printer": p.printer_name})
    return {"opened": opened, "farm": linked}


def _link():
    from .farm import _link as fl
    return fl("/farm")


def from_alarm(db: Session, printer: str, code: str | None, message: str | None) -> MaintTask | None:
    """A printer alarm opens a task, once per printer and alarm code while it is open."""
    key = (code or (message or "")[:60] or "alarm")[:80]
    if db.execute(select(MaintTask).where(MaintTask.printer_name == printer, MaintTask.state == "open",
                                          MaintTask.alarm_code == key)).first():
        return None
    t = MaintTask(printer_name=printer, title=f"Alarm: {message or code or 'printer alarm'}"[:300], source="alarm",
                  alarm_code=key, due_at=now(), created_by="farm")
    db.add(t)
    db.flush()
    return t


def complete(db: Session, t: MaintTask, actor: str, notes: str | None = None, parts: list | None = None) -> MaintTask:
    from .inventory import remove_part
    if t.state != "open":
        raise ValueError(f"this task is already {t.state}")
    from .alert_inbox import resolve
    resolve(db, f"alert:maint:{t.id}", "maintenance done")
    use = clean_parts(parts) if parts is not None else list(t.parts or [])
    used, errs = [], []
    for p in use:
        try:
            short = remove_part(p["part_id"], p["qty"], f"Maintenance on {t.printer_name}: {t.title} (Artisan Hub, {actor})")
            used.append({**p, "short": short} if short else p)
            if short:
                errs.append(f"{p.get('name') or p['part_id']}: {short:g} not in stock")
        except Exception as e:  # noqa: BLE001
            errs.append(f"{p.get('name') or p['part_id']}: {getattr(e, 'message', None) or e}"[:300])
    t.state, t.done_at, t.done_by = "done", now(), actor
    t.parts_used, t.parts_error = used, "; ".join(errs) or None
    if notes:
        t.notes = ((t.notes + "\n") if t.notes else "") + notes
    p = db.get(MaintPlan, t.plan_id) if t.plan_id else None
    if p:
        p.last_done_at = t.done_at
        p.farm_hours_since, p.farm_status = (0.0, "ok") if p.farm_schedule_id else (p.farm_hours_since, p.farm_status)
    _record_in_farm(t, p, actor, notes)
    _release(db, t)
    event(db, "farm", f"Maintenance done on {t.printer_name}: {t.title}" + (f" (parts: {t.parts_error})" if errs else ""),
          actor=actor)
    if use:
        enqueue(db, "inventory.refresh_parts", ref="refresh:parts", delay=2)
    return t


def task_dict(t: MaintTask) -> dict:
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {"id": t.id, "printer": t.printer_name, "title": t.title, "source": t.source, "plan_id": t.plan_id,
            "state": t.state, "due_at": iso(t.due_at), "parts": t.parts or [], "parts_used": t.parts_used or [],
            "parts_error": t.parts_error, "notes": t.notes, "created_by": t.created_by, "created_at": iso(t.created_at),
            "out_of_service": t.out_of_service, "farm_note": t.farm_note,
            "done_at": iso(t.done_at), "done_by": t.done_by}


def plan_dict(db: Session, p: MaintPlan) -> dict:
    return {"id": p.id, "printer": p.printer_name, "title": p.title, "every_hours": p.every_hours,
            "every_days": p.every_days, "parts": p.parts or [], "notes": p.notes, "active": p.active,
            "last_done_at": p.last_done_at.isoformat() if p.last_done_at and _aware(p.last_done_at) > EPOCH else None,
            "linked": bool(p.farm_schedule_id), "farm_status": p.farm_status, "category": p.category,
            **plan_status(db, p)}


def overview(db: Session) -> dict:
    open_ = list(db.execute(select(MaintTask).where(MaintTask.state == "open").order_by(MaintTask.due_at.asc().nulls_last(),
                                                                                         MaintTask.id)).scalars())
    done = list(db.execute(select(MaintTask).where(MaintTask.state != "open").order_by(MaintTask.done_at.desc().nulls_last())
                           .limit(30)).scalars())
    plans = list(db.execute(select(MaintPlan).order_by(MaintPlan.printer_name, MaintPlan.title)).scalars())
    printers = sorted({p.printer_name for p in plans} | {t.printer_name for t in open_ + done} |
                      {n for (n,) in db.execute(select(FarmJob.printer_name).where(FarmJob.printer_name.is_not(None)).distinct())})
    return {"open": [task_dict(t) for t in open_], "recent": [task_dict(t) for t in done],
            "plans": [plan_dict(db, p) for p in plans],
            "hours": merged_hours(db, printers, {p.printer_name for p in plans}),
            "down": down_printers(), "linked": _farm_on()}


def _farm_printer_names() -> list[str]:
    if not _farm_on():
        return []
    try:
        return [x.get("name") for x in _farm().printers().get("printers", []) if x.get("name")]
    except Exception:  # noqa: BLE001
        return []


def merged_hours(db: Session, names, preferred=()) -> dict:
    """Print hours in the last 30 days per printer, with names that differ only by case (Voron01, voron01) merged
    under one key: the farm's own name for it, else a maintenance plan's, else the first seen."""
    since = now() - timedelta(days=30)
    farm = {n.lower(): n for n in _farm_printer_names()}
    plans = {n.lower(): n for n in preferred if n}
    out: dict = {}
    keys: dict = {}
    for n in sorted(names):
        k = n.lower()
        key = keys.setdefault(k, farm.get(k) or plans.get(k) or n)
        out[key] = out.get(key, 0.0) + print_hours(db, n, since)
    return {k: round(v, 1) for k, v in out.items()}


def _parts_text(t: MaintTask) -> str:
    return ", ".join(f"{x['qty']:g} x {x.get('name') or x['part_id']}" for x in t.parts_used or [])


def _record_in_farm(t: MaintTask, p: MaintPlan | None, actor: str, notes: str | None):
    """Linked plan: mark the farm item done (its counter restarts). Anything else: a farm maintenance log entry."""
    if not _farm_on():
        return
    text = "; ".join(x for x in [notes, f"Parts: {_parts_text(t)}" if t.parts_used else None,
                                 "Recorded by Artisan Hub"] if x)
    try:
        label = getattr(_farm(), "label", "") or "the farm"
        if p and p.farm_schedule_id:
            _farm().maint_complete(p.farm_schedule_id, actor, text)
            t.farm_note = f"Completed in {label}"
        elif not _can("maintenance_log"):
            t.farm_note = f"{label} keeps no free-form maintenance log; recorded in the hub only"
        else:
            cat = "consumable" if t.parts_used else ("calibration" if "calibrat" in t.title.lower() else "inspection")
            _farm().maint_log(t.printer_name, t.title[:200], cat, actor, text)
            t.farm_note = f"Logged in {label}"
    except Exception as e:  # noqa: BLE001
        t.farm_note = f"Not recorded in {_farm_label()}: {(getattr(e, 'message', None) or str(e))[:200]}"


def _farm_label() -> str:
    try:
        return getattr(_farm(), "label", "") or "the farm"
    except Exception:  # noqa: BLE001
        return "the farm"


def set_out_of_service(db: Session, t: MaintTask, down: bool, actor: str = "") -> MaintTask:
    """Mark the printer out of service in the farm (no new jobs) for this task, or bring it back."""
    if down:
        if not _can("out_of_service"):
            from ..clients.farm_base import FarmUnsupported
            raise FarmUnsupported(getattr(_farm(), "kind", "farm"), "out_of_service")
        _farm().set_down(t.printer_name, True, f"{t.title} (Artisan Hub)"[:200])
        t.out_of_service = True
        event(db, "farm", f"{t.printer_name} taken out of service: {t.title}", actor=actor or None)
    else:
        _release(db, t, force=True)
    return t


def _release(db: Session, t: MaintTask, force: bool = False):
    """Bring the printer back when no other open task holds it out of service."""
    if not t.out_of_service:
        return
    t.out_of_service = False
    others = db.execute(select(MaintTask).where(MaintTask.printer_name == t.printer_name, MaintTask.state == "open",
                                                MaintTask.out_of_service.is_(True), MaintTask.id != t.id)).first()
    if others and not force:
        return
    if not _can("out_of_service"):
        return
    try:
        _farm().set_down(t.printer_name, False, "")
        event(db, "farm", f"{t.printer_name} back in service")
    except Exception as e:  # noqa: BLE001
        t.farm_note = ((t.farm_note + "; ") if t.farm_note else "") + f"Couldn't bring {t.printer_name} back in {_farm_label()}: {e}"[:200]


def down_printers() -> list[str]:
    if not _farm_on():
        return []
    try:
        return [d.get("printer_name") for d in _farm().maint_down() if d.get("printer_name")]
    except Exception:  # noqa: BLE001
        return []
