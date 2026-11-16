"""Farm: the farm's queue as the hub mirrors it, and plate checks after each print."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..auth import need, Principal
from ..db import get_db
from ..services import printer_access
from ..kv import set_setting
from ..models import FarmJob, PlateCheck
from ..services import farm
from ..services.connections import NotConnected

router = APIRouter()


def _actor(p: Principal) -> str:
    return p.get("username") or p.get("email") or "someone"


@router.get("/api/farm")
def overview(live: bool = True, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    out = farm.overview(db, live_printers=live)
    out["printers"] = printer_access.visible(db, p, out.get("printers"))
    return out


class HideCodeIn(BaseModel):
    code: str
    printer: str | None = None


@router.get("/api/farm/hidden-errors")
def hidden_errors(db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    from ..services import printer_status
    return {"hidden": printer_status.hidden_list(db)}


@router.post("/api/farm/hidden-errors")
def hide_error(body: HideCodeIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from ..services import printer_status
    try:
        row = printer_status.hide(db, body.code, body.printer, _actor(p))
    except ValueError:
        raise HTTPException(400, "no code")
    db.commit()
    return {"code": printer_status.norm(body.code), **row}


@router.delete("/api/farm/hidden-errors/{code}")
def unhide_error(code: str, db: Session = Depends(get_db), _: Principal = Depends(need("farm"))):
    from ..services import printer_status
    gone = printer_status.unhide(db, code)
    db.commit()
    return {"removed": gone}


@router.get("/api/farm/jobs")
def jobs(q: str | None = None, status: str | None = None, printer: str | None = None, limit: int = 100,
         db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    """Search the mirrored queue (active and finished jobs)."""
    from ..models import Model
    s = select(FarmJob)
    if q:
        s = s.where(FarmJob.name.ilike(f"%{q}%"))
    if status:
        s = s.where(FarmJob.status.in_([x.strip() for x in status.split(",") if x.strip()]))
    if printer:
        s = s.where(FarmJob.printer_name == printer)
    rows = list(db.execute(s.order_by(FarmJob.id.desc()).limit(max(1, min(limit, 500)))).scalars())
    ids = {r.model_id for r in rows if r.model_id}
    models = {m.id: m for m in db.execute(select(Model).where(Model.id.in_(ids))).scalars()} if ids else {}
    return {"jobs": [farm.job_dict(r, models) for r in rows]}


@router.post("/api/farm/sync")
def sync_now(db: Session = Depends(get_db), _: Principal = Depends(need("farm"))):
    try:
        out = farm.sync(db)
    except NotConnected:
        raise HTTPException(400, "ArtisanHub Farm isn't connected")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"ArtisanHub Farm: {(getattr(e, 'message', None) or str(e))[:300]}")
    db.commit()
    return out


@router.get("/api/farm/jobs/{job_id}/preflight")
def job_preflight(job_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    """Why a queued job isn't printing, from the farm itself, printer by printer."""
    fj = db.get(FarmJob, job_id)
    if not fj:
        raise HTTPException(404, "no such job")
    w = farm._client()
    if not w.has("preflight"):
        raise HTTPException(400, "this farm can't explain its queue")
    try:
        return w.preflight(fj.external_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"farm: {(getattr(e, 'message', None) or str(e))[:300]}")


@router.get("/api/farm/stats")
def queue_stats(days: int = 30, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    """The farm's own queue statistics (success rate, wait and print times, retries, per printer)."""
    w = farm._client()
    if not w.has("queue_stats"):
        raise HTTPException(400, "this farm has no queue statistics")
    try:
        return w.queue_stats(days)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"farm: {(getattr(e, 'message', None) or str(e))[:300]}")


class FarmSettings(BaseModel):
    plate_checks: bool | None = None
    stagger_minutes: float | None = None
    order_routing: str | None = None
    route_test_orders: bool | None = None
    alert_rules: dict | None = None
    ship_within_days: float | None = None
    turnaround_minutes: float | None = None
    max_retries: int | None = None


def _settings_view(db) -> dict:
    from ..kv import setting
    from ..services import alerts, schedule
    return {"plate_checks": farm.gate_on(db), "stagger_minutes": float(setting(db, "stagger_minutes", 3) or 0),
            "order_routing": farm.routing(db), "route_test_orders": bool(setting(db, "route_test_orders", False)),
            "alert_rules": alerts.rules(db), "alert_events": {k: v[0] for k, v in alerts.EVENTS.items()},
            "urgent_events": [k for k, v in alerts.EVENTS.items() if v[1]],
            "channels_configured": alerts.configured(), "channels": list(alerts.CHANNELS),
            "ship_within_days": schedule.ship_days(db), "turnaround_minutes": schedule.turnaround(db).total_seconds() / 60,
            "max_retries": farm.max_retries(db)}


@router.get("/api/farm/settings")
def farm_settings_get(db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    return _settings_view(db)


@router.put("/api/farm/settings")
def farm_settings(body: FarmSettings, db: Session = Depends(get_db), p: Principal = Depends(need("settings"))):
    from ..jobs import event
    from ..services import alerts
    if body.plate_checks is not None:
        set_setting(db, "plate_checks", body.plate_checks)
    if body.stagger_minutes is not None:
        if not 0 <= body.stagger_minutes <= 60:
            raise HTTPException(400, "stagger is 0 to 60 minutes")
        set_setting(db, "stagger_minutes", body.stagger_minutes)
    if body.ship_within_days is not None:
        if not 0 < body.ship_within_days <= 60:
            raise HTTPException(400, "ship within 1 to 60 days")
        set_setting(db, "ship_within_days", body.ship_within_days)
    if body.turnaround_minutes is not None:
        if not 0 <= body.turnaround_minutes <= 240:
            raise HTTPException(400, "turnaround is 0 to 240 minutes")
        set_setting(db, "turnaround_minutes", body.turnaround_minutes)
    if body.max_retries is not None:
        if not 0 <= body.max_retries <= 10:
            raise HTTPException(400, "retries are 0 to 10")
        set_setting(db, "farm_max_retries", body.max_retries)
    if body.route_test_orders is not None:
        set_setting(db, "route_test_orders", body.route_test_orders)
    if body.order_routing is not None:
        if body.order_routing not in farm.ROUTING:
            raise HTTPException(400, "order routing is hub or off")
        if body.order_routing != farm.routing(db):
            set_setting(db, "order_routing", body.order_routing)
            event(db, "team", f"Order routing set to {body.order_routing}", actor=_actor(p))
    if body.alert_rules is not None:
        try:
            set_setting(db, "alert_rules", {**(alerts.rules(db)), **alerts.clean_rules(body.alert_rules)})
        except ValueError as e:
            raise HTTPException(400, str(e))
    db.commit()
    return _settings_view(db)


@router.post("/api/farm/alerts/test")
def alerts_test(db: Session = Depends(get_db), _: Principal = Depends(need("farm"))):
    from ..services import alerts
    ok, msg = alerts.send_test()
    return {"ok": ok, "message": msg}


@router.get("/api/plate-checks/{cid}/photo")
def check_photo(cid: int, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    from fastapi.responses import FileResponse
    from ..services.alerts import photo_path
    c = _check(db, cid)
    p = photo_path(c.photo)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


class PassIn(BaseModel):
    set_ready: bool = True
    note: str | None = None


class FailIn(BaseModel):
    reason: str
    note: str | None = None
    reprint: bool = False
    set_ready: bool = True


def _check(db, cid) -> PlateCheck:
    c = db.get(PlateCheck, cid)
    if not c:
        raise HTTPException(404)
    return c


@router.post("/api/plate-checks/{cid}/pass")
def check_pass(cid: int, body: PassIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    c = _check(db, cid)
    try:
        farm.pass_check(db, c, _actor(p), set_ready=body.set_ready, note=body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return farm.check_dict(c, {})


@router.post("/api/plate-checks/{cid}/fail")
def check_fail(cid: int, body: FailIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    c = _check(db, cid)
    try:
        farm.fail_check(db, c, _actor(p), body.reason, note=body.note, reprint=body.reprint, set_ready=body.set_ready)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return farm.check_dict(c, {})


@router.post("/api/plate-checks/{cid}/skip")
def check_skip(cid: int, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    c = _check(db, cid)
    try:
        farm.skip_check(db, c, _actor(p))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return farm.check_dict(c, {})


@router.post("/api/plate-checks/{cid}/retry-ready")
def check_retry_ready(cid: int, db: Session = Depends(get_db), _: Principal = Depends(need("farm"))):
    """Setting Ready failed (printer offline, busy): try again."""
    c = _check(db, cid)
    if c.state == "waiting":
        raise HTTPException(400, "pass or fail the plate first")
    farm.request_ready(db, c)
    db.commit()
    return farm.check_dict(c, {})


def waiting_count(db: Session) -> int:
    return db.execute(select(func.count(PlateCheck.id)).where(PlateCheck.state == "waiting")).scalar() or 0


# ---------- timeline and order dates ----------

@router.get("/api/farm/timeline")
def timeline(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from ..services import schedule
    out = schedule.build(db)
    out["lanes"] = printer_access.visible_items(db, p, out.get("lanes"), id_field="printer_id", name_fields=("printer",))
    return out


class DueIn(BaseModel):
    due_at: str | None = None      # ISO date or datetime; empty = back to the default


@router.put("/api/orders/{order_id}/due")
def order_due(order_id: int, body: DueIn, db: Session = Depends(get_db), _: Principal = Depends(need("farm"))):
    from datetime import datetime, timezone
    from ..models import Order
    from ..services import schedule
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404)
    if body.due_at:
        try:
            d = datetime.fromisoformat(body.due_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "due date must be YYYY-MM-DD or a full date and time")
        if len(body.due_at) <= 10:
            d = d.replace(hour=17)
        o.due_at = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    else:
        o.due_at = schedule.default_due(db, o)
    o.late_alerted_at = None
    db.commit()
    return {"due_at": o.due_at.isoformat() if o.due_at else None}


class PrioIn(BaseModel):
    priority: int = 75


@router.post("/api/orders/{order_id}/prioritise")
def order_prioritise(order_id: int, body: PrioIn, db: Session = Depends(get_db), _: Principal = Depends(need("farm"))):
    from ..services import schedule
    if body.priority not in (25, 50, 75, 100):
        raise HTTPException(400, "priority is 25, 50, 75 or 100")
    try:
        out = schedule.prioritise(db, order_id, body.priority)
    except ValueError as e:
        raise HTTPException(404, str(e))
    db.commit()
    return out


# ---------- maintenance ----------

@router.get("/api/maintenance")
def maint_overview(db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    from ..services import maintenance
    return maintenance.overview(db)


class TaskIn(BaseModel):
    printer: str
    title: str
    parts: list[dict] = []
    notes: str | None = None
    due_at: str | None = None
    out_of_service: bool = False


@router.post("/api/maintenance/tasks")
def maint_task_new(body: TaskIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from datetime import datetime, timezone
    from ..models import MaintTask
    from ..services import maintenance
    if not body.printer.strip() or not body.title.strip():
        raise HTTPException(400, "printer and title are needed")
    try:
        parts = maintenance.clean_parts(body.parts)
        due = datetime.fromisoformat(body.due_at).replace(tzinfo=timezone.utc) if body.due_at else maintenance.now()
    except ValueError as e:
        raise HTTPException(400, str(e))
    t = MaintTask(printer_name=body.printer.strip(), title=body.title.strip()[:300], parts=parts, notes=body.notes,
                  due_at=due, created_by=_actor(p))
    db.add(t)
    db.flush()
    if body.out_of_service:
        try:
            maintenance.set_out_of_service(db, t, True, _actor(p))
        except Exception as e:  # noqa: BLE001
            t.farm_note = f"Couldn't take {t.printer_name} out of service: {(getattr(e, 'message', None) or str(e))[:200]}"
    db.commit()
    return maintenance.task_dict(t)


class ServiceIn(BaseModel):
    out_of_service: bool


@router.post("/api/maintenance/tasks/{tid}/service")
def maint_task_service(tid: int, body: ServiceIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """Take the task's printer out of service in the farm (no new jobs), or bring it back."""
    from ..models import MaintTask
    from ..services import maintenance
    t = db.get(MaintTask, tid)
    if not t:
        raise HTTPException(404)
    if t.state != "open" and body.out_of_service:
        raise HTTPException(400, "the task is closed")
    try:
        maintenance.set_out_of_service(db, t, body.out_of_service, _actor(p))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Farm: {(getattr(e, 'message', None) or str(e))[:300]}")
    db.commit()
    return maintenance.task_dict(t)


@router.post("/api/maintenance/sync")
def maint_sync(db: Session = Depends(get_db), _: Principal = Depends(need("farm"))):
    """Pull the farm's maintenance items now and open any tasks that are due."""
    from ..services import maintenance
    out = maintenance.check_plans(db)
    db.commit()
    return out


class DoneIn(BaseModel):
    notes: str | None = None
    parts: list[dict] | None = None


@router.post("/api/maintenance/tasks/{tid}/done")
def maint_task_done(tid: int, body: DoneIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from ..models import MaintTask
    from ..services import maintenance
    t = db.get(MaintTask, tid)
    if not t:
        raise HTTPException(404)
    try:
        maintenance.complete(db, t, _actor(p), notes=body.notes, parts=body.parts)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return maintenance.task_dict(t)


@router.post("/api/maintenance/tasks/{tid}/dismiss")
def maint_task_dismiss(tid: int, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from ..models import MaintTask
    from ..services import maintenance
    t = db.get(MaintTask, tid)
    if not t:
        raise HTTPException(404)
    if t.state != "open":
        raise HTTPException(400, f"this task is already {t.state}")
    t.state, t.done_at, t.done_by = "dismissed", maintenance.now(), _actor(p)
    maintenance._release(db, t)
    from ..services.alert_inbox import resolve
    resolve(db, f"alert:maint:{t.id}", "task dismissed")
    db.commit()
    return maintenance.task_dict(t)


class PlanIn(BaseModel):
    printer: str
    title: str
    every_hours: float | None = None
    every_days: float | None = None
    parts: list[dict] = []
    notes: str | None = None
    active: bool = True
    last_done_at: str | None = None


def _plan_fields(body: PlanIn) -> dict:
    from datetime import datetime, timezone
    from ..services import maintenance
    if not body.every_hours and not body.every_days:
        raise HTTPException(400, "give print hours, days, or both")
    if (body.every_hours is not None and body.every_hours <= 0) or (body.every_days is not None and body.every_days <= 0):
        raise HTTPException(400, "intervals must be above 0")
    try:
        parts = maintenance.clean_parts(body.parts)
        last = datetime.fromisoformat(body.last_done_at).replace(tzinfo=timezone.utc) if body.last_done_at else None
    except ValueError as e:
        raise HTTPException(400, str(e))
    out = {"printer_name": body.printer.strip(), "title": body.title.strip()[:200], "every_hours": body.every_hours,
           "every_days": body.every_days, "parts": parts, "notes": body.notes, "active": body.active}
    if last:
        out["last_done_at"] = last
    return out


@router.post("/api/maintenance/plans")
def maint_plan_new(body: PlanIn, db: Session = Depends(get_db), _: Principal = Depends(need("farm"))):
    from ..models import MaintPlan
    from ..services import maintenance
    p = MaintPlan(**_plan_fields(body))
    db.add(p)
    db.flush()
    maintenance.check_plans(db)
    db.commit()
    return maintenance.plan_dict(db, p)


@router.put("/api/maintenance/plans/{pid}")
def maint_plan_edit(pid: int, body: PlanIn, db: Session = Depends(get_db), _: Principal = Depends(need("farm"))):
    from ..models import MaintPlan
    from ..services import maintenance
    p = db.get(MaintPlan, pid)
    if not p:
        raise HTTPException(404)
    if p.farm_schedule_id:
        # linked to the farm: the task and its interval are the farm's; the hub keeps parts, notes and on/off
        try:
            p.parts = maintenance.clean_parts(body.parts)
        except ValueError as e:
            raise HTTPException(400, str(e))
        p.notes, p.active = body.notes, body.active
    else:
        for k, v in _plan_fields(body).items():
            setattr(p, k, v)
    db.commit()
    return maintenance.plan_dict(db, p)


@router.delete("/api/maintenance/plans/{pid}")
def maint_plan_delete(pid: int, db: Session = Depends(get_db), _: Principal = Depends(need("farm"))):
    """Removes the plan only; its past tasks stay in the history."""
    from ..models import MaintPlan
    p = db.get(MaintPlan, pid)
    if not p:
        raise HTTPException(404)
    if p.farm_schedule_id:
        raise HTTPException(400, "this plan comes from the farm's maintenance: turn it off here or remove it there")
    db.delete(p)
    db.commit()
    return {"ok": True}


# ---------- reports ----------

@router.get("/api/reports/farm")
def report_farm(days: int = 30, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    from ..services import reports
    return reports.farm(db, max(1, min(days, 366)))


@router.get("/api/reports/filament")
def report_filament(days: int = 30, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    """Filament really used vs estimated: by source (printer, weighed, progress, estimate) and recent weigh-ins."""
    from ..services import filament_use
    return filament_use.summary(db, max(1, min(days, 366)))


class MeasureIn(BaseModel):
    days: int = 3


@router.post("/api/farm/filament/measure")
def filament_measure(body: MeasureIn, db: Session = Depends(get_db), _: Principal = Depends(need("farm"))):
    """Measure finished jobs again now (printer history first), for the last `days` days."""
    from ..services import filament_use
    out = filament_use.measure_recent(db, max(1, min(body.days, 120)))
    db.commit()
    return out


class MoonrakerIn(BaseModel):
    urls: dict[str, str]


@router.get("/api/farm/filament/printers")
def filament_printers(db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    """Printers whose own history gives real filament use (found Klipper printers plus overrides)."""
    from ..kv import setting
    from ..services import filament_use
    return {"printers": filament_use.klipper_hosts(db), "overrides": setting(db, "moonraker_urls") or {}}


@router.put("/api/farm/filament/printers")
def filament_printers_set(body: MoonrakerIn, db: Session = Depends(get_db), _: Principal = Depends(need("admin"))):
    """Add or override a printer's Moonraker URL ("" turns one off); keyed by the printer's name in the farm."""
    from ..services import filament_use
    urls = {k.strip(): v.strip() for k, v in body.urls.items() if k.strip()}
    for v in urls.values():
        if v and not v.startswith(("http://", "https://")):
            raise HTTPException(400, "Moonraker URLs start with http:// or https://")
    set_setting(db, "moonraker_urls", urls)
    db.commit()
    return {"printers": filament_use.klipper_hosts(db), "overrides": urls}


@router.get("/api/reports/profit")
def report_profit(days: int = 30, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    from ..services import reports
    return reports.profit(db, max(1, min(days, 366)))


@router.get("/api/reports/{kind}.csv")
def report_csv(kind: str, days: int = 30, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    from fastapi.responses import Response
    from ..services import reports
    if kind not in reports.CSV:
        raise HTTPException(404)
    days = max(1, min(days, 366))
    return Response(reports.csv_text(db, kind, days), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="artisan-{kind}-{days}d.csv"'})


# ---------- printer and queue controls on the Farm page (the same verbs as the mobile API) ----------

class FarmCommandIn(BaseModel):
    command: str
    args: dict | None = None
    confirm: bool = False


@router.post("/api/farm/printers/{name}/command")
def farm_printer_command(name: str, body: FarmCommandIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """Clear the plate, pause, resume, cancel, light, home, preheat, cool down: whatever the farm supports."""
    printer_access.require(db, p, name)
    from .mobile import PRINTER_COMMANDS
    from ..jobs import event
    if body.command not in PRINTER_COMMANDS:
        raise HTTPException(400, f"command must be one of {', '.join(sorted(PRINTER_COMMANDS))}")
    try:
        out = farm._client().command(name, body.command, body.args, confirm=body.confirm)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, (getattr(e, "message", None) or str(e))[:300])
    event(db, "farm", f"{name}: {body.command} from the Farm page", actor=_actor(p))
    db.commit()
    return {"ok": True, "result": out}


class FarmServiceIn(BaseModel):
    down: bool
    reason: str | None = None


@router.post("/api/farm/printers/{name}/service")
def farm_printer_service(name: str, body: FarmServiceIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """Out of service (no new jobs; a running print carries on) or back in service."""
    printer_access.require(db, p, name)
    from ..jobs import event
    try:
        farm._client().set_down(name, body.down, (body.reason or "").strip() or f"Set from the Farm page by {_actor(p)}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, (getattr(e, "message", None) or str(e))[:300])
    event(db, "farm", f"{name}: {'out of service' if body.down else 'back in service'} from the Farm page", actor=_actor(p))
    db.commit()
    return {"ok": True}


class FarmPriorityIn(BaseModel):
    priority: int


@router.post("/api/farm/jobs/{job_id}/priority")
def farm_job_priority(job_id: str, body: FarmPriorityIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from ..jobs import event
    if not 1 <= body.priority <= 100:
        raise HTTPException(400, "priority is 1 to 100 (25 low, 50 normal, 75 high, 100 urgent)")
    try:
        farm._client().set_priority(job_id, body.priority)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, (getattr(e, "message", None) or str(e))[:300])
    fj = db.execute(select(FarmJob).where(FarmJob.external_id == str(job_id))).scalar()
    if fj:
        fj.priority = body.priority
    event(db, "farm", f"Job {job_id} priority {body.priority} from the Farm page", actor=_actor(p))
    db.commit()
    return {"ok": True}


@router.post("/api/farm/jobs/{job_id}/cancel")
def farm_job_cancel(job_id: str, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """A job that hasn't started leaves the queue; a printing one is stopped."""
    from ..jobs import event
    fj = db.execute(select(FarmJob).where(FarmJob.external_id == str(job_id))).scalar()
    try:
        if fj and fj.status in ("printing", "paused", "resuming"):
            farm._client().cancel_job(int(job_id))
        else:
            farm._client().cancel_queued(job_id, f"Cancelled from the Farm page by {_actor(p)}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, (getattr(e, "message", None) or str(e))[:300])
    event(db, "farm", f"Job {job_id} cancelled from the Farm page", actor=_actor(p))
    db.commit()
    return {"ok": True}


# ---- alarm feed (OpenPrintHQ engines from 2026-11-07; capability "alarms") ----

def _alarm_call(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, (getattr(e, "message", None) or str(e))[:300])


@router.get("/api/farm/alarms")
def farm_alarms(state: str = "active", printer: str | None = None, unacknowledged: bool = False,
                db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """What every printer is reporting: active alarms worst first, or history (state=cleared|all)."""
    if state not in ("active", "cleared", "all"):
        raise HTTPException(400, "state is active, cleared or all")
    printer_access.require(db, p, printer)
    return {"alarms": printer_access.visible_items(db, p, _alarm_call(
        lambda: farm._client().alarms(state=state, printer=printer, unacknowledged=unacknowledged)))}


@router.get("/api/farm/alarms/summary")
def farm_alarm_summary(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    return printer_access.alarm_summary(db, p, _alarm_call(lambda: farm._client().alarm_summary()))


@router.post("/api/farm/alarms/{alarm_id}/ack")
def farm_alarm_ack(alarm_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from ..jobs import event
    out = _alarm_call(lambda: farm._client().ack_alarm(alarm_id, _actor(p)))
    event(db, "farm", f"Alarm {alarm_id} acknowledged from the Farm page", actor=_actor(p))
    db.commit()
    return {"ok": True, **(out or {})}


@router.post("/api/farm/printers/{name}/alarms/ack")
def farm_printer_alarms_ack(name: str, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    printer_access.require(db, p, name)
    from ..jobs import event
    out = _alarm_call(lambda: farm._client().ack_printer_alarms(name, _actor(p)))
    event(db, "farm", f"{name}: alarms acknowledged from the Farm page", actor=_actor(p))
    db.commit()
    return {"ok": True, **(out or {})}
