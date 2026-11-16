"""Farm timeline: when each queued job should print, per printer, and whether each order ships on time.

A simple forward simulation of the farm's queue: printers free up when their current print ends (the farm's
ETA) plus a turnaround for clearing the plate; waiting jobs are taken highest priority first, oldest first, each
on the printer it is assigned to, else the earliest free printer that has an approved slice of the model (any
printer when the hub can't tell). It is an estimate: the farm makes the real choice, and its filament matching
can hold a job back. Jobs waiting for filament or held aren't placed.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..kv import setting
from ..models import FarmJob, Order, PlateCheck, PrinterProfile, SliceResult
from . import farm

DEFAULT_JOB = timedelta(hours=2)      # a job with no time estimate
HORIZON_CAP = timedelta(days=14)


def now():
    return datetime.now(timezone.utc)


def turnaround(db) -> timedelta:
    try:
        return timedelta(minutes=float(setting(db, "turnaround_minutes", 10) or 0))
    except (TypeError, ValueError):
        return timedelta(minutes=10)


def ship_days(db) -> float:
    try:
        return float(setting(db, "ship_within_days", 3) or 3)
    except (TypeError, ValueError):
        return 3.0


def default_due(db, o: Order) -> datetime | None:
    base = farm._aware(o.placed_at) if o.placed_at else None
    return base + timedelta(days=ship_days(db)) if base else None


def _printers_for_models(db: Session, model_ids: set) -> dict:
    """model id -> names of hub printers with an approved slice (the hub printer name is the farm's name)."""
    if not model_ids:
        return {}
    names = {p.id: p.name for p in db.execute(select(PrinterProfile)).scalars()}
    out = {}
    for r in db.execute(select(SliceResult).where(SliceResult.model_id.in_(model_ids))).scalars():
        if r.approved_file_ids:
            out.setdefault(r.model_id, set()).add(names.get(r.profile_id))
    return out


def build(db: Session, printers: list[dict] | None = None, at: datetime | None = None, down: list | None = None) -> dict:
    t0 = at or now()
    ta = turnaround(db)
    if printers is None:
        try:
            printers = (farm._client().printers() or {}).get("printers") or []
        except Exception:  # noqa: BLE001
            printers = []
    jobs = list(db.execute(select(FarmJob).where(FarmJob.status.in_(farm.ACTIVE))).scalars())
    waiting_checks = {c.printer_name for c in db.execute(select(PlateCheck).where(PlateCheck.state == "waiting")).scalars()}
    if down is None:
        from .maintenance import down_printers
        down = down_printers()
    free, lanes, online = {}, {}, set()
    for p in printers:
        name = p["name"]
        lanes[name] = {"printer": name, "online": bool(p.get("online")), "state": p.get("state"), "bars": [],
                       "down": name in down}
        if name in down:
            lanes[name]["state"] = "out of service"
        if not p.get("online") or name in down:
            continue
        online.add(name)
        f = t0
        if p.get("busy"):
            f = t0 + timedelta(minutes=float(p.get("eta_minutes") or 0)) + ta
        elif name in waiting_checks or not p.get("ready"):
            f = t0 + ta
        free[name] = f
    unscheduled = []

    def bar(fj, start, end, printer, est=True):
        lanes.setdefault(printer, {"printer": printer, "online": printer in online, "state": None, "bars": [],
                                   "down": printer in down})
        lanes[printer]["bars"].append({"job": fj.external_id, "name": fj.name, "status": fj.status,
                                       "orders": fj.order_refs or [], "model_id": fj.model_id,
                                       "start": start.isoformat(), "end": end.isoformat(), "estimated": est})

    ends = {}
    for fj in [j for j in jobs if j.status in ("printing", "paused", "resuming")]:
        p = fj.printer_name
        st = farm._aware(fj.started_at) if fj.started_at else t0
        live = next((x for x in printers if x["name"] == p), {})
        if live.get("eta_minutes") is not None:
            en = t0 + timedelta(minutes=float(live["eta_minutes"]))
        else:
            dur = timedelta(seconds=fj.est_time_s) if fj.est_time_s else DEFAULT_JOB
            en = max(t0, st + dur)
        if p:
            bar(fj, st, en, p)
            free[p] = max(free.get(p, t0), en + ta) if p in online else free.get(p, t0)
        ends[fj.external_id] = en
    fits = _printers_for_models(db, {j.model_id for j in jobs if j.model_id})
    todo = [j for j in jobs if j.status in ("queued", "assigned")]
    todo.sort(key=lambda j: (-(j.priority or 50), farm._aware(j.created_ext) if j.created_ext else t0, j.id))
    for fj in todo:
        dur = timedelta(seconds=fj.est_time_s) if fj.est_time_s else DEFAULT_JOB
        if fj.printer_name in free:
            cands = [fj.printer_name]
        elif fj.printer_name and fj.status == "assigned":
            cands = []            # assigned to a printer that is offline: it waits for that printer
        else:
            cands = [p for p in free if not fits.get(fj.model_id) or p in fits[fj.model_id]]
        if not cands:
            unscheduled.append({"job": fj.external_id, "name": fj.name, "orders": fj.order_refs or [],
                                "why": (f"{fj.printer_name} is out of service" if fj.printer_name in down else
                                        f"{fj.printer_name} is offline") if fj.printer_name else "no online printer can print it"})
            continue
        p = min(cands, key=lambda x: free[x])
        st = free[p]
        en = st + dur
        free[p] = en + ta
        bar(fj, st, en, p, est=bool(fj.est_time_s))
        ends[fj.external_id] = en
    for fj in jobs:
        if fj.status in ("waiting_filament", "held"):
            unscheduled.append({"job": fj.external_id, "name": fj.name, "orders": fj.order_refs or [],
                                "why": fj.waiting_reason or fj.status.replace("_", " ")})
    orders = []
    for o in db.execute(select(Order).where(Order.routed_at.is_not(None), Order.printed_at.is_(None),
                                            Order.status.in_(("processing", "on-hold")))).scalars():
        ids = [str(x) for ln in o.lines for x in (ln.farm_job_ids or [])]
        done = {r.external_id for r in db.execute(select(FarmJob).where(FarmJob.external_id.in_(ids),
                                                                        FarmJob.status == "completed")).scalars()}
        left = [i for i in ids if i not in done]
        due = farm._aware(o.due_at) if o.due_at else default_due(db, o)
        stuck = [i for i in left if i not in ends]
        finish = max((ends[i] for i in left if i in ends), default=None)
        if stuck:
            state = "unknown"
        elif not due or not finish:
            state = "on_time"
        elif finish > due:
            state = "late"
        elif finish > due - timedelta(hours=4):
            state = "at_risk"
        else:
            state = "on_time"
        orders.append({"id": o.id, "number": o.number, "customer": o.customer, "due_at": due.isoformat() if due else None,
                       "due_set": o.due_at is not None, "projected": finish.isoformat() if finish else None,
                       "state": state, "jobs": len(ids), "jobs_left": len(left), "stuck": len(stuck)})
    orders.sort(key=lambda x: x["due_at"] or "9999")
    all_ends = [farm._aware(datetime.fromisoformat(b["end"])) for l in lanes.values() for b in l["bars"]]
    horizon = min(max(all_ends + [t0 + timedelta(hours=12)]), t0 + HORIZON_CAP)
    return {"now": t0.isoformat(), "until": horizon.isoformat(), "turnaround_minutes": ta.total_seconds() / 60,
            "lanes": sorted(lanes.values(), key=lambda l: l["printer"]), "unscheduled": unscheduled, "orders": orders}


def check_late(db: Session) -> dict:
    """Alert once per order when the timeline says it will miss (or nearly miss) its date."""
    from .alerts import notify
    tl = build(db)
    sent = []
    for o in tl["orders"]:
        if o["state"] not in ("late", "at_risk"):
            continue
        row = db.get(Order, o["id"])
        if row.late_alerted_at:
            continue
        row.late_alerted_at = now()
        when = datetime.fromisoformat(o["projected"]).strftime("%a %b %d %H:%M UTC") if o["projected"] else "?"
        notify(db, "order_late", f"Order #{o['number']} may ship late",
               f"Projected to finish printing {when}; due {datetime.fromisoformat(o['due_at']).strftime('%a %b %d')}. "
               "Prioritise it on the Farm timeline.", link=farm._link("/farm"), ref=f"alert:late:{row.id}",
               subject={"order": row.id})
        sent.append(o["number"])
    return {"alerted": sent, "orders": len(tl["orders"])}


def prioritise(db: Session, order_id: int, priority: int = 75) -> dict:
    o = db.get(Order, order_id)
    if not o:
        raise ValueError("no such order")
    w = farm._client()
    done, errs = [], []
    for ln in o.lines:
        for i in ln.farm_job_ids or []:
            fj = db.execute(select(FarmJob).where(FarmJob.external_id == str(i))).scalar()
            if fj and fj.status not in ("queued", "assigned", "waiting_filament", "held"):
                continue
            try:
                w.set_priority(i, priority)
                if fj:
                    fj.priority = priority
                done.append(i)
            except Exception as e:  # noqa: BLE001
                errs.append(f"job {i}: {getattr(e, 'message', None) or e}")
    return {"raised": done, "errors": errs}
