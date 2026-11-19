"""Mobile API (v1): one place for phone apps to run the whole stack through the hub.

Authentication: a team API key (Authorization: Bearer ahk_...), made under /api/mobile/v1/keys with
- scopes: read / write / admin (a role's permissions), any single permission by name (farm, store, inventory, ...),
  relay:<app> or relay:* (raw access to a connected app, admin keys only) and owner (the hub owner's provisioning);
- an optional expiry (expires_in_days);
- an optional app instance: instance_id ties the key to one install, bind_instance ties it to the first install
  that uses it. The app sends X-Hub-Instance on every call.

Curated endpoints cover what is done from a phone: farm status, printers and cameras, the queue, plate checks,
spools, parts, orders, products (live toggle), maintenance, quotes, the hub's event log and push notifications.
Each one checks the caller's permissions and the existing hub logic does the work, so the phone behaves like the
web app. The relay forwards a request to a connected app's own API with the team's stored credentials; every
change made through it is written to the event log. Owner endpoints let the hub owner provision teams, people
and team keys.
"""
import json
import re
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal, new_api_key, need
from ..db import get_db
from ..services import printer_access
from ..jobs import event
from ..models import (PERMS, ApiKey, Event, FarmJob, Membership, Order, PartCache, PlateCheck, Product, Quote,
                      SpoolCache, Team, User)

router = APIRouter(prefix="/api/mobile/v1", tags=["mobile"])
API_VERSION = 1
RELAY_APPS = ("openprinthq", "spoolman", "inventree", "woo", "dam", "manyfold", "moonraker")
COARSE = ("read", "write", "admin")
RELAY_MAX = 20 * 1024 * 1024


def now():
    return datetime.now(timezone.utc)


def _iso(d):
    if d is None:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()


def _actor(p: Principal) -> str:
    return p.get("username") or p.get("email") or "api"


def _err(e: Exception, code: int = 502):
    msg = getattr(e, "message", None) or str(e)
    raise HTTPException(code, msg[:500])


# ---------------------------------------------------------------- who am I

@router.get("/me")
def me(request: Request, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """The caller, its team, permissions and key details, and which apps the team has connected."""
    from ..services.connections import KINDS, is_connected
    key = db.get(ApiKey, p["key_id"]) if p.get("key_id") else None
    return {"api": API_VERSION, "actor": _actor(p), "kind": p.get("kind"), "owner": bool(p.get("platform_admin")),
            "team": {"id": p.team_id, "slug": p.get("team_slug"), "name": p.get("team_name")},
            "perms": p.get("perms") or [],
            "key": {"id": key.id, "name": key.name, "scopes": key.scopes, "expires_at": _iso(key.expires_at),
                    "instance_bound": bool(key.instance_id)} if key else None,
            "connected": {k: is_connected(k, p.team_id) for k in KINDS if k not in ("slicer",)},
            "relay": _relay_allowed_apps(p), "farm": _farm_info()}


def _farm_info() -> dict | None:
    """Which farm app the team runs and what it can do, so the phone shows only what works."""
    from ..clients import farm, farm_connected
    try:
        return {**farm().describe(), "connected": farm_connected()}
    except Exception:  # noqa: BLE001
        return None


@router.get("/overview")
def overview(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """The phone's home screen: one call, counts and the farm at a glance."""
    from ..services import farm
    active = list(db.execute(select(FarmJob).where(FarmJob.status.in_(farm.ACTIVE))).scalars())
    out = {"api": API_VERSION, "team": p.get("team_name"), "time": now().isoformat(),
           "queue": {s: sum(1 for j in active if j.status == s) for s in sorted({j.status for j in active})},
           "plate_checks_waiting": db.scalar(select(func.count()).select_from(PlateCheck).where(PlateCheck.state == "waiting")) or 0,
           "orders_open": db.scalar(select(func.count()).select_from(Order).where(Order.status.in_(("processing", "on-hold")))) or 0,
           "quotes_waiting": db.scalar(select(func.count()).select_from(Quote).where(Quote.status == "ordered")) or 0,
           "products_live": db.scalar(select(func.count()).select_from(Product).where(Product.live.is_(True))) or 0,
           "printers": None, "errors": {}}
    try:
        from ..services import printer_status
        out["printers"] = [_printer(x) for x in printer_status.annotate(
            db, printer_access.visible(db, p, _farm().printers().get("printers", [])))]
    except Exception as e:  # noqa: BLE001
        out["errors"]["printers"] = str(getattr(e, "message", e))[:200]
    try:
        from ..services import maintenance
        m = maintenance.overview(db)
        out["maintenance"] = {"open": len(m["open"]), "due": sum(1 for x in m["plans"] if x.get("due")), "down": m["down"]}
    except Exception as e:  # noqa: BLE001
        out["errors"]["maintenance"] = str(e)[:200]
    db.rollback()
    return out


# ---------------------------------------------------------------- printers and cameras

def _farm():
    """The team's farm (farm interface)."""
    from ..clients import farm
    return farm()


BRANDS = ("Bambu Lab", "Prusa", "Creality", "Elegoo", "Anycubic", "Voron", "Qidi", "Sovol", "Snapmaker", "Flashforge",
          "Artillery", "RatRig", "Rat Rig", "Ultimaker", "Raise3D", "Anker", "Kingroon", "Two Trees", "Tronxy", "Geeetech",
          "BIQU", "Mingda", "LulzBot", "MakerBot", "Formlabs", "Peopoly", "Positron")
FAMILY_BRAND = {"bambu": "Bambu Lab", "prusalink": "Prusa"}


def brand_model(preset: str | None, family: str | None) -> tuple[str, str | None]:
    """Make and model from the printer's slicer preset ("Bambu Lab H2C 0.4 nozzle" -> Bambu Lab, H2C); without one, the
    brand its connection implies (bambu, prusalink), else Other."""
    import re
    name = re.sub(r"\s+\d+(\.\d+)?\s*(mm)?\s*nozzle.*$", "", (preset or "").strip(), flags=re.I)
    for b in BRANDS:
        if name.lower().startswith(b.lower()):
            return ("Rat Rig" if b == "RatRig" else b), (name[len(b):].strip() or None)
    if name:
        first, _, rest = name.partition(" ")
        return first, rest or None
    return FAMILY_BRAND.get((family or "").lower(), "Other"), None


def _job_label(x: dict) -> str | None:
    """The job's name; some connections (PrusaLink) send a job object instead, so fall back to the file's name."""
    import re
    j = x.get("job")
    if isinstance(j, str) and j.strip():
        return j.strip()
    f = x.get("file")
    if isinstance(f, str) and f.strip():
        f = f.strip().rsplit("/", 1)[-1]
        f = re.sub(r"\.(gcode|bgcode|3mf|gco|g)$", "", f, flags=re.I)
        return re.sub(r"[+_]+", " ", f).strip() or None
    return None


def _profiles(db) -> dict:
    from ..models import PrinterProfile
    from sqlalchemy import select
    return {(pp.farm_printer or pp.name): pp.machine_preset for pp in db.execute(select(PrinterProfile)).scalars()}


def _printer(x: dict, profiles: dict | None = None) -> dict:
    keep = ("id", "name", "family", "model", "state", "online", "busy", "ready", "progress", "eta_minutes", "file", "layer",
            "total_layers", "temps", "nozzle_temp", "bed_temp", "error", "nozzle_count", "error_message", "reported_code",
            "error_hidden", "mx_due", "last_result", "commands")
    out = {k: x.get(k) for k in keep if k in x}
    out["brand"], model = brand_model((profiles or {}).get(x.get("name")), x.get("family"))
    out["model"] = x.get("model") or model
    out["job_name"] = _job_label(x) if x.get("busy") else None
    # cheap: no image fetch, only "a job is printing on OpenPrintHQ" (the cover route answers 404 otherwise)
    out["cover_available"] = bool(x.get("busy")) and x.get("id") is not None and "nozzle_count" in x
    out.setdefault("nozzle_count", 1)
    return out


@router.get("/printers")
def printers(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from ..services import printer_status
    try:
        prof = _profiles(db)
        return {"printers": [_printer(x, prof) for x in printer_status.annotate(
            db, printer_access.visible(db, p, _farm().printers().get("printers", [])))]}
    except Exception as e:  # noqa: BLE001
        _err(e)


@router.get("/printers/{name}")
def printer(name: str, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """One printer. With OpenPrintHQ as the farm it adds nozzles, filament paths, what each slot has loaded, and
    the job fields the app draws (progress, layer, total_layers, job_name, eta, cover_available)."""
    printer_access.require(db, p, name)
    try:
        f = _farm()
        out = dict(f.printer(str(name)))
    except Exception as e:  # noqa: BLE001
        _err(e)
    from ..services import printer_status
    out = printer_status.annotate(db, [out])[0]
    eta = out.get("eta_minutes")
    out["job_name"] = _job_label(out)
    out["brand"], _m = brand_model(_profiles(db).get(name), out.get("family"))
    out["model"] = out.get("model") or _m
    out["eta"] = (now() + timedelta(minutes=eta)).isoformat() if isinstance(eta, (int, float)) and out.get("busy") else None
    out["cover_available"] = bool(out.get("busy")) and f.kind == "openprinthq"
    if f.kind == "openprinthq" and out.get("id") is not None:
        pid = int(out["id"])
        for key, fn in (("nozzles", f.nozzles), ("paths", f.filament_paths), ("loaded", f.loaded)):
            try:
                out[key] = fn(pid)
            except Exception as e:  # noqa: BLE001
                out[key] = None
                out.setdefault("errors", {})[key] = str(getattr(e, "message", e))[:200]
    return out


@router.get("/printers/{name}/snapshot")
def printer_snapshot(name: str, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """The printer's camera, one JPEG frame."""
    printer_access.require(db, p, name)
    try:
        data = _farm().snapshot(name)
    except Exception as e:  # noqa: BLE001
        _err(e)
    if not data:
        raise HTTPException(404, f"no camera frame for {name}")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


LIVE_FILES = re.compile(r"^(playlist\.m3u8|init\.mp4|segment\.(m4s|ts))$")
LIVE_PARAM = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@router.get("/printers/{name}/live.m3u8")
def printer_live(name: str, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """The printer's camera as live video: an HLS playlist (fragmented MP4) that iOS and Android players open
    directly, at the camera's own quality. The farm holds one connection to the camera while it is watched. The
    playlist's links are relative, so the player fetches the rest from /printers/{name}/hls/ with the same key."""
    printer_access.require(db, p, name)
    try:
        fc = _farm()
        sources = fc.camera_sources(name)
    except Exception as e:  # noqa: BLE001
        _err(e)
    for src in sources[:2]:
        body = fc.live_playlist(src)
        if body:
            return Response(content=body, media_type="application/vnd.apple.mpegurl",
                            headers={"Cache-Control": "no-store"})
    raise HTTPException(404, f"no live video for {name}")


@router.get("/printers/{name}/hls/{file}")
def printer_live_part(name: str, file: str, request: Request, db: Session = Depends(get_db),
                      p: Principal = Depends(need("view"))):
    """A part of a live view from /printers/{name}/live.m3u8: its playlist, init segment or media segments."""
    printer_access.require(db, p, name)
    if not LIVE_FILES.match(file):
        raise HTTPException(404, "no such file")
    params = {}
    for k in ("id", "n"):
        v = request.query_params.get(k)
        if v is None:
            continue
        if not LIVE_PARAM.match(v):
            raise HTTPException(400, f"bad {k}")
        params[k] = v
    if "id" not in params:
        raise HTTPException(400, "id is required")
    try:
        part = _farm().live_part(file, params)
    except Exception as e:  # noqa: BLE001
        _err(e)
    if not part:
        raise HTTPException(404, "this live view has ended; open it again")
    return Response(content=part[0], media_type=part[1], headers={"Cache-Control": "no-store"})


STREAM_SECONDS = 600       # one live view at most; the app reopens it


@router.get("/printers/{name}/stream")
def printer_stream(name: str, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """The printer's camera, live as a stream of JPEG frames (multipart MJPEG, about 2 to 3 a second: as fast as
    the farm hands out frames). Ends after 10 minutes. Works anywhere the hub does (no direct camera access)."""
    printer_access.require(db, p, name)
    import time
    from fastapi.responses import StreamingResponse
    try:
        fc = _farm()
        sources = fc.camera_sources(name)
    except Exception as e:  # noqa: BLE001
        _err(e)
    if not sources:
        raise HTTPException(404, f"no live camera for {name}")
    src = sources[0]

    def frames():
        end, misses = time.monotonic() + STREAM_SECONDS, 0
        while time.monotonic() < end and misses < 10:
            t = time.monotonic()
            try:
                data = fc.frame(src)
            except Exception:  # noqa: BLE001
                data = None
            if data:
                misses = 0
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n"
            else:
                misses += 1
            time.sleep(max(0.0, 0.3 - (time.monotonic() - t)))

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class CommandIn(BaseModel):
    command: str
    args: dict | None = None
    confirm: bool = False


PRINTER_COMMANDS = {"set_ready", "clear_ready", "pause", "resume", "cancel", "stop", "home", "light_on", "light_off",
                    "set_temperature", "preheat", "cooldown"}


@router.post("/printers/{name}/command")
def printer_command(name: str, body: CommandIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """The farm's printer verbs (set_ready, pause, resume, cancel, ...). The farm decides what each printer supports."""
    printer_access.require(db, p, name)
    if body.command not in PRINTER_COMMANDS:
        raise HTTPException(400, f"command must be one of {', '.join(sorted(PRINTER_COMMANDS))}")
    try:
        out = _farm().command(name, body.command, body.args, confirm=body.confirm)
    except Exception as e:  # noqa: BLE001
        _err(e)
    event(db, "farm", f"{name}: {body.command} from the mobile API", actor=_actor(p))
    db.commit()
    return {"ok": True, "result": out}


# ---------------------------------------------------------------- queue

@router.get("/queue")
def queue(include_done: bool = False, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from ..services import farm
    statuses = farm.ACTIVE + (farm.DONE if include_done else ())
    rows = db.execute(select(FarmJob).where(FarmJob.status.in_(statuses))
                      .order_by(FarmJob.priority.desc().nullslast(), FarmJob.created_ext)).scalars()
    return {"jobs": [farm.job_dict(j, {}) for j in list(rows)[:300]]}


class PriorityIn(BaseModel):
    priority: int


@router.post("/queue/{job_id}/priority")
def job_priority(job_id: str, body: PriorityIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    if not 1 <= body.priority <= 100:
        raise HTTPException(400, "priority is 1 to 100 (25 low, 50 normal, 75 high, 100 urgent)")
    try:
        _farm().set_priority(job_id, body.priority)
    except Exception as e:  # noqa: BLE001
        _err(e)
    fj = db.execute(select(FarmJob).where(FarmJob.external_id == str(job_id))).scalar()
    if fj:
        fj.priority = body.priority
    event(db, "farm", f"Job {job_id} priority {body.priority} from the mobile API", actor=_actor(p))
    db.commit()
    return {"ok": True}


class CancelIn(BaseModel):
    reason: str | None = None


@router.post("/queue/{job_id}/cancel")
def job_cancel(job_id: str, body: CancelIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """Cancel a job: one that hasn't started is taken out of the queue; a printing one is stopped."""
    fj = db.execute(select(FarmJob).where(FarmJob.external_id == str(job_id))).scalar()
    try:
        if fj and fj.status in ("printing", "paused", "resuming"):
            _farm().cancel_job(int(job_id))
        else:
            _farm().cancel_queued(job_id, body.reason or f"Cancelled from the mobile API by {_actor(p)}")
    except Exception as e:  # noqa: BLE001
        _err(e)
    event(db, "farm", f"Job {job_id} cancelled from the mobile API", actor=_actor(p))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- plate checks (the web app's own handlers)

class PassBody(BaseModel):
    set_ready: bool = True
    note: str | None = None


class FailBody(BaseModel):
    reason: str
    note: str | None = None
    reprint: bool = False
    set_ready: bool = True


@router.get("/plate-checks")
def plate_checks(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from ..services import farm
    rows = db.execute(select(PlateCheck).order_by(PlateCheck.id.desc()).limit(60)).scalars()
    rows = list(rows)
    return {"waiting": [farm.check_dict(c, {}) for c in rows if c.state == "waiting"],
            "recent": [farm.check_dict(c, {}) for c in rows if c.state != "waiting"][:20],
            "reasons": farm.FAIL_REASONS, "stagger_minutes": farm.stagger(db) / timedelta(minutes=1)}


@router.get("/plate-checks/{cid}/photo")
def plate_check_photo(cid: int, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from . import farm as fr
    return fr.check_photo(cid, db, p)


@router.post("/plate-checks/{cid}/pass")
def plate_check_pass(cid: int, body: PassBody, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from . import farm as fr
    return fr.check_pass(cid, fr.PassIn(**body.model_dump()), db, p)


@router.post("/plate-checks/{cid}/fail")
def plate_check_fail(cid: int, body: FailBody, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from . import farm as fr
    return fr.check_fail(cid, fr.FailIn(**body.model_dump()), db, p)


@router.post("/plate-checks/{cid}/skip")
def plate_check_skip(cid: int, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from . import farm as fr
    return fr.check_skip(cid, db, p)


# ---------------------------------------------------------------- spools, filaments, locations, labels, scanning
# are in routers/mobile_control.py (the one spool library, 2026-10-23).


# ---------------------------------------------------------------- parts (InvenTree)

@router.get("/parts")
def parts(low: bool = False, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    rows = list(db.execute(select(PartCache).order_by(PartCache.name)).scalars())
    out = [{"id": r.inventree_part_id, "name": r.name, "in_stock": r.in_stock, "allocated": r.allocated,
            "minimum": getattr(r, "minimum_stock", None)} for r in rows]
    if low:
        out = [x for x in out if (x["minimum"] or 0) and (x["in_stock"] or 0) - (x["allocated"] or 0) < x["minimum"]]
    return {"parts": out}


class AdjustIn(BaseModel):
    delta: float
    note: str | None = None


@router.post("/parts/{part_id}/adjust")
def part_adjust(part_id: int, body: AdjustIn, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    from . import ops
    try:
        return ops.adjust_part(part_id, ops.Adjust(delta=body.delta, note=body.note or f"Adjusted from the mobile API by {_actor(p)}"), db, p)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        _err(e)


# ---------------------------------------------------------------- orders, products, quotes

@router.get("/orders")
def orders(status: str | None = None, limit: int = 50, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from . import ops
    rows = ops.orders(db, p)
    if status:
        want = {s.strip() for s in status.split(",")}
        rows = [o for o in rows if o["status"] in want]
    return {"orders": rows[:max(1, min(limit, 200))]}


@router.get("/orders/{order_id}")
def order(order_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from . import ops
    row = next((o for o in ops.orders(db, p) if o["id"] == order_id), None)
    if not row:
        raise HTTPException(404)
    return row


class OrderPrioIn(BaseModel):
    priority: int = 75


@router.post("/orders/{order_id}/prioritise")
def order_prioritise(order_id: int, body: OrderPrioIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from . import farm as fr
    return fr.order_prioritise(order_id, fr.PrioIn(priority=body.priority), db, p)


@router.get("/products")
def products(live: bool | None = None, q: str | None = None, limit: int = 100,
             db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    s = select(Product).order_by(Product.id.desc())
    if live is not None:
        s = s.where(Product.live.is_(live))
    rows = []
    for pr in db.execute(s).scalars():
        if q and q.lower() not in f"{pr.title} {pr.sku}".lower():
            continue
        rows.append({"id": pr.id, "title": pr.title, "sku": pr.sku, "live": pr.live, "price": pr.price,
                     "model_id": pr.model_id, "woo_product_id": pr.woo_product_id, "stock": pr.woo_stock_pushed})
        if len(rows) >= max(1, min(limit, 500)):
            break
    return {"products": rows}


class LiveBody(BaseModel):
    live: bool


@router.post("/products/{product_id}/live")
def product_live(product_id: int, body: LiveBody, db: Session = Depends(get_db), p: Principal = Depends(need("store"))):
    from . import catalog
    return catalog.go_live(product_id, catalog.LiveIn(live=body.live), db, p)


@router.get("/quotes")
def quotes(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from ..services import quotes as qs
    rows = db.execute(select(Quote).where(Quote.status.in_(("ordered", "priced", "ready"))).order_by(Quote.id.desc()).limit(50)).scalars()
    out = []
    for x in rows:
        d = qs.admin_dict(db, x)
        o = db.get(Order, x.order_id) if x.order_id else None
        bill = ((o.raw or {}).get("billing") or {}) if o else {}
        name = (o.customer if o else None) or " ".join(v for v in (bill.get("first_name"), bill.get("last_name")) if v) or None
        d["customer_name"], d["customer_email"] = name, bill.get("email") or None
        out.append(d)
    return {"quotes": out}


@router.post("/quotes/{qid}/approve")
def quote_approve(qid: int, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from . import quotes as qr
    return qr.approve(qid, db, p)


class RefuseBody(BaseModel):
    reason: str | None = None


@router.post("/quotes/{qid}/refuse")
def quote_refuse(qid: int, body: RefuseBody, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from . import quotes as qr
    return qr.refuse(qid, qr.ReviewIn(reason=body.reason), db, p)


# ---------------------------------------------------------------- maintenance

@router.get("/maintenance")
def maintenance(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from . import farm as fr
    return fr.maint_overview(db, p)


class DoneBody(BaseModel):
    notes: str | None = None


@router.post("/maintenance/tasks/{tid}/done")
def maintenance_done(tid: int, body: DoneBody, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from . import farm as fr
    return fr.maint_task_done(tid, fr.DoneIn(notes=body.notes), db, p)


# ---------------------------------------------------------------- events and push

@router.get("/events")
def events(since_id: int = 0, limit: int = 50, level: str | None = None,
           db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """The hub's event log, newest first; poll with since_id for what is new."""
    s = select(Event).where(Event.id > since_id).order_by(Event.id.desc()).limit(max(1, min(limit, 200)))
    if level:
        s = s.where(Event.level == level)
    return {"events": [{"id": e.id, "ref": e.ref, "level": e.level, "message": e.message, "actor": e.actor,
                        "at": _iso(e.created_at)} for e in db.execute(s).scalars()]}


# ---------------------------------------------------------------- the app's look and its home screen tiles

@router.get("/look")
def look(request: Request, skin: str | None = None, all: bool = False, db: Session = Depends(get_db),
         p: Principal = Depends(need("view"))):
    """The look the app draws with: the key owner's skin and light/dark choice (or ?skin= to preview another), every
    colour token for light and dark, the font families with their font files, and the wordmark. Read from the hub's
    shared stylesheet, so it always matches the web apps. Sends an ETag; send If-None-Match to get 304."""
    from ..services import look as looks
    prefs = {}
    if p.get("user_id"):
        u = db.get(User, p["user_id"])
        prefs = (u.prefs or {}) if u else {}
    want = skin or prefs.get("skin") or "openprinthq"
    if skin is not None and skin not in looks.SKINS:
        raise HTTPException(400, "skin is openprinthq or classic")
    doc = looks.build(want, prefs.get("mode") or "system", all_skins=all)
    if doc is None:
        raise HTTPException(503, "the hub's stylesheet is missing; rebuild the hub")
    tag = looks.etag(doc)
    hdr = {"ETag": tag, "Cache-Control": "private, max-age=300"}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=hdr)
    return Response(content=json.dumps(doc), media_type="application/json", headers=hdr)


# ---------------------------------------------------------------- TVs (Apple TV, Android TV wall displays)

TV_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # no 0/O, 1/I/L: read off a TV across the room


class TvStartIn(BaseModel):
    device: str = "TV"


class TvPollIn(BaseModel):
    device_code: str


class TvApproveIn(BaseModel):
    code: str
    name: str | None = None


def _tv_hash(code: str) -> str:
    import hashlib
    return hashlib.sha256(code.encode()).hexdigest()


def _tv_norm(code: str) -> str:
    c = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    return f"{c[:4]}-{c[4:8]}" if len(c) == 8 else c


@router.post("/tv/start")
def tv_start(body: TvStartIn, db: Session = Depends(get_db)):
    """A TV starts signing in: a short code to show on screen, and a secret only the TV keeps (it asks /tv/poll with
    it). No key needed. The code works for 10 minutes."""
    import secrets
    from ..models import TvLink
    from .phone_pages import _hub
    now_ = datetime.now(timezone.utc)
    recent = db.execute(select(func.count()).select_from(TvLink).where(TvLink.created_at > now_ - timedelta(minutes=10),
                                                                       TvLink.approved_at.is_(None))).scalar_one()
    if recent > 50:
        raise HTTPException(429, "too many TVs signing in; try again in a few minutes")
    device_code = secrets.token_urlsafe(32)
    while True:
        user_code = "".join(secrets.choice(TV_ALPHABET) for _ in range(8))
        user_code = f"{user_code[:4]}-{user_code[4:]}"
        if not db.execute(select(TvLink.id).where(TvLink.user_code == user_code, TvLink.expires_at > now_)).first():
            break
    db.add(TvLink(device_hash=_tv_hash(device_code), user_code=user_code, device=(body.device or "TV")[:60],
                  expires_at=now_ + timedelta(minutes=10)))
    db.commit()
    return {"device_code": device_code, "user_code": user_code, "verify_url": f"{_hub()}/tv", "hub": _hub(),
            "expires_in": 600, "interval": 5}


@router.post("/tv/poll")
def tv_poll(body: TvPollIn, db: Session = Depends(get_db)):
    """The TV asks whether its code was approved: {state: pending}, or once {state: ready, key, hub, team}."""
    from ..models import Team, TvLink
    from .phone_pages import _hub
    t = db.execute(select(TvLink).where(TvLink.device_hash == _tv_hash(body.device_code))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "unknown TV code")
    now_ = datetime.now(timezone.utc)
    if t.claimed_at:
        raise HTTPException(410, "this code was already used")
    if not t.approved_at:
        exp = t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=timezone.utc)
        if exp < now_:
            raise HTTPException(410, "the code expired; show a new one")
        return {"state": "pending", "user_code": t.user_code}
    raw = new_api_key(db, f"TV: {t.name or t.device}"[:120], ["view"], team_id=t.team_id, user_id=t.user_id)
    k = db.execute(select(ApiKey).where(ApiKey.team_id == t.team_id).order_by(ApiKey.id.desc()).limit(1)).scalar_one()
    t.key_id, t.claimed_at = k.id, now_
    team = db.get(Team, t.team_id)
    db.commit()
    return {"state": "ready", "key": raw, "hub": _hub(), "team": team.name if team else None, "name": t.name}


@router.post("/tv/approve")
def tv_approve(body: TvApproveIn, db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    """Approve the code a TV shows (from the hub's Connect a TV page or the phone app). The TV gets a view-only key."""
    from ..models import TvLink
    code = _tv_norm(body.code)
    now_ = datetime.now(timezone.utc)
    t = db.execute(select(TvLink).where(TvLink.user_code == code, TvLink.expires_at > now_,
                                        TvLink.approved_at.is_(None))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "no TV is showing that code (it may have expired; the TV shows a new one)")
    t.approved_at, t.team_id, t.user_id, t.approved_by = now_, p.team_id, p.get("user_id"), _actor(p)
    t.name = (body.name or "").strip()[:120] or None
    event(db, "api", f"TV approved: {t.name or t.device} ({code})", actor=_actor(p))
    db.commit()
    return {"device": t.device, "name": t.name, "code": code}


@router.get("/wall")
def wall(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """Everything a wall display shows, in one call: the printers and the Start screen's tiles."""
    from ..models import Team
    team = db.get(Team, p.team_id)
    try:
        ps = printers(db=db, p=p).get("printers", [])
    except HTTPException:
        ps = []
    return {"team": team.name if team else None, "time": datetime.now(timezone.utc).isoformat(), "printers": ps,
            "tiles": tiles(db=db, p=p)}


@router.get("/tiles")
def tiles(spool_low_g: float = 150, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """Everything the Start screen's live tiles show, in one small call (cheap enough for background refresh).
    A part that fails is left out and named in errors."""
    from ..services import farm
    out: dict = {"api": API_VERSION, "time": now().isoformat(), "errors": {}}

    try:
        prs = [_printer(x) for x in printer_access.visible(db, p, _farm().printers().get("printers", []))]
        def st(x):
            if x.get("online") is False or x.get("state") in ("offline", "disconnected"):
                return "offline"
            return "printing" if x.get("busy") or x.get("state") in ("printing", "paused") else "idle"
        out["farm"] = {"printing": sum(st(x) == "printing" for x in prs), "idle": sum(st(x) == "idle" for x in prs),
                       "offline": sum(st(x) == "offline" for x in prs),
                       "now": [{"name": x.get("name"), "state": x.get("state"), "progress": x.get("progress"),
                                "eta_minutes": x.get("eta_minutes"), "file": x.get("file")}
                               for x in prs if st(x) == "printing"][:8]}
    except Exception as e:  # noqa: BLE001
        out["errors"]["farm"] = str(getattr(e, "message", e))[:200]

    waiting = list(db.execute(select(PlateCheck).where(PlateCheck.state == "waiting")
                              .order_by(PlateCheck.id.desc())).scalars())
    last = waiting[0] if waiting else None
    out["plate_checks"] = {"waiting": len(waiting),
                           "latest": {"id": last.id, "printer": last.printer_name, "job": last.job_name,
                                      "photo": f"/api/mobile/v1/plate-checks/{last.id}/photo" if last.photo else None,
                                      "at": _iso(getattr(last, "created_at", None))} if last else None}

    active = list(db.execute(select(FarmJob.status).where(FarmJob.status.in_(farm.ACTIVE))).scalars())
    out["queue"] = {s: active.count(s) for s in sorted(set(active))}
    out["queue"]["total"] = len(active)

    try:
        from ..services.schedule import default_due
        open_ = list(db.execute(select(Order).where(Order.status.in_(("processing", "on-hold")),
                                                    Order.is_test.is_(False))).scalars())
        dues = []
        for o in open_:
            d = farm._aware(o.due_at) if getattr(o, "due_at", None) else default_due(db, o)
            if d:
                dues.append((d, o))
        dues.sort(key=lambda t: t[0])
        nxt = dues[0] if dues else None
        out["orders"] = {"open": len(open_), "next_due": {"id": nxt[1].id, "number": nxt[1].number,
                                                           "due_at": nxt[0].isoformat()} if nxt else None}
    except Exception as e:  # noqa: BLE001
        db.rollback()
        out["errors"]["orders"] = str(e)[:200]

    sp = list(db.execute(select(SpoolCache).where(SpoolCache.archived.is_(False))).scalars())
    low = sorted((s for s in sp if (s.remaining_g or 0) < spool_low_g), key=lambda s: s.remaining_g or 0)
    out["filament"] = {"spools": len(sp), "low": len(low), "low_g": spool_low_g,
                       "lowest": [{"id": s.spool_id, "name": " ".join(x for x in (s.material, s.name) if x),
                                   "remaining_g": round(s.remaining_g or 0)} for s in low[:3]]}

    parts = list(db.execute(select(PartCache)).scalars())
    out["parts"] = {"below_minimum": sum(1 for r in parts if (r.minimum_stock or 0)
                                         and (r.in_stock or 0) - (r.allocated or 0) < r.minimum_stock)}
    out["quotes"] = {"waiting": db.scalar(select(func.count()).select_from(Quote).where(Quote.status == "ordered")) or 0}
    out["store"] = {"live": db.scalar(select(func.count()).select_from(Product).where(Product.live.is_(True))) or 0}

    try:
        from ..services import maintenance
        m = maintenance.overview(db)
        out["maintenance"] = {"open": len(m["open"]), "due": sum(1 for x in m["plans"] if x.get("due")), "down": m["down"]}
    except Exception as e:  # noqa: BLE001
        db.rollback()
        out["errors"]["maintenance"] = str(e)[:200]

    ev = db.execute(select(Event).order_by(Event.id.desc()).limit(1)).scalar()
    out["activity"] = {"id": ev.id, "message": ev.message, "level": ev.level, "at": _iso(ev.created_at)} if ev else None
    db.rollback()
    return out


@router.get("/push")
def push_info(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """Where this team's push notifications go, so a phone can subscribe (the ntfy app, or any client)."""
    from ..services import alerts
    from ..services.connections import get
    c = get("alerts", required=False) or {}
    url = c.get("ntfy_url") or ""
    m = re.match(r"^(https?://[^/]+)/([^/?#]+)$", url)
    return {"channels": alerts.configured(), "ntfy": {"server": m.group(1), "topic": m.group(2),
                                                      "protected": bool(c.get("ntfy_token"))} if m else None,
            "events": {k: v[0] for k, v in alerts.EVENTS.items()}, "rules": alerts.rules(db)}


@router.post("/push/test")
def push_test(p: Principal = Depends(need("farm"))):
    from ..services import alerts
    ok, msg = alerts.send_test()
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "detail": msg}


# ---------------------------------------------------------------- API keys

class KeyBody(BaseModel):
    name: str
    scopes: list[str] = ["read"]
    expires_in_days: int | None = 90
    instance_id: str | None = None
    bind_instance: bool = True
    note: str | None = None


def _key_dict(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": k.scopes, "revoked": k.revoked,
            "expires_at": _iso(k.expires_at), "expired": bool(k.expires_at and _aware(k.expires_at) <= now()),
            "instance": ("bound" if k.instance_id else "binds on first use" if k.bind_instance else "any"),
            "note": k.note, "created_at": _iso(k.created_at), "last_used_at": _iso(k.last_used_at), "last_ip": k.last_ip}


def _aware(d):
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def check_scopes(scopes: list[str], p: Principal, db: Session) -> list[str]:
    """Scopes the caller may hand out: never more than it holds itself."""
    from ..models import SCOPE_PERMS
    have = set(p.get("perms") or [])
    out = []
    for sc in dict.fromkeys(s.strip() for s in scopes if s and s.strip()):
        if sc in COARSE:
            if not set(SCOPE_PERMS[sc]) <= have:
                raise HTTPException(403, f"you can't grant {sc}: it includes permissions you don't have")
        elif sc in PERMS:
            if sc not in have:
                raise HTTPException(403, f"you can't grant {sc}: you don't have it")
        elif sc.startswith("relay:"):
            app = sc.split(":", 1)[1]
            if app != "*" and app not in RELAY_APPS:
                raise HTTPException(400, f"relay apps: {', '.join(RELAY_APPS)} or *")
            if "connections" not in have:
                raise HTTPException(403, "raw relay keys need the connections permission")
        elif sc == "clip":
            if "models" not in have:
                raise HTTPException(403, "you can't grant clip: you can't add models")
        elif sc == "owner":
            if not p.get("platform_admin") or not p.get("user_id"):
                raise HTTPException(403, "only the hub owner can make owner keys")
        else:
            raise HTTPException(400, f"unknown scope {sc}: use read, write, admin, a permission name, clip, relay:<app> or owner")
        out.append(sc)
    if not out:
        raise HTTPException(400, "give the key at least one scope")
    return out


def _make_key(db: Session, body: KeyBody, team_id: int, user_id: int | None, scopes: list[str]) -> dict:
    if body.expires_in_days is not None and not 1 <= body.expires_in_days <= 3650:
        raise HTTPException(400, "expires_in_days is 1 to 3650 (or null for no expiry)")
    raw = new_api_key(db, body.name.strip()[:120] or "mobile", scopes, team_id=team_id, user_id=user_id)
    k = db.execute(select(ApiKey).where(ApiKey.prefix == raw.split("_")[1])).scalar()
    k.expires_at = now() + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    k.instance_id = (body.instance_id or "").strip()[:120] or None
    k.bind_instance = bool(body.bind_instance) and not k.instance_id
    k.note = (body.note or "")[:300] or None
    db.flush()
    return {"key": raw, **_key_dict(k),
            "use": "Authorization: Bearer <key>" + ("; X-Hub-Instance: <the app's install id>" if k.instance_id or k.bind_instance else "")}


@router.get("/keys")
def keys(db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    rows = db.execute(select(ApiKey).where(ApiKey.team_id == p.team_id).order_by(ApiKey.id)).scalars()
    return {"keys": [_key_dict(k) for k in rows]}


@router.post("/keys")
def make_key(body: KeyBody, db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    """A key for an app, shown once. Scopes never exceed the maker's own permissions; a person's key also stops
    working when they leave the team."""
    scopes = check_scopes(body.scopes, p, db)
    out = _make_key(db, body, p.team_id, p.get("user_id"), scopes)
    event(db, "api", f"API key '{body.name}' made ({', '.join(scopes)})", actor=_actor(p))
    db.commit()
    return out


@router.delete("/keys/{key_id}")
def revoke_key(key_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    k = db.get(ApiKey, key_id)
    if not k or k.team_id != p.team_id:
        raise HTTPException(404)
    k.revoked = True
    event(db, "api", f"API key '{k.name}' revoked", actor=_actor(p))
    db.commit()
    return {"ok": True}


@router.post("/keys/{key_id}/unbind")
def unbind_key(key_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    """Free a key from its app instance (new phone): the next instance that uses it takes it."""
    k = db.get(ApiKey, key_id)
    if not k or k.team_id != p.team_id:
        raise HTTPException(404)
    k.instance_id, k.bind_instance = None, True
    db.commit()
    return _key_dict(k)


# ---------------------------------------------------------------- owner: provisioning

def _owner(p: Principal):
    if not p.get("platform_admin"):
        raise HTTPException(403, "owner only: the hub owner, signed in or with an owner-scoped key")


@router.get("/owner/teams")
def owner_teams(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    _owner(p)
    from . import teams as tr
    return tr.platform_teams(db, p)


class TeamBody(BaseModel):
    name: str
    owner_email: str | None = None


@router.post("/owner/teams")
def owner_make_team(body: TeamBody, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """Formerly a new team. An installation has one team, so there is none to make: 410 Gone."""
    _owner(p)
    from ..services import teams as ts
    raise HTTPException(410, ts.ONE_TEAM_ONLY)


@router.post("/owner/people")
def owner_add_person(body: dict, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """Same as the web app's Add people: {email, name, mode: team | individual | join, team_name, team_id, role, perms}."""
    _owner(p)
    from . import teams as tr
    try:
        person = tr.PersonIn(**body)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)[:300])
    return tr.add_person(person, db, p)


@router.post("/owner/teams/{team_id}/keys")
def owner_team_key(team_id: int, body: KeyBody, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """A key for another team (provisioning its apps). It belongs to the team, not a person; owner scope can't be
    given this way."""
    _owner(p)
    from ..tenant import unscoped
    with unscoped():
        if not db.get(Team, team_id):
            raise HTTPException(404)
        bad = [s for s in body.scopes if s == "owner"]
        if bad:
            raise HTTPException(400, "owner keys are made by the owner for their own team")
        scopes = []
        for sc in dict.fromkeys(body.scopes):
            if sc in COARSE or sc in PERMS or (sc.startswith("relay:") and (sc[6:] in RELAY_APPS or sc[6:] == "*")):
                scopes.append(sc)
            else:
                raise HTTPException(400, f"unknown scope {sc}")
        if not scopes:
            raise HTTPException(400, "give the key at least one scope")
        out = _make_key(db, body, team_id, None, scopes)
        event(db, "api", f"API key '{body.name}' made for team {team_id}", actor=_actor(p))
        db.commit()
        return out


@router.get("/owner/teams/{team_id}/members")
def owner_members(team_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    _owner(p)
    from ..tenant import unscoped
    with unscoped():
        rows = db.execute(select(Membership).where(Membership.team_id == team_id)).scalars()
        return {"members": [{"user_id": m.user_id, "email": m.user.email if m.user else None, "role": m.role,
                             "perms": m.effective_perms} for m in rows]}


# ---------------------------------------------------------------- raw relay

def _relay_allowed_apps(p: Principal) -> list[str]:
    if "connections" not in (p.get("perms") or []):
        return []
    if p.get("kind") != "api":
        return list(RELAY_APPS)
    sc = [s for s in (p.get("key_scopes") or []) if s.startswith("relay:")]
    if "relay:*" in sc:
        return list(RELAY_APPS)
    return [s[6:] for s in sc if s[6:] in RELAY_APPS]


def _seg(s: str) -> str:
    from urllib.parse import quote
    return quote(str(s), safe="")


_BAD_PATH = re.compile(r"(^|/)\.\.(/|$)|^[a-z][a-z0-9+.-]*:|^//|[\r\n]", re.I)
_DROP_REQ = {"host", "authorization", "cookie", "content-length", "connection", "x-api-key", "x-hub-instance",
             "x-hub-team", "accept-encoding", "transfer-encoding", "x-forwarded-for", "x-forwarded-proto",
             "x-forwarded-host", "x-real-ip"}
_KEEP_RESP = {"content-type", "content-disposition", "etag", "last-modified", "cache-control", "x-total-count", "link", "location"}


def _target(app: str, path: str, p: Principal, db: Session) -> tuple[str, dict, object]:
    """(absolute URL, headers to add, httpx auth) for one relayed request, with the team's stored credentials."""
    from ..services.connections import get
    if app == "openprinthq":            # the engine API through the control plane, with the team's API key
        c = get("openprinthq")
        return c["url"].rstrip("/") + "/api/engine/api/v1/" + path, {"Authorization": f"Bearer {c['api_key']}"}, None
    if app == "spoolman":
        c = get("spoolman")
        return c["url"].rstrip("/") + "/api/" + path, {}, None
    if app == "inventree":
        c = get("inventree")
        h = {"Authorization": f"Token {c['token']}"}
        if c.get("host"):
            h.update({"Host": c["host"], "X-Forwarded-Proto": "https"})
        return c["url"].rstrip("/") + "/api/" + path, h, None
    if app == "woo":
        c = get("woo")
        return c["url"].rstrip("/") + "/wp-json/" + path, {}, (c["key"], c["secret"])
    if app == "manyfold":
        c = get("manyfold")
        return c["url"].rstrip("/") + "/" + path, _manyfold_headers(c), None
    if app == "moonraker":
        from ..services.filament_use import klipper_hosts
        printer, _, rest = path.partition("/")
        url = klipper_hosts(db).get(printer)
        if not url:
            raise HTTPException(404, f"no Moonraker found for printer {printer!r} (Klipper printers in the farm)")
        return url.rstrip("/") + "/" + rest, {}, None
    raise HTTPException(404, f"relay apps: {', '.join(RELAY_APPS)}")


_mf_tokens: dict = {}


def _manyfold_headers(c: dict) -> dict:
    """Manyfold's API takes an OAuth token (client credentials, the hub's registered app)."""
    import time
    from urllib.parse import urlparse
    h = {"Accept": "application/vnd.manyfold.v0+json, application/ld+json, application/json"}
    host = urlparse(c.get("public_url") or "").hostname
    if host:
        h.update({"Host": host, "X-Forwarded-Proto": "https"})
    key = (c["url"], c.get("client_id"))
    tok = _mf_tokens.get(key)
    if not tok or tok[1] < time.time() + 30:
        if not c.get("client_id"):
            raise HTTPException(400, "Manyfold has no OAuth app credentials in this team's connection")
        r = httpx.post(c["url"].rstrip("/") + "/oauth/token", headers={k: v for k, v in h.items() if k != "Accept"},
                       data={"grant_type": "client_credentials", "client_id": c["client_id"],
                             "client_secret": c.get("client_secret"), "scope": "read write"}, timeout=20)
        if r.status_code >= 400:
            raise HTTPException(502, f"Manyfold refused the hub's app credentials: {r.text[:200]}")
        j = r.json()
        tok = (j["access_token"], time.time() + float(j.get("expires_in") or 3600))
        _mf_tokens[key] = tok
    h["Authorization"] = f"Bearer {tok[0]}"
    return h


async def _relay(app: str, path: str, request: Request, db: Session, p: Principal):
    if app not in _relay_allowed_apps(p):
        raise HTTPException(403, f"relay to {app} needs the connections permission and a relay:{app} (or relay:*) key scope")
    if _BAD_PATH.search(path or ""):
        raise HTTPException(400, "bad relay path")
    body = await request.body()
    if len(body) > RELAY_MAX:
        raise HTTPException(413, "request too large for the relay (20 MB)")
    method = request.method.upper()

    if app == "dam":                    # ResourceSpace: signed function calls, not paths
        import json as _json
        from starlette.concurrency import run_in_threadpool
        from ..clients import dam
        try:
            doc = _json.loads(body or b"{}")
        except ValueError:
            raise HTTPException(400, 'POST {"function": "...", "params": [...]}')
        fn = str(doc.get("function") or path or "").strip()
        if not re.fullmatch(r"[a-z_]{2,60}", fn):
            raise HTTPException(400, "function is a ResourceSpace API function name")
        try:
            out = await run_in_threadpool(lambda: dam().call(fn, *(doc.get("params") or [])))
        except Exception as e:  # noqa: BLE001
            _err(e)
        if not fn.startswith(("get_", "search_", "do_search", "list_")):
            event(db, "relay", f"dam {fn}", actor=_actor(p))
            db.commit()
        return {"result": out}

    url, add, auth = _target(app, path, p, db)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQ}
    headers.update(add)
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False, auth=auth) as cl:
            r = await cl.request(method, url, params=list(request.query_params.multi_items()), content=body or None,
                                 headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{app} did not answer: {type(e).__name__}")
    if len(r.content) > RELAY_MAX:
        raise HTTPException(502, f"{app} answer too large for the relay")
    if method not in ("GET", "HEAD", "OPTIONS"):
        event(db, "relay", f"{app} {method} /{path} -> {r.status_code}", actor=_actor(p))
        db.commit()
    loc = r.headers.get("location") or ""
    if app == "manyfold" and r.status_code in (301, 302, 303) and "/users/edit" in loc:
        raise HTTPException(409, "Manyfold's first-run setup is not finished: open Manyfold in a browser, "
                                 "complete the account form, then try again")
    keep = {k: v for k, v in r.headers.items() if k.lower() in _KEEP_RESP}
    return Response(content=r.content, status_code=r.status_code, headers=keep)


@router.api_route("/relay/{app}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def relay(app: str, path: str, request: Request, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """Raw access to a connected app's own API, with the team's stored credentials. Paths are relative to:
    openprinthq /api/engine/api/v1/ (the farm's engine, the team's API key), spoolman /api/,
    inventree /api/, woo /wp-json/, manyfold /, moonraker /<printer>/<path>, dam: POST {"function", "params"}.
    Needs the connections permission and a relay:<app> (or relay:*) scope; changes are written to the event log."""
    return await _relay(app, path, request, db, p)


@router.post("/relay/dam")
async def relay_dam(request: Request, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    return await _relay("dam", "", request, db, p)


# ---------------------------------------------------------------- pairing a phone by QR code (2026-10-22)

PAIR_MINUTES = 10
ACCESS = {
    "view": ("View only", ["See printers and cameras, the queue, plate checks, orders, stock and the store"]),
    "act": ("View and act", ["See printers and cameras, the queue, plate checks, orders, stock and the store",
                             "Pass and fail plates, run printer commands, and change the queue, stock, products and quotes"]),
    "all": ("Everything you can do", ["Everything you can do in the hub, including its settings"]),
}
_pair_hits: dict = {}


def _pair_limit(request: Request):
    """At most 30 unauthenticated pairing calls a minute from one address."""
    import time
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")
    t = time.time()
    hits = [x for x in _pair_hits.get(ip, []) if t - x < 60]
    if len(hits) >= 30:
        raise HTTPException(429, "too many tries; wait a minute")
    hits.append(t)
    _pair_hits[ip] = hits
    if len(_pair_hits) > 5000:
        _pair_hits.clear()


def _code_hash(code: str) -> str:
    import hashlib
    return hashlib.sha256(code.encode()).hexdigest()


def _pair_url(code: str) -> str:
    """What the QR code holds: a plain https link to this hub's /pair page, so any camera can open it. The page
    opens the app (Android intent, iOS app link) or offers to install it. The app reads the same link."""
    from ..config import settings
    return f"{settings().public_url.rstrip('/')}/pair?code={code}"


def _qr_svg(text: str) -> str:
    import io
    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=8, border=4, xmldecl=False, omitsize=True, dark="#000000", light="#ffffff")
    return buf.getvalue().decode()


def _pairing_state(pp, now_=None) -> str:
    now_ = now_ or now()
    if pp.claimed_at:
        return "connected"
    if pp.cancelled:
        return "cancelled"
    return "expired" if _aware(pp.expires_at) <= now_ else "waiting"


class PairingIn(BaseModel):
    access: str = "act"          # view | act | all
    days: int | None = 90        # how long the phone's key works; None = no expiry


@router.post("/pairings")
def make_pairing(body: PairingIn, db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    """Start pairing a phone: returns a QR code (SVG) the OpenPrintHQ app scans. It works once, for 10 minutes."""
    import secrets
    from ..models import PhonePairing
    if body.access not in ACCESS:
        raise HTTPException(400, "access is view, act or all")
    if body.days is not None and not 1 <= body.days <= 3650:
        raise HTTPException(400, "days is 1 to 3650, or null for no expiry")
    if body.access == "view":
        scopes = ["read"]
    elif body.access == "act":
        scopes = ["read", "write"]
    else:
        scopes = sorted(set(p.get("perms") or []) - {"api_keys"}) or ["read"]
    scopes = check_scopes(scopes, p, db)
    code = secrets.token_urlsafe(24)
    pp = PhonePairing(code_hash=_code_hash(code), team_id=p.team_id, user_id=p.get("user_id"), made_by=_actor(p),
                      access=body.access, scopes=scopes, key_days=body.days,
                      expires_at=now() + timedelta(minutes=PAIR_MINUTES))
    db.add(pp)
    db.flush()
    url = _pair_url(code)
    out = {"id": pp.id, "url": url, "qr_svg": _qr_svg(url), "expires_at": _iso(pp.expires_at), "state": "waiting",
           "access": ACCESS[body.access][0], "days": body.days}
    db.commit()
    return out


@router.get("/pairings/{pid}")
def pairing_status(pid: int, db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    """For the hub page that shows the QR: waiting, connected (with the phone's name), expired or cancelled."""
    from ..models import PhonePairing
    pp = db.get(PhonePairing, pid)
    if not pp or pp.team_id != p.team_id:
        raise HTTPException(404)
    return {"id": pp.id, "state": _pairing_state(pp), "device": pp.device, "key_id": pp.key_id,
            "expires_at": _iso(pp.expires_at), "connected_at": _iso(pp.claimed_at)}


@router.delete("/pairings/{pid}")
def cancel_pairing(pid: int, db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    from ..models import PhonePairing
    pp = db.get(PhonePairing, pid)
    if not pp or pp.team_id != p.team_id:
        raise HTTPException(404)
    if not pp.claimed_at:
        pp.cancelled = True
        db.commit()
    return {"ok": True}


def _open_pairing(db: Session, code: str):
    from ..models import PhonePairing
    pp = db.execute(select(PhonePairing).where(PhonePairing.code_hash == _code_hash(code or ""))).scalar()
    if not pp:
        raise HTTPException(404, "this QR code is not from this hub, or was replaced; make a new one in the hub")
    st = _pairing_state(pp)
    if st == "connected":
        raise HTTPException(410, "this QR code was already used; make a new one in the hub")
    if st == "cancelled":
        raise HTTPException(410, "this QR code was cancelled in the hub; make a new one")
    if st == "expired":
        raise HTTPException(410, "this QR code has expired (they last 10 minutes); make a new one in the hub")
    return pp


@router.get("/pair/{code}")
def pair_preview(code: str, request: Request, db: Session = Depends(get_db)):
    """What the phone shows before you confirm: which hub and team, who made the code, what the phone may do and
    for how long. No key is made and nothing is used up."""
    from ..config import settings
    from ..models import Team, User
    from ..services import look as looks
    _pair_limit(request)
    pp = _open_pairing(db, code)
    team = db.get(Team, pp.team_id)
    prefs = {}
    if pp.user_id:
        u = db.get(User, pp.user_id)
        prefs = (u.prefs or {}) if u else {}
    accent = {}
    css = looks.load_css()
    skin = prefs.get("skin") if prefs.get("skin") in looks.SKINS else "openprinthq"
    if css:
        sl = looks.skin_look(css, skin)
        accent = {"skin": skin, "dark": sl["colors"]["dark"].get("accent"), "light": sl["colors"]["light"].get("accent"),
                  "dark_text": sl["colors"]["dark"].get("accent_text"), "light_text": sl["colors"]["light"].get("accent_text")}
    label, can = ACCESS.get(pp.access, ACCESS["act"])
    return {"hub": settings().public_url.rstrip("/"), "team": team.name if team else "", "made_by": pp.made_by,
            "access": pp.access, "access_label": label, "can": can, "days": pp.key_days,
            "expires_at": _iso(pp.expires_at), "look": accent}


class ClaimIn(BaseModel):
    device: str | None = None


@router.post("/pair/{code}/claim")
def pair_claim(code: str, body: ClaimIn, request: Request, db: Session = Depends(get_db)):
    """The person confirmed on the phone: make the key, tied to this phone's X-Hub-Instance, and use up the code."""
    from .. import tenant
    from ..models import Membership, PhonePairing
    _pair_limit(request)
    inst = (request.headers.get("x-hub-instance") or "").strip()[:120]
    if len(inst) < 8:
        raise HTTPException(400, "the app must send its install id (X-Hub-Instance)")
    pp = _open_pairing(db, code)
    if pp.user_id and not db.execute(select(Membership).where(Membership.team_id == pp.team_id,
                                                             Membership.user_id == pp.user_id)).scalar():
        raise HTTPException(403, "the person who made this QR code is no longer on this team")
    # Use the code up first, so two phones scanning at once cannot both connect.
    won = db.execute(PhonePairing.__table__.update()
                     .where(PhonePairing.id == pp.id, PhonePairing.claimed_at.is_(None))
                     .values(claimed_at=now())).rowcount
    if not won:
        raise HTTPException(410, "this QR code was just used by another phone; make a new one in the hub")
    device = " ".join((body.device or "Phone").split())[:60] or "Phone"
    tenant.bind_session(db, pp.team_id)
    with tenant.use_team(pp.team_id):
        kb = KeyBody(name=f"{device} (QR)", scopes=pp.scopes, expires_in_days=pp.key_days, instance_id=inst,
                     bind_instance=False, note=f"paired by QR, made by {pp.made_by or 'the hub'}")
        out = _make_key(db, kb, pp.team_id, pp.user_id, pp.scopes)
        db.execute(PhonePairing.__table__.update().where(PhonePairing.id == pp.id)
                   .values(device=device, instance_id=inst, key_id=out["id"]))
        event(db, "api", f"Phone '{device}' connected by QR ({ACCESS.get(pp.access, ACCESS['act'])[0].lower()})",
              actor=pp.made_by or "api")
        db.commit()
    return {"key": out["key"], "name": out["name"], "scopes": out["scopes"], "expires_at": out["expires_at"]}
