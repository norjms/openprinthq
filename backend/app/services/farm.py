"""Farm edge: a mirror of the farm's job queue, and plate checks after every print.

The farm only sends a job to a printer whose plate is clear, and a printer's plate stops being clear when a print
ends, until someone clears it. The plate check is where that happens in the hub: the person records what came off
the plate (passed, or failed and why, with an optional reprint) and the hub tells the farm the plate is clear.
Nothing is blocked if someone clears the plate in the farm instead; the check is then skipped.

The queue mirror is fed by the farm's webhooks (every queue event) and a periodic sync of its queue, so the hub can
list, search and explain waiting jobs without asking the farm each time.

Staggered start: the hub clears plates one printer at a time, at least `stagger_minutes` apart (counting prints
that started on their own too), so several printers never heat up together after a batch of plate checks.

Orders: with `order_routing` = "hub", a paid WooCommerce order is sent to the farm by the hub (the product's
approved print files), cancelled orders pull their jobs that haven't started, and a private note goes on the Woo
order once every job has printed.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.base import ApiError
from ..config import settings
from ..jobs import enqueue, event
from ..kv import setting, set_setting
from ..models import FarmJob, PlateCheck, PrintRecord, Model, Order, OrderLine, Product

log = logging.getLogger("hub.farm")

ACTIVE = ("queued", "waiting_filament", "assigned", "printing", "paused", "resuming", "held")
DONE = ("completed", "failed", "cancelled")
EVENT_STATUS = {"queue.job_submitted": "queued", "queue.job_assigned": "assigned", "queue.job_printing": "printing",
                "queue.job_completed": "completed", "queue.job_failed": "failed", "queue.job_cancelled": "cancelled"}
FAIL_REASONS = {
    "spaghetti": "Spaghetti or came loose",
    "adhesion": "Lifted or warped",
    "layer_shift": "Layer shift",
    "under_extrusion": "Under-extrusion or gaps",
    "cosmetic": "Cosmetic (stringing, blobs, seams)",
    "wrong_filament": "Wrong colour or material",
    "dimensions": "Wrong size or doesn't fit",
    "other": "Other",
}
# A finished job we only learn about from a sync (no webhook) opens a check only if it ended this recently.
RECENT = timedelta(hours=6)


def now():
    return datetime.now(timezone.utc)


def ts(v) -> datetime | None:
    """Farm timestamps: UTC, with or without a zone."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def gate_on(db: Session) -> bool:
    return bool(setting(db, "plate_checks", True))


def _client():
    from ..clients import farm
    return farm()


# ---------- mirror ----------

def fields_private(j: dict) -> dict:
    md = j.get("metadata") or {}
    st = j.get("status")
    fr = md.get("file_uuid")
    if not fr and str(j.get("file_path") or "").startswith("vfs://database/gcode/"):
        fr = j["file_path"].rsplit("/", 1)[-1]
    reason = None
    if st == "waiting_filament":
        reason = md.get("filament_rejection") or "Waiting for a printer with the right filament"
    elif st == "held":
        reason = "Held in the farm"
    return {"name": j.get("name"), "status": st, "printer_name": j.get("printer_id") or md.get("soft_assign_printer"),
            "priority": j.get("priority"), "quantity": md.get("quantity_required"),
            "source": j.get("source") or md.get("source"), "file_ref": fr,
            "order_refs": [str(o.get("order_id") or o.get("id") or o) if isinstance(o, dict) else str(o)
                           for o in (j.get("linked_orders") or [])],
            "material": j.get("material_type"), "filament_g": md.get("filament_used_g"),
            "fil_plan": _plan(md),
            "est_time_s": md.get("print_time_seconds"), "progress": j.get("progress"),
            "error": j.get("error_message"), "waiting_reason": reason,
            "created_ext": ts(j.get("created_at")), "started_at": ts(j.get("started_at")),
            "completed_at": ts(j.get("completed_at"))}


def _plan(md: dict):
    from .filament_use import plan_from_metadata
    return plan_from_metadata(md)


def fields_public(j: dict) -> dict:
    return {"name": j.get("name"), "status": j.get("status"), "printer_name": j.get("assigned_printer"),
            "priority": j.get("priority"), "progress": j.get("progress"), "error": j.get("error_message"),
            "created_ext": ts(j.get("created_at")), "started_at": ts(j.get("started_at")),
            "completed_at": ts(j.get("completed_at"))}


def _match_model(db: Session, name: str | None, printer: str | None) -> int | None:
    from . import slicing
    try:
        return slicing.match_farm_job(db, name, printer)[1]
    except Exception:  # noqa: BLE001
        return None


def upsert(db: Session, ext_id, fields: dict, last_event: str | None = None) -> tuple[FarmJob, str | None]:
    """Create or update one mirrored job; answers (row, its status before this update)."""
    ext_id = str(ext_id)
    fj = db.execute(select(FarmJob).where(FarmJob.external_id == ext_id)).scalar()
    prev = fj.status if fj else None
    if not fj:
        fj = FarmJob(external_id=ext_id, status=fields.get("status") or "queued")
        db.add(fj)
    for k, v in fields.items():
        if v is None or v == []:
            continue
        if k == "status" and prev in DONE and v in ACTIVE:
            continue          # a late event never reopens a finished job
        setattr(fj, k, v)
    if fj.status != "waiting_filament" and fj.status != "held":
        fj.waiting_reason = None
    if last_event:
        fj.last_event = last_event
    fj.seen_at = now()
    if fj.model_id is None and fj.name:
        fj.model_id = _match_model(db, fj.name, fj.printer_name)
    db.flush()
    return fj, prev


def transition(db: Session, fj: FarmJob, prev: str | None, live: bool):
    """React to a job's status change: open a plate check when a print ends, close stale checks when a printer
    has clearly been set Ready elsewhere (it started another print)."""
    if fj.status == prev:
        return
    if fj.status == "printing" and fj.printer_name:
        since = fj.started_at or now()
        for c in db.execute(select(PlateCheck).where(PlateCheck.printer_name == fj.printer_name,
                                                     PlateCheck.state == "waiting")).scalars():
            if c.external_job_id != fj.external_id and _aware(c.created_at) <= since:
                c.state, c.note = "skipped", "The plate was cleared in the farm and another print started"
                c.checked_at, c.checked_by = now(), "farm"
    if fj.status in ("printing", "paused") and prev is not None and (live or prev in ACTIVE):
        from .alerts import notify
        who = fj.printer_name or "A printer"
        if fj.status == "printing" and prev not in ("paused", "resuming"):
            notify(db, "print_started", f"{who} started {fj.name or 'a print'}", "", link=_link("/farm"),
                   ref=f"alert:job:{fj.external_id}")
        elif fj.status == "paused":
            notify(db, "print_paused", f"{who} paused {fj.name or 'a print'}", fj.waiting_reason or "",
                   link=_link("/farm"), ref=f"alert:job:{fj.external_id}")
    if fj.status == "printing":
        set_setting(db, "farm_last_start", now().isoformat())
        if live or prev is not None:     # which spools it is using, while they are still loaded
            enqueue(db, "filament.snapshot", ref=f"fsnap:{fj.id}", payload={"job_id": fj.id}, delay=5)
    if fj.status == "waiting_filament" and prev is not None and prev != "waiting_filament":
        from .alerts import notify
        unmatched = (fj.waiting_reason or "").startswith("Filament doesn't match")
        notify(db, "waiting_filament",
               f"{'Filament needs mapping' if unmatched else 'Waiting for filament'}: {fj.name}",
               (fj.waiting_reason or "") + (" Tap to choose which loaded filament prints it." if unmatched else ""),
               link=_link(f"/farm/jobs/{fj.external_id}/filament"), ref=f"alert:wf:{fj.external_id}")
    if fj.status in DONE and prev not in DONE:
        fresh = live or prev is not None or (fj.completed_at and now() - fj.completed_at < RECENT)
        started = fj.status != "cancelled" or fj.started_at is not None or prev in ("printing", "paused", "resuming")
        if fresh and started and fj.printer_name:
            c = open_check(db, fj)
            if c:
                enqueue(db, "farm.check_opened", ref=f"check:{c.id}", payload={"check_id": c.id}, delay=2)
        order_progress(db, fj)
        enqueue(db, "filament.measure", ref=f"fmeasure:{fj.id}", payload={"job_id": fj.id}, delay=90)
        if fj.printer_name and fj.status in ("completed", "failed"):
            enqueue(db, "maint.check", ref="maint:check", delay=30)


def _aware(d: datetime | None) -> datetime:
    if d is None:
        return now()
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def open_check(db: Session, fj: FarmJob) -> PlateCheck | None:
    if not gate_on(db):
        return None
    if db.execute(select(PlateCheck).where(PlateCheck.external_job_id == fj.external_id)).first():
        return None
    for c in db.execute(select(PlateCheck).where(PlateCheck.printer_name == fj.printer_name,
                                                 PlateCheck.state == "waiting")).scalars():
        c.state, c.note = "skipped", "A later print ended on this printer, so its plate was cleared in the farm"
        c.checked_at, c.checked_by = now(), "farm"
    c = PlateCheck(printer_name=fj.printer_name, farm_job_id=fj.id, external_job_id=fj.external_id,
                   job_name=fj.name, model_id=fj.model_id, job_outcome=fj.status)
    db.add(c)
    db.flush()
    return c


def ingest_event(db: Session, name: str, data: dict) -> dict:
    """One farm webhook."""
    jid = data.get("job_id")
    st = EVENT_STATUS.get(name)
    if jid is not None and st:
        t = ts(data.get("timestamp")) or now()
        f = {"status": st, "name": data.get("job_name"),
             "printer_name": data.get("printer_name") or data.get("printer"), "progress": data.get("progress")}
        if st == "printing":
            f["started_at"] = t
        if st in DONE:
            f["completed_at"] = t
        if st == "failed":
            f["error"] = data.get("message")
        fj, prev = upsert(db, jid, f, last_event=name)
        transition(db, fj, prev, live=True)
        return {"job": fj.external_id, "status": fj.status, "was": prev}
    if name in ("alarm.raised", "alarm.cleared"):
        from . import printer_status
        who_ = data.get("printer_name") or data.get("printer")
        code_ = data.get("code") or data.get("error_code") or data.get("event_code")
        if name == "alarm.raised":
            printer_status.alarm_raised(db, who_, code_, data.get("message"))
        else:
            printer_status.alarm_cleared(db, who_, code_)
    if name == "alarm.raised" and _quiet_alarm(db, data):
        return {"event": name, "quiet": True}
    if name in ("printer.offline", "alarm.raised", "vision.failure_detected"):
        from .alerts import notify
        who = data.get("printer_name") or data.get("printer") or "a printer"
        what = {"printer.offline": "went offline", "alarm.raised": f"alarm: {data.get('message') or data.get('code') or ''}",
                "vision.failure_detected": f"print failure detected ({round((data.get('confidence') or 0) * 100)}%)"}[name]
        event(db, "farm", f"{who} {what}".strip(), level="warn")
        kind = {"printer.offline": "printer_offline", "alarm.raised": "alarm", "vision.failure_detected": "failure_detected"}[name]
        busy = db.execute(select(FarmJob).where(FarmJob.printer_name == who, FarmJob.status == "printing")).first()
        if kind != "printer_offline" or busy:     # offline only matters mid-print
            notify(db, kind, f"{who} {what}".strip(), data.get("message") or "", link=_link())
        if kind == "alarm" and who != "a printer":
            from .maintenance import from_alarm
            from_alarm(db, who, data.get("code") or data.get("event_code"), data.get("message"))
    return {"event": name}


def _quiet_alarm(db: Session, data: dict) -> bool:
    """A hidden code or a maintenance reminder raises no alert and opens no alarm task (reminders get their own
    Upkeep task when the printer reports them)."""
    from . import printer_status
    who = data.get("printer_name") or data.get("printer")
    code = data.get("code") or data.get("error_code") or data.get("event_code")
    if not printer_status.norm(code):
        return False
    return printer_status.is_hidden(db, who, code) or printer_status.is_maintenance(code)


def _link(path: str = "/farm") -> str:
    return settings().public_url.rstrip("/") + path


def check_opened(db: Session, cid: int) -> dict:
    """A print just ended: take a finish photo from the printer's camera and send the alert with it."""
    from pathlib import Path
    from .alerts import notify
    c = db.get(PlateCheck, cid)
    if not c:
        return {"skipped": "gone"}
    try:
        img = _client().snapshot(c.printer_name)
    except Exception as e:  # noqa: BLE001
        log.info("no snapshot for %s: %s", c.printer_name, e)
        img = None
    if img:
        d = Path(settings().data_dir) / "farm"
        d.mkdir(parents=True, exist_ok=True)
        name = f"check-{c.team_id}-{c.id}.jpg"
        (d / name).write_bytes(img)
        c.photo = name
    m = db.get(Model, c.model_id) if c.model_id else None
    what = f"{m.code} {m.name}" if m else (c.job_name or "a job")
    if c.job_outcome == "completed":
        sent = notify(db, "print_done", f"{c.printer_name} finished {what}", "Clear the plate and pass the check.",
                      link=_link(), photo=c.photo)
    else:
        sent = notify(db, "print_failed", f"{c.printer_name}: print {c.job_outcome}", f"{what}. Check the plate.",
                      link=_link(), photo=c.photo)
    return {"photo": bool(img), "alerted": sent}


def sync(db: Session) -> dict:
    """Pull the farm's queue: every active job, plus the most recent finished ones."""
    w = _client()
    seen, new, changed = set(), 0, 0
    act = w.queue(statuses=list(ACTIVE), limit=500) or {}
    done = w.queue(statuses=list(DONE), limit=50, sort="job_id_desc") or {}
    for j in (act.get("jobs") or []) + (done.get("jobs") or []):
        if j.get("id") is None:
            continue
        fj, prev = upsert(db, j["id"], fields_private(j))
        seen.add(fj.external_id)
        new += prev is None
        changed += prev is not None and prev != fj.status
        transition(db, fj, prev, live=False)
    # active rows the queue no longer lists: finished beyond the history page, or deleted
    gone = 0
    for fj in db.execute(select(FarmJob).where(FarmJob.status.in_(ACTIVE))).scalars().all():
        if fj.external_id in seen:
            continue
        try:
            info = w.job(int(fj.external_id))
        except ApiError as e:
            if e.status == 404:
                fj.status, fj.waiting_reason = "deleted", None
                gone += 1
            continue
        except ValueError:
            continue
        _, prev = upsert(db, fj.external_id, fields_public(info or {}))
        transition(db, fj, prev, live=False)
    return {"jobs": len(seen), "new": new, "changed": changed, "deleted": gone}


# ---------- jobs waiting on a printer that isn't ready ----------

READY_GRACE = 300   # seconds a printer stays not ready, with work waiting for it, before the alert


def ready_alerts(db: Session, at: float | None = None) -> dict:
    """Queued jobs waiting on an idle printer that isn't ready: its plate needs clearing, or it was never set Ready.
    One alert per printer once it has been like that for READY_GRACE; another only after it has been ready again.
    Jobs for no printer in particular count against every printer when none is ready."""
    import time
    from urllib.parse import quote
    from .alerts import notify
    t = time.time() if at is None else at
    jobs = db.execute(select(FarmJob).where(FarmJob.status.in_(("queued", "assigned")))).scalars().all()
    state = dict(setting(db, "farm_ready_alerts") or {})
    if not jobs:
        if state:
            set_setting(db, "farm_ready_alerts", {})
        return {"waiting": 0}
    printers = (_client().printers() or {}).get("printers") or []
    any_ready = any(p.get("ready") for p in printers)
    sent = []
    for p in printers:
        name = p.get("name")
        if not name or not p.get("online") or p.get("busy") or p.get("ready"):
            state.pop(name, None)
            continue
        waiting = [j for j in jobs if j.printer_name == name]
        if not waiting and not any_ready:
            waiting = [j for j in jobs if not j.printer_name]
        if not waiting:
            state.pop(name, None)
            continue
        st = state.setdefault(name, {"since": t, "sent": False})
        if st.get("sent") or t - float(st.get("since") or t) < READY_GRACE:
            continue
        check = db.execute(select(PlateCheck).where(PlateCheck.printer_name == name,
                                                    PlateCheck.state == "waiting")).first()
        why = ("its plate needs clearing. Clear it and pass the plate check." if check
               else "it isn't set Ready. Clear the plate and set it Ready.")
        n = len(waiting)
        first = waiting[0].name or "a job"
        notify(db, "waiting_ready", f"{name}: {n} job{'s' if n != 1 else ''} waiting",
               f"{first}{f' and {n - 1} more' if n > 1 else ''} can't start on {name}: {why}",
               link=_link(f"/farm/printers/{quote(name, safe='')}/ready"), ref=f"alert:ready:{name}")
        st["sent"] = True
        sent.append(name)
    set_setting(db, "farm_ready_alerts", state)
    return {"waiting": len(jobs), "alerted": sent}


# ---------- plate checks ----------

def _set_ready(c: PlateCheck):
    try:
        _client().command(c.printer_name, "set_ready")
        c.ready_set, c.ready_error, c.ready_due = True, None, None
    except Exception as e:  # noqa: BLE001
        msg = getattr(e, "message", None) or str(e)
        c.ready_set, c.ready_error = False, msg[:500]


def stagger(db: Session) -> timedelta:
    try:
        return timedelta(minutes=max(0.0, float(setting(db, "stagger_minutes", 3) or 0)))
    except (TypeError, ValueError):
        return timedelta(minutes=3)


def request_ready(db: Session, c: PlateCheck) -> PlateCheck:
    """Set the printer Ready now, or later when another printer was set Ready or started printing within the
    stagger window (the next free slot after every Ready already planned)."""
    gap = stagger(db)
    if not gap:
        _set_ready(c)
        return c
    t = now()
    marks = [ts(setting(db, "farm_last_start")), ts(setting(db, "farm_last_ready"))]
    marks += [x for x in db.execute(select(PlateCheck.ready_due).where(PlateCheck.ready_due.is_not(None),
                                                                       PlateCheck.id != c.id)).scalars()]
    marks = [_aware(m) for m in marks if m]
    slot = t
    for m in sorted(marks):
        if m + gap > slot and m - gap < slot:
            slot = m + gap
    if slot <= t + timedelta(seconds=5):
        _set_ready(c)
        if c.ready_set:
            set_setting(db, "farm_last_ready", t.isoformat())
        return c
    c.ready_due, c.ready_set, c.ready_error = slot, False, None
    db.flush()
    enqueue(db, "farm.set_ready", ref=f"ready:{c.id}", payload={"check_id": c.id},
            delay=int((slot - t).total_seconds()) + 1)
    return c


def ready_due(db: Session, cid: int) -> dict:
    c = db.get(PlateCheck, cid)
    if not c or c.ready_set or not c.ready_due:
        return {"skipped": True}
    _set_ready(c)
    if c.ready_set:
        set_setting(db, "farm_last_ready", now().isoformat())
    else:
        c.ready_due = None
    return {"ready": c.ready_set, "error": c.ready_error}


def pass_check(db: Session, c: PlateCheck, actor: str, set_ready: bool = True, note: str | None = None) -> PlateCheck:
    if c.state not in ("waiting",):
        raise ValueError(f"this plate check is already {c.state}")
    c.state, c.note, c.checked_by, c.checked_at = "passed", (note or None), actor, now()
    if set_ready:
        request_ready(db, c)
    if c.model_id:
        event(db, f"model:{c.model_id}", f"Plate check passed on {c.printer_name}", actor=actor)
    return c


def fail_check(db: Session, c: PlateCheck, actor: str, reason: str, note: str | None = None,
               reprint: bool = False, set_ready: bool = True) -> PlateCheck:
    if c.state not in ("waiting",):
        raise ValueError(f"this plate check is already {c.state}")
    if reason not in FAIL_REASONS:
        raise ValueError("pick a reason")
    label = FAIL_REASONS[reason]
    c.state, c.reason, c.note, c.checked_by, c.checked_at = "failed", reason, (note or None), actor, now()
    text = f"Plate check: {label}" + (f": {note}" if note else "")
    rec = db.execute(select(PrintRecord).where(PrintRecord.source == "farm",
                                               PrintRecord.external_id == str(c.external_job_id))).scalar()
    if rec:
        rec.outcome = "failed"
        rec.note = (text + (f" ({rec.note})" if rec.note else ""))[:2000]
        if rec.result_id:
            from . import slicing
            from ..models import SliceResult
            r = db.get(SliceResult, rec.result_id)
            if r:
                slicing.refresh_print_problems(db, r)
    elif c.model_id:
        from . import slicing
        r, mid, fid = slicing.match_farm_job(db, c.job_name, c.printer_name)
        slicing.record_print(db, r, mid or c.model_id, "failed", source="farm", external_id=str(c.external_job_id),
                             printer_name=c.printer_name, note=text, actor=actor, file_id=fid)
    if reprint:
        fj = db.get(FarmJob, c.farm_job_id) if c.farm_job_id else None
        if not fj or not fj.file_ref:
            c.ready_error = "Reprint not sent: the farm's file for this job isn't known"
        else:
            try:
                out = _client().submit_job(file_ref=fj.file_ref, quantity=1, job_name=fj.name, priority=fj.priority)
                ids = (out or {}).get("job_ids") or (out or {}).get("jobs") or []
                c.reprint_job_id = str(ids[0] if ids and not isinstance(ids[0], dict) else (ids[0] or {}).get("id")) if ids else "sent"
                if ids:
                    relink_reprint(db, fj, ids[0])
            except Exception as e:  # noqa: BLE001
                c.ready_error = f"Reprint failed: {(getattr(e, 'message', None) or str(e))[:300]}"
    if set_ready:
        err = c.ready_error
        request_ready(db, c)
        if err and not c.ready_error:
            c.ready_error = err
        elif err:
            c.ready_error = f"{err}; {c.ready_error}"
    if c.model_id:
        event(db, f"model:{c.model_id}", f"Plate check failed on {c.printer_name}: {label}" + (f": {note}" if note else ""),
              level="warn", actor=actor)
    return c


def skip_check(db: Session, c: PlateCheck, actor: str) -> PlateCheck:
    if c.state != "waiting":
        raise ValueError(f"this plate check is already {c.state}")
    c.state, c.checked_by, c.checked_at, c.note = "skipped", actor, now(), "Dismissed without a check"
    return c


# ---------- views ----------

def can_pin() -> bool:
    """The farm can send a job to one named printer (Print now)."""
    try:
        return bool(_client().has("pin_printer"))
    except Exception:  # noqa: BLE001
        return False


def print_now(db: Session, model, prof, actor: str, quantity: int = 1, start: bool = False) -> dict:
    """Send the printer's approved slice of a model to that printer now: one farm job per plate file, pinned to it
    (it waits for the printer if it is busy). Raises ValueError for what the person can fix."""
    from . import shop, slicing
    from ..models import ModelFile
    r = slicing.result_for(db, model.id, prof.id, create=False)
    ids = list((r.approved_file_ids if r else None) or [])
    if not ids:
        raise ValueError(f"nothing approved for {prof.name} yet: slice it and approve the result first")
    c = _client()
    if not c.has("pin_printer"):
        raise ValueError("this farm can't send a job to one printer")
    printer = prof.farm_printer or prof.name
    files = [db.get(ModelFile, i) for i in ids]
    if any(f is None for f in files):
        raise ValueError("an approved file is missing from the hub's library: slice it again")
    qty = max(1, min(50, int(quantity or 1)))
    out, started, notes = [], [], []
    for i, f in enumerate(files):
        ref = shop.ensure_farm_file(f)
        name = f"{model.code} {model.name}" + (f" (plate {i + 1} of {len(files)})" if len(files) > 1 else "")
        res = c.submit_job(file_ref=ref, quantity=qty, job_name=name, printer=printer, start=start and i == 0) or {}
        out += [str(x) for x in (res.get("job_ids") or res.get("jobs") or [])]
        started += [str(x) for x in (res.get("started") or [])]
        notes += [n for n in (res.get("notes") or []) if n not in notes]
    event(db, f"model:{model.id}", f"Sent to {prof.name}: {len(out)} job(s)" + (", started" if started else ""), actor=actor)
    from ..jobs import enqueue
    enqueue(db, "farm.sync", ref="farm:sync", delay=5)
    db.flush()
    return {"printer": prof.name, "jobs": out, "files": len(files), "quantity": qty, "started": started, "notes": notes}


def job_dict(fj: FarmJob, models: dict) -> dict:
    m = models.get(fj.model_id)
    return {"id": fj.id, "external_id": fj.external_id, "name": fj.name, "status": fj.status, "printer": fj.printer_name,
            "priority": fj.priority, "quantity": fj.quantity, "source": fj.source, "orders": fj.order_refs or [],
            "material": fj.material, "filament_g": fj.filament_g, "used_g": fj.used_g, "used_source": fj.used_source,
            "est_time_s": fj.est_time_s, "progress": fj.progress,
            "waiting_reason": fj.waiting_reason, "error": fj.error, "plate_reslice": fj.plate_reslice,
            "model": {"id": m.id, "code": m.code, "name": m.name} if m else None,
            "created_at": _iso(fj.created_ext), "started_at": _iso(fj.started_at), "completed_at": _iso(fj.completed_at)}


def check_dict(c: PlateCheck, models: dict) -> dict:
    from ..serialize import hero, img_url
    m = models.get(c.model_id)
    return {"id": c.id, "printer": c.printer_name, "job_id": c.external_job_id, "job_name": c.job_name,
            "job_outcome": c.job_outcome, "state": c.state, "reason": c.reason,
            "reason_label": FAIL_REASONS.get(c.reason) if c.reason else None, "note": c.note,
            "reprint_job_id": c.reprint_job_id, "ready_set": c.ready_set, "ready_error": c.ready_error,
            "ready_due": _iso(c.ready_due), "photo": f"/api/plate-checks/{c.id}/photo" if c.photo else None,
            "checked_by": c.checked_by, "checked_at": _iso(c.checked_at), "created_at": _iso(c.created_at),
            "model": {"id": m.id, "code": m.code, "name": m.name, "thumb": img_url(hero(m))} if m else None}


def _iso(d):
    return d.isoformat() if d else None


def blocked_reasons(jobs: list[FarmJob], printers: list[dict] | None, waiting_checks: list[PlateCheck]) -> dict:
    """Why each waiting job hasn't started."""
    out = {}
    pending = {c.printer_name for c in waiting_checks}
    if printers is not None:
        ready = [p["name"] for p in printers if p.get("online") and p.get("ready") and not p.get("busy")]
        parts = []
        if pending:
            parts.append("plate check waiting on " + ", ".join(sorted(pending)))
        off = [p["name"] for p in printers if not p.get("online")]
        if off:
            parts.append("offline: " + ", ".join(off))
        busy = [p["name"] for p in printers if p.get("busy")]
        if busy:
            parts.append("printing: " + ", ".join(busy))
        idle = [p["name"] for p in printers if p.get("online") and not p.get("busy") and not p.get("ready")
                and p["name"] not in pending]
        if idle:
            parts.append("not set Ready: " + ", ".join(idle))
        no_ready = "No printer is Ready (" + "; ".join(parts) + ")" if not ready else None
    else:
        no_ready = None
    for fj in jobs:
        if fj.status in ("waiting_filament", "held"):
            out[fj.id] = fj.waiting_reason
        elif fj.status == "queued" and no_ready:
            out[fj.id] = no_ready
        elif fj.status == "assigned" and fj.printer_name in pending:
            out[fj.id] = f"{fj.printer_name} is waiting for its plate check"
    return out


def overview(db: Session, live_printers: bool = True) -> dict:
    act = list(db.execute(select(FarmJob).where(FarmJob.status.in_(ACTIVE))
                          .order_by(FarmJob.created_ext.asc().nulls_last(), FarmJob.id)).scalars())
    hist = list(db.execute(select(FarmJob).where(FarmJob.status.in_(DONE + ("deleted",)))
                           .order_by(FarmJob.completed_at.desc().nulls_last(), FarmJob.id.desc()).limit(30)).scalars())
    waiting = list(db.execute(select(PlateCheck).where(PlateCheck.state == "waiting").order_by(PlateCheck.created_at)).scalars())
    done = list(db.execute(select(PlateCheck).where(PlateCheck.state != "waiting")
                           .order_by(PlateCheck.checked_at.desc().nulls_last()).limit(20)).scalars())
    ids = {x.model_id for x in act + hist + waiting + done if x.model_id}
    models = {m.id: m for m in db.execute(select(Model).where(Model.id.in_(ids))).scalars()} if ids else {}
    printers, perr = None, None
    if live_printers:
        try:
            printers = (_client().printers() or {}).get("printers") or []
        except Exception as e:  # noqa: BLE001
            perr = (getattr(e, "message", None) or str(e))[:300]
    from . import printer_status
    printers = printer_status.annotate(db, printers)
    why = blocked_reasons(act, printers, waiting)
    queue = []
    for fj in act:
        d = job_dict(fj, models)
        d["blocked"] = why.get(fj.id)
        queue.append(d)
    synced = db.execute(select(FarmJob.seen_at).order_by(FarmJob.seen_at.desc()).limit(1)).scalar()
    try:
        c = _client()
        caps, label, kind = sorted(c.CAPABILITIES), getattr(c, "label", "") or "", getattr(c, "kind", "") or ""
    except Exception:  # noqa: BLE001
        c, caps, label, kind = None, [], "", ""
    down = {}
    if c is not None and "out_of_service" in caps and printers is not None:
        try:
            down = {d.get("printer_name"): d.get("reason") for d in (c.maint_down() or [])}
        except Exception:  # noqa: BLE001
            down = {}
    keys = ("name", "model", "online", "ready", "busy", "state", "progress", "eta_minutes", "job", "layer",
            "total_layers", "nozzle_temp", "bed_temp", "error")
    return {"plate_checks_on": gate_on(db), "capabilities": caps, "farm_label": label, "farm_kind": kind, "checks": [check_dict(c, models) for c in waiting],
            "recent_checks": [check_dict(c, models) for c in done], "queue": queue,
            "blocked": sum(1 for q in queue if q["blocked"]), "history": [job_dict(f, models) for f in hist],
            "printers": [{**{k: p.get(k) for k in keys}, "out_of_service": p.get("name") in down,
                          "out_of_service_reason": down.get(p.get("name"))} for p in (printers or [])],
            "printers_error": perr, "reasons": FAIL_REASONS,
            "synced_at": _iso(synced)}


# ---------- WooCommerce orders to the farm ----------

NOT_STARTED = ("queued", "waiting_filament", "assigned", "held")


def max_retries(db: Session) -> int:
    """How many times the farm requeues a failed order print on its own (farms with the "retries" capability)."""
    try:
        return max(0, min(10, int(setting(db, "farm_max_retries", 1) or 0)))
    except (TypeError, ValueError):
        return 1


ROUTING = ("hub", "off")   # hub: paid orders are sent to the farm; off: orders are queued by hand


def routing(db: Session) -> str:
    r = setting(db, "order_routing", "off") or "off"
    return r if r in ROUTING else "off"


SHIPPED = ("completed", "shipped", "fulfilled")
GONE = ("cancelled", "refunded")


def order_alert(db: Session, order: Order, prev_status: str | None):
    """New paid orders, shipped orders and cancelled ones, for the phone app (and any channel set for them)."""
    if order.is_test or prev_status == order.status:
        return
    from .alerts import notify
    who = f"Order #{order.number}" + (f" from {order.customer}" if order.customer else "")
    money = f"{order.total:.2f} {order.currency or ''}".strip() if order.total is not None else ""
    if order.status == "processing" and prev_status in (None, "pending", "on-hold"):
        notify(db, "order_new", f"New order #{order.number}", ", ".join(x for x in (order.customer, money) if x),
               link=_link("/orders"), ref=f"alert:order:{order.id}")
    elif order.status in SHIPPED and prev_status not in SHIPPED:
        notify(db, "order_shipped", f"{who} shipped", "", link=_link("/orders"), ref=f"alert:order:{order.id}")
    elif order.status in GONE and prev_status not in GONE and prev_status is not None:
        notify(db, "order_cancelled", f"{who} {order.status}", money, link=_link("/orders"),
               ref=f"alert:order:{order.id}")


def on_order(db: Session, order: Order, prev_status: str | None):
    """Called whenever an order is stored: route it, or pull its jobs when it is cancelled."""
    order_alert(db, order, prev_status)
    if routing(db) != "hub":
        return
    if order.is_test and not setting(db, "route_test_orders", False):
        return
    if order.status == "processing" and any(not ln.farm_job_ids for ln in order.lines):
        enqueue(db, "farm.route_order", ref=f"route:{order.id}", payload={"order_id": order.id}, delay=2)
    elif order.status in ("cancelled", "refunded", "failed") and prev_status != order.status and order.routed_at:
        enqueue(db, "farm.cancel_order", ref=f"route:{order.id}", payload={"order_id": order.id}, delay=2)


def _job_ids(out) -> list:
    ids = (out or {}).get("job_ids") or []
    return [i if not isinstance(i, dict) else i.get("id") for i in ids if i is not None]


def route_order(db: Session, order_id: int, force: bool = False) -> dict:
    """Send an order's lines to the farm. Only paid (processing) orders, unless force: an order created in the hub
    that was asked to print before it is paid."""
    from .shop import print_files
    o = db.get(Order, order_id)
    if not o or (o.status != "processing" and not (force and o.status in ("pending", "on-hold", "processing"))):
        return {"skipped": "not a paid order"}
    w = _client()
    sent, problems = 0, []
    from ..models import Quote
    quoted = {x.order_line_id for x in db.execute(select(Quote).where(Quote.order_id == o.id)).scalars()}
    for ln in o.lines:
        if ln.farm_job_ids or ln.id in quoted:
            continue              # custom prints go to the farm when their quote is approved
        p = db.get(Product, ln.product_id) if ln.product_id else None
        if not p:
            ln.farm_error = f"no product in the hub has SKU {ln.sku}"
            problems.append(f"{ln.name}: {ln.farm_error}")
            continue
        name = f"#{o.number} {ln.sku or p.sku}"
        try:
            from .shop import ensure_farm_file
            files = print_files(p)
            if not files:
                raise ValueError("it has no print-ready file (sliced .3mf or .gcode) marked for production")
            ids = []
            retry = {"max_retries": max_retries(db)} if getattr(w, "has", lambda c: False)("retries") and max_retries(db) else {}
            for f in files:
                # uploads it first when the farm's library doesn't have it yet
                ref = ensure_farm_file(f, w, f"{p.sku}__{f.filename}")
                ids += _job_ids(w.submit_job(file_ref=ref, quantity=ln.qty,
                                             job_name=f"{name} {f.filename}", **retry))
            if ln.variant_id:
                from ..models import Variant
                v = db.get(Variant, ln.variant_id)
                if v:
                    event(db, f"order:{o.id}", f"Order #{o.number}: {p.title} in {v.color_name} was sent with the "
                          "file's own colours; check the filament the farm picks", level="warn")
        except Exception as e:  # noqa: BLE001
            ln.farm_error = (getattr(e, "message", None) or str(e))[:500]
            problems.append(f"{ln.name}: {ln.farm_error}")
            continue
        ln.farm_job_ids, ln.farm_error = ids, None
        for i in ids:
            fj, _ = upsert(db, i, {"name": name, "status": "queued", "order_refs": [str(o.number)], "model_id": p.model_id})
        sent += len(ids)
    if all(ln.farm_job_ids or ln.id in quoted for ln in o.lines) and any(ln.farm_job_ids for ln in o.lines):
        o.routed_at = now()
        if not o.due_at:
            from .schedule import default_due
            o.due_at = default_due(db, o)
        event(db, f"order:{o.id}", f"Order #{o.number} sent to the farm ({sent} print job(s))")
    if problems:
        from .alerts import notify
        event(db, f"order:{o.id}", f"Order #{o.number} not fully sent to the farm: {'; '.join(problems)}"[:900], level="warn")
        notify(db, "order_problem", f"Order #{o.number} couldn't go to the farm", "; ".join(problems)[:900],
               link=_link("/orders"), ref=f"alert:route:{o.id}")
    return {"jobs": sent, "problems": problems}


def cancel_order(db: Session, order_id: int) -> dict:
    o = db.get(Order, order_id)
    if not o:
        return {"skipped": "gone"}
    w = _client()
    cancelled, printing = [], []
    for ln in o.lines:
        for i in ln.farm_job_ids or []:
            fj = db.execute(select(FarmJob).where(FarmJob.external_id == str(i))).scalar()
            st = fj.status if fj else "queued"
            if st in NOT_STARTED:
                try:
                    w.cancel_queued(i, f"Order #{o.number} {o.status}")
                    if fj:
                        fj.status = "cancelled"
                    cancelled.append(i)
                except Exception as e:  # noqa: BLE001
                    event(db, f"order:{o.id}", f"Couldn't cancel job {i}: {getattr(e, 'message', e)}", level="warn")
            elif st in ("printing", "paused", "resuming"):
                printing.append(i)
    if printing:
        event(db, f"order:{o.id}", f"Order #{o.number} is {o.status} but job(s) {printing} are printing; left running",
              level="warn")
    event(db, f"order:{o.id}", f"Order #{o.number} {o.status}: cancelled {len(cancelled)} waiting print job(s)")
    return {"cancelled": cancelled, "still_printing": printing}


def relink_reprint(db: Session, fj: FarmJob, new_id):
    """A reprint replaces the failed job on its order line."""
    for ln in _lines_with(db, fj.external_id):
        ln.farm_job_ids = [new_id if str(x) == fj.external_id else x for x in ln.farm_job_ids]
        upsert(db, new_id, {"name": fj.name, "status": "queued", "order_refs": fj.order_refs, "model_id": fj.model_id})


def _lines_with(db: Session, ext_id: str) -> list[OrderLine]:
    out = []
    for o in db.execute(select(Order).where(Order.routed_at.is_not(None), Order.printed_at.is_(None))).scalars():
        for ln in o.lines:
            if any(str(x) == str(ext_id) for x in ln.farm_job_ids or []):
                out.append(ln)
    return out


def order_progress(db: Session, fj: FarmJob):
    """When a job ends: if it was the last one of its order, note it on the order in its store."""
    for ln in _lines_with(db, fj.external_id):
        o = ln.order
        ids = [str(x) for line in o.lines for x in (line.farm_job_ids or [])]
        rows = {r.external_id: r.status for r in db.execute(select(FarmJob).where(FarmJob.external_id.in_(ids))).scalars()}
        if ids and all(rows.get(i) == "completed" for i in ids) and not o.printed_at:
            o.printed_at = now()
            event(db, f"order:{o.id}", f"Order #{o.number}: every item printed")
            enqueue(db, "farm.order_printed", ref=f"printed:{o.id}", payload={"order_id": o.id})


def order_printed(db: Session, order_id: int) -> dict:
    from .alerts import notify
    from .stores import order_note
    o = db.get(Order, order_id)
    if not o:
        return {"skipped": "gone"}
    noted = order_note(db, o, "Artisan Hub: every item on this order has printed and is ready to pack.")
    notify(db, "order_printed", f"Order #{o.number} is printed", "Ready to pack.", link=_link("/orders"))
    return {"noted": noted}


def order_print_state(db: Session, o: Order) -> dict:
    ids = [str(x) for ln in o.lines for x in (ln.farm_job_ids or [])]
    rows = {r.external_id: r.status for r in db.execute(select(FarmJob).where(FarmJob.external_id.in_(ids))).scalars()} if ids else {}
    errs = [ln.farm_error for ln in o.lines if ln.farm_error]
    counts = {}
    for i in ids:
        counts[rows.get(i, "queued")] = counts.get(rows.get(i, "queued"), 0) + 1
    return {"jobs": len(ids), "by_status": counts, "errors": errs, "routed_at": _iso(o.routed_at),
            "printed_at": _iso(o.printed_at)}
