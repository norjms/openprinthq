"""Printers, slicing, review and approval (through the team's slicer service)."""
import json
import re

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session, selectinload

from ..auth import need, Principal
from ..clients.base import ApiError
from ..db import get_db
from ..jobs import enqueue
from ..models import Model, PrinterProfile, SliceResult, Product
from ..services import slicing

router = APIRouter(prefix="/api")


def prof_dict(p: PrinterProfile) -> dict:
    return {"id": p.id, "name": p.name, "engine": slicing.engine_of(p), "engine_label": slicing.label(slicing.engine_of(p)),
            "machine_preset": p.machine_preset, "imported": bool(p.machine_config), "process_preset": p.process_preset,
            "tools": p.tools or [], "filament_presets": p.filament_presets or {},
            "machine_overrides": p.machine_overrides or {}, "process_overrides": p.process_overrides or {},
            "use_model_settings": p.use_model_settings, "active": p.active, "is_default": p.is_default,
            "dispatch": p.dispatch or "none", "farm_printer": p.farm_printer, "hourly_rate": p.hourly_rate, "bed_type": p.bed_type, "filament_slots": p.filament_slots, "notes": p.notes}


def result_dict(r: SliceResult | None, prof: PrinterProfile, model: Model, db: Session | None = None) -> dict:
    base = {"profile_id": prof.id, "printer": prof.name, "engine": slicing.engine_of(prof)}
    if not r:
        return {**base, "state": "none", "problems": slicing.problems_for(model, prof)}
    extra = {}
    if db is not None:
        extra = {"cost": slicing.cost_of(db, model, prof, r.stats or {}),
                 "approved_cost": slicing.cost_of(db, model, prof, r.approved_stats or {}) if r.approved_file_ids else None,
                 "prints": slicing.print_summary(db, r)}
    pv = [f"/api/slice-results/{r.id}/preview/{n}" for n in (r.previews or [])]
    return {**base, "id": r.id, "state": r.state, "engine_version": r.engine_version, "orientation": r.orientation or {},
            "stats": r.stats or {}, "approved_stats": r.approved_stats or {}, "file_ids": r.file_ids or [],
            "approved_file_ids": r.approved_file_ids or [], "previews": pv, "problems": r.problems or [],
            "error": r.error, "has_approved": bool(r.approved_file_ids),
            "candidate_differs": bool(r.approved_file_ids) and r.file_ids != r.approved_file_ids,
            "sliced_at": r.sliced_at.isoformat() if r.sliced_at else None,
            "approved_at": r.approved_at.isoformat() if r.approved_at else None, "approved_by": r.approved_by,
            "requested_by": r.requested_by, "locked": bool(r.locked), "source": r.source or "hub", **extra}


def _client():
    try:
        return slicing.client()
    except ApiError as e:
        raise HTTPException(503, e.message)


def _err(e: ApiError):
    raise HTTPException(502 if (e.status or 500) >= 500 else 400, e.message)


# ---------- the slicer service ----------

@router.get("/slicer/status")
def status(db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    from ..clients import slicer_configured
    from ..services.connections import is_connected
    profs = [prof_dict(p) for p in db.execute(select(PrinterProfile).order_by(PrinterProfile.name)).scalars()]
    if not slicer_configured():
        return {"configured": False, "profiles": profs, "source": None}
    try:
        h = _client().health()
    except (ApiError, HTTPException) as e:
        h = {"ok": False, "error": getattr(e, "message", None) or getattr(e, "detail", str(e))}
    eng = h.get("engines") or ({"orca": h["orca"]} if h.get("orca") else {})
    return {"configured": True, "health": h, "engines": eng,
            "engine_labels": {k: slicing.label(k) for k in eng},
            "source": "team" if is_connected("slicer") else "shared", "profiles": profs}


@router.get("/slicer/machines")
def machines(q: str = "", engine: str = "orca", _: Principal = Depends(need("view"))):
    try:
        return _client().machines(q, engine)[:300]
    except ApiError as e:
        _err(e)


@router.get("/slicer/presets/{kind}")
def presets(kind: str, machine: str, engine: str = "orca", filament_type: str | None = None, process: str | None = None,
            _: Principal = Depends(need("view"))):
    try:
        return _client().presets(kind, machine, filament_type, engine=engine, process=process)
    except ApiError as e:
        _err(e)


@router.get("/slicer/machine-defaults")
def machine_defaults(machine: str, engine: str = "orca", _: Principal = Depends(need("view"))):
    """What the setup wizard pre-fills for a printer preset: tool count, nozzles, default process."""
    entry = slicing.catalog_entry(machine, engine)
    if not entry:
        raise HTTPException(404, f"{slicing.label(engine)} has no printer preset {machine!r}")
    return {"entry": entry, "tools": slicing.default_tools(entry, machine), "process": entry.get("default_process")}


@router.get("/slicer/discover")
def discover(_: Principal = Depends(need("view"))):
    return slicing.discover_printers()


# ---------- printers ----------

class ProfileIn(BaseModel):
    name: str | None = None
    engine: str | None = None
    machine_preset: str | None = None
    machine_config: str | None = None
    process_preset: str | None = None
    tools: list[dict] | None = None
    filament_presets: dict | None = None
    machine_overrides: dict | None = None
    process_overrides: dict | None = None
    use_model_settings: bool | None = None
    active: bool | None = None
    is_default: bool | None = None
    dispatch: str | None = None
    farm_printer: str | None = None
    hourly_rate: float | None = None
    bed_type: str | None = None
    filament_slots: int | None = None
    notes: str | None = None


def _imported_base(engine: str, text: str) -> str | None:
    """The printer preset an imported config builds on (its `inherits`), or its own name."""
    if engine == "orca":
        try:
            d = json.loads(text)
        except ValueError:
            raise HTTPException(400, "an OrcaSlicer printer preset is a JSON file (Printer settings > export)")
        if not isinstance(d, dict) or d.get("type") not in (None, "machine"):
            raise HTTPException(400, "that JSON is not an OrcaSlicer printer preset")
        return d.get("inherits") or d.get("name")
    m = re.search(r"^\s*inherits\s*=\s*(.+?)\s*$", text, re.M)
    if not re.search(r"^\s*\w+\s*=", text, re.M):
        raise HTTPException(400, "a PrusaSlicer printer preset is an INI file (File > Export > Export Config)")
    return m.group(1).split(";")[0].strip() if m else None


def _apply(db, p: PrinterProfile, body: ProfileIn):
    data = body.model_dump(exclude_unset=True)
    if "engine" in data and data["engine"] not in slicing.ENGINES:
        raise HTTPException(400, f"engine must be one of {', '.join(slicing.ENGINES)}")
    if data.get("hourly_rate") is not None and data["hourly_rate"] < 0:
        raise HTTPException(400, "the hourly rate can't be negative")
    if data.get("filament_slots") is not None and not 1 <= data["filament_slots"] <= 64:
        raise HTTPException(400, "filament slots must be between 1 and 64")
    if data.get("bed_type") == "":
        data["bed_type"] = None
    if data.get("bed_type") and data["bed_type"] not in slicing.BED_TYPES:
        raise HTTPException(400, f"build plate must be one of {', '.join(slicing.BED_TYPES)}")
    if "dispatch" in data and data["dispatch"] not in ("none", "farm"):
        raise HTTPException(400, "dispatch must be none or farm")
    if "tools" in data:
        try:
            data["tools"] = slicing.clean_tools(data["tools"])
        except ValueError as e:
            raise HTTPException(400, str(e))
    if data.get("machine_config"):
        if len(data["machine_config"]) > 512_000:
            raise HTTPException(400, "printer preset file too large")
        base = _imported_base(data.get("engine") or p.engine or "orca", data["machine_config"])
        if not data.get("machine_preset") and base:
            data["machine_preset"] = base
    for k, v in data.items():
        setattr(p, k, v)
    if not p.machine_preset:
        raise HTTPException(400, "pick a printer preset or import one")
    if data.get("is_default"):
        for other in db.execute(select(PrinterProfile).where(PrinterProfile.id != p.id)).scalars():
            other.is_default = False


@router.post("/printer-profiles")
def create_profile(body: ProfileIn, db: Session = Depends(get_db), _: Principal = Depends(need("slice"))):
    if not body.name:
        raise HTTPException(400, "give the printer a name")
    p = PrinterProfile(name=body.name.strip()[:80], engine=body.engine or "orca", machine_preset="", tools=[])
    db.add(p)
    _apply(db, p, body)
    if not db.execute(select(PrinterProfile).where(PrinterProfile.is_default.is_(True), PrinterProfile.id != p.id)).first():
        p.is_default = True
    if not p.tools:
        slicing.ensure_tools(p)
    db.commit()
    return prof_dict(p)


@router.patch("/printer-profiles/{pid}")
def edit_profile(pid: int, body: ProfileIn, db: Session = Depends(get_db), pr: Principal = Depends(need("slice"))):
    p = db.get(PrinterProfile, pid)
    if not p:
        raise HTTPException(404)
    _apply(db, p, body)
    enqueue(db, "slicing.check_stale", ref=f"stale:profile:{p.id}", payload={"profile_id": p.id}, delay=2, actor=pr.actor)
    db.commit()
    return prof_dict(p)


@router.delete("/printer-profiles/{pid}")
def delete_profile(pid: int, db: Session = Depends(get_db), _: Principal = Depends(need("slice"))):
    p = db.get(PrinterProfile, pid)
    if p:
        db.delete(p)
        db.commit()
    return {"ok": True}


# ---------- slicing a model ----------

class SliceIn(BaseModel):
    profile_id: int | None = None
    profile_ids: list[int] | None = None
    file_id: int | None = None
    layer_height: float | None = None
    process: str | None = None
    process_overrides: dict | None = None
    copies: int = 1
    orientation: dict | None = None
    approve: bool = False
    make_production: bool = False      # older clients: same as approve
    ignore_designer: bool = False
    ground_largest_face: bool = False  # older clients: orientation largest_face


def _profile_ids(db, body: SliceIn) -> list[int]:
    if body.profile_ids:
        ids = [i for i in body.profile_ids if db.get(PrinterProfile, i)]
        if not ids:
            raise HTTPException(400, "unknown printer")
        return ids
    if body.profile_id:
        if not db.get(PrinterProfile, body.profile_id):
            raise HTTPException(400, "unknown printer")
        return [body.profile_id]
    d = slicing.default_profile(db)
    if not d:
        raise HTTPException(400, "add a printer in Settings > Printers first")
    return [d.id]


def _opts(body: SliceIn) -> dict:
    o = body.model_dump(exclude={"profile_id", "profile_ids", "ground_largest_face", "make_production"}, exclude_none=True)
    if body.ground_largest_face and not body.orientation:
        o["orientation"] = {"mode": "largest_face"}
    if body.make_production:
        o["approve"] = True
    if o.get("orientation") and o["orientation"].get("mode") not in slicing.ORIENT_MODES:
        raise HTTPException(400, "orientation mode must be as_is, largest_face, auto or face")
    if not o.get("approve"):
        o.pop("approve", None)
    return o


def _queue(db, model_id: int, pid: int, opts: dict, actor: str, priority: int | None = None):
    r = slicing.result_for(db, model_id, pid)
    if r.state != "slicing":
        r.state = "queued"
    j = enqueue(db, "slice.model", ref=f"model:{model_id}:slice:{pid}", payload={"model_id": model_id, "profile_id": pid,
                                                                                   "opts": opts}, actor=actor, max_attempts=2,
                priority=priority)
    r.job_id = j.id
    return j


@router.get("/models/{model_id}/slicing")
def model_slicing(model_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404)
    profs = list(db.execute(select(PrinterProfile).where(PrinterProfile.active.is_(True))
                            .order_by(PrinterProfile.is_default.desc(), PrinterProfile.name)).scalars())
    rs = {r.profile_id: r for r in db.execute(select(SliceResult).where(SliceResult.model_id == m.id)).scalars()}
    try:
        srcs = [{"id": f.id, "filename": f.filename} for f in slicing.choose_sources(m)]
    except ValueError:
        srcs = []
    insp = m.inspect or {}
    return {"model_id": m.id, "colors_needed": slicing.colors_needed(m), "sources": srcs,
            "faces": insp.get("faces") or [], "size_mm": insp.get("size_mm"), "inspected_at": insp.get("inspected_at"),
            "printers": [{**result_dict(rs.get(p.id), p, m, db), "is_default": p.is_default, "tools": len(p.tools or []) or 1}
                         for p in profs],
            "can_print_now": _can_print_now()}


def _can_print_now() -> bool:
    from ..services import farm
    return farm.can_pin()


class PrintNowIn(BaseModel):
    profile_id: int
    quantity: int = 1
    start_now: bool = False       # the bed is clear: start it right away when the printer is free


@router.post("/models/{model_id}/print")
def print_now(model_id: int, body: PrintNowIn, db: Session = Depends(get_db), who: Principal = Depends(need("farm"))):
    """Print a model now on one printer: its approved slice goes to the farm pinned to that printer."""
    from ..services import farm
    from .farm import _actor
    m = db.get(Model, model_id)
    prof = db.get(PrinterProfile, body.profile_id)
    if not m or not prof:
        raise HTTPException(404, "model or printer not found")
    from ..services import plates as plates_svc, printer_access
    if not printer_access.may_use(db, who, plates_svc.farm_printer_name(prof)):
        raise HTTPException(404, "model or printer not found")
    try:
        out = farm.print_now(db, m, prof, _actor(who), body.quantity, start=body.start_now)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ApiError as e:
        raise HTTPException(502, f"The farm said: {getattr(e, 'message', None) or e}")
    db.commit()
    return out


# ---------- choosing files by hand, locks, removal ----------

class UseFileIn(BaseModel):
    file_id: int
    lock: bool = True


def _model_prof(db, model_id: int, profile_id: int):
    m, prof = db.get(Model, model_id), db.get(PrinterProfile, profile_id)
    if not m or not prof:
        raise HTTPException(404, "model or printer not found")
    return m, prof


def _after_use(db, m: Model, prof: PrinterProfile, out: dict, actor: str, what: str):
    from ..jobs import event
    from ..tasks import _after_approve
    event(db, f"model:{m.id}", f"{what} for {prof.name}" + (" (locked)" if out.get("locked") else ""), actor=actor)
    if out.get("bom_updated"):
        enqueue(db, "store.sync_stock", ref="stock:all", delay=5)
    _after_approve(db, m, out)


@router.post("/models/{model_id}/printers/{profile_id}/use-file")
def use_file(model_id: int, profile_id: int, body: UseFileIn, db: Session = Depends(get_db),
             p: Principal = Depends(need("slice"))):
    """Print one of the model's sliced files on this printer: approved now, locked unless lock is false."""
    from ..models import ModelFile
    m, prof = _model_prof(db, model_id, profile_id)
    f = db.get(ModelFile, body.file_id)
    if not f or f.model_id != m.id:
        raise HTTPException(404, "file not found on this model")
    try:
        out = slicing.use_file(db, m, prof, f, p.actor, body.lock)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _after_use(db, m, prof, out, p.actor, f"Chose {f.filename}")
    db.commit()
    return out


@router.post("/models/{model_id}/printers/{profile_id}/upload")
async def upload_for_printer(model_id: int, profile_id: int, file: UploadFile = File(...), lock: bool = Form(True),
                             db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    """Upload a file sliced elsewhere (Bambu Studio, Orca on a desktop) and make it the one this printer prints."""
    import shutil
    import tempfile
    from pathlib import Path
    from ..config import settings
    from ..services import importer
    from ..services import images as imgsvc
    m, prof = _model_prof(db, model_id, profile_id)
    name = Path(file.filename or "").name
    if not name.lower().endswith((".gcode", ".3mf", ".bgcode")):
        raise HTTPException(400, "upload a sliced .3mf or .gcode file")
    with tempfile.TemporaryDirectory(dir=settings().data_dir) as td:
        dst = Path(td) / name
        with open(dst, "wb") as out_f:
            shutil.copyfileobj(file.file, out_f)
        had = {f.sha256: f for f in m.files}
        fresh = imgsvc.sha256_file(dst) not in had
        mf = importer.add_model_file(db, m, dst, had)
    m.library_rel = importer.library_folder(m)
    try:
        out = slicing.use_file(db, m, prof, mf, p.actor, lock)
    except ValueError as e:
        if fresh and mf.library_rel and not any(f.library_rel == mf.library_rel for f in m.files if f.id != mf.id):
            (Path(settings().library_root) / mf.library_rel).unlink(missing_ok=True)
        db.rollback()
        raise HTTPException(400, str(e))
    _after_use(db, m, prof, out, p.actor, f"Uploaded {mf.filename}")
    enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=30)
    db.commit()
    return {**out, "file_id": mf.id}


class LockIn(BaseModel):
    locked: bool


@router.post("/slice-results/{rid}/lock")
def lock_result(rid: int, body: LockIn, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    from ..jobs import event
    r = _result(db, rid)
    try:
        out = slicing.set_lock(db, r, body.locked)
    except ValueError as e:
        raise HTTPException(400, str(e))
    prof = db.get(PrinterProfile, r.profile_id)
    event(db, f"model:{r.model_id}", f"{'Locked' if body.locked else 'Unlocked'} the slice for {prof.name}", actor=p.actor)
    db.commit()
    return out


@router.delete("/slice-results/{rid}")
def remove_result(rid: int, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    """Remove a printer's slice: the printer goes back to "not sliced"."""
    from ..jobs import event
    r = _result(db, rid)
    m = db.get(Model, r.model_id)
    out = slicing.remove_result(db, r)
    event(db, f"model:{m.id}", f"Removed the slice for {out['printer']}", actor=p.actor)
    if out.get("production_cleared") and m.product and m.product.woo_product_id:
        enqueue(db, "farm.sync_product", ref=f"product:{m.product.id}", payload={"product_id": m.product.id}, delay=2)
    db.commit()
    return out


@router.get("/models/{model_id}/slice-preview")
def slice_preview(model_id: int, profile_id: int | None = None, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404)
    p = db.get(PrinterProfile, _profile_ids(db, SliceIn(profile_id=profile_id))[0])
    try:
        srcs = slicing.choose_sources(m)
        spec = slicing.build_spec(db, m, p, {})
    except (ValueError, ApiError) as e:
        raise HTTPException(400, str(getattr(e, "message", e)))
    spec.pop("machine_config", None)
    return {"profile": prof_dict(p), "sources": [{"id": f.id, "filename": f.filename} for f in srcs], "spec": spec}


@router.post("/models/{model_id}/slice")
def slice_one(model_id: int, body: SliceIn, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    if not db.get(Model, model_id):
        raise HTTPException(404)
    opts = _opts(body)
    jobs = [_queue(db, model_id, pid, opts, p.actor).id for pid in _profile_ids(db, body)]
    db.commit()
    return {"job": jobs[0], "jobs": jobs}


class BulkSliceIn(SliceIn):
    model_ids: list[int]


@router.post("/models/bulk-slice")
def slice_many(body: BulkSliceIn, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    pids = _profile_ids(db, body)
    opts = _opts(body)
    opts.pop("file_id", None)
    n = 0
    for mid in body.model_ids[:2000]:
        if not db.get(Model, mid):
            continue
        for pid in pids:
            _queue(db, mid, pid, opts, p.actor, priority=6)
            n += 1
    db.commit()
    return {"queued": n}


@router.post("/models/{model_id}/inspect")
def inspect(model_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    if not db.get(Model, model_id):
        raise HTTPException(404)
    j = enqueue(db, "slice.inspect", ref=f"model:{model_id}:inspect", payload={"model_id": model_id}, actor=p.actor)
    db.commit()
    return {"job": j.id}


# ---------- review ----------

def _result(db, rid: int) -> SliceResult:
    r = db.get(SliceResult, rid)
    if not r:
        raise HTTPException(404)
    return r


@router.post("/slice-results/{rid}/approve")
def approve(rid: int, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    from ..tasks import _after_approve
    r = _result(db, rid)
    try:
        out = slicing.approve(db, r, p.actor)
    except ValueError as e:
        raise HTTPException(400, str(e))
    m = db.get(Model, r.model_id)
    from ..jobs import event
    event(db, f"model:{m.id}", f"Approved the slice for {db.get(PrinterProfile, r.profile_id).name}", actor=p.actor)
    if out.get("bom_updated"):
        enqueue(db, "store.sync_stock", ref="stock:all", delay=5)
    _after_approve(db, m, out)
    db.commit()
    return out


@router.post("/slice-results/{rid}/reject")
def reject(rid: int, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    r = _result(db, rid)
    out = slicing.reject(db, r, p.actor)
    db.commit()
    return out


class ResultIn(BaseModel):
    orientation: dict | None = None


@router.patch("/slice-results/{rid}")
def edit_result(rid: int, body: ResultIn, db: Session = Depends(get_db), _: Principal = Depends(need("slice"))):
    r = _result(db, rid)
    if body.orientation is not None:
        if body.orientation.get("mode", "as_is") not in slicing.ORIENT_MODES:
            raise HTTPException(400, "orientation mode must be as_is, largest_face, auto or face")
        r.orientation = body.orientation
    db.commit()
    return {"ok": True}


@router.get("/slice-results/{rid}/preview/{name}")
def preview(rid: int, name: str, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    r = _result(db, rid)        # tenant-scoped: another team's result is a 404
    if name not in (r.previews or []) or not re.fullmatch(r"preview_plate_\d+\.png", name):
        raise HTTPException(404)
    path = slicing.preview_dir(r.id) / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


# ---------- across the catalog ----------

@router.get("/slicing/grid")
def grid(q: str = "", state: str = "", limit: int = 100, offset: int = 0, db: Session = Depends(get_db),
         _: Principal = Depends(need("view"))):
    """Models down, printers across, slice state in each cell."""
    profs = list(db.execute(select(PrinterProfile).where(PrinterProfile.active.is_(True))
                            .order_by(PrinterProfile.is_default.desc(), PrinterProfile.name)).scalars())
    mq = select(Model).where(Model.status != "archived")
    if q:
        like = f"%{q}%"
        mq = mq.where(or_(Model.name.ilike(like), Model.code.ilike(like)))
    if state:
        sub = select(SliceResult.model_id).where(SliceResult.state == state)
        mq = mq.where(Model.id.in_(sub)) if state != "none" else mq.where(~Model.id.in_(select(SliceResult.model_id)))
    total = db.execute(select(func.count()).select_from(mq.subquery())).scalar()
    models = list(db.execute(mq.order_by(Model.code).limit(min(limit, 500)).offset(offset)
                             .options(selectinload(Model.product).selectinload(Product.filaments))).scalars())
    rs = {}
    if models:
        for r in db.execute(select(SliceResult).where(SliceResult.model_id.in_([m.id for m in models]))).scalars():
            rs[(r.model_id, r.profile_id)] = r
    rows = []
    for m in models:
        cells = []
        for p in profs:
            r = rs.get((m.id, p.id))
            st = (r.stats or {}) if r else {}
            cost = slicing.cost_of(db, m, p, st) if r else None
            cells.append({"profile_id": p.id, "id": r.id if r else None, "state": r.state if r else "none",
                          "time_s": st.get("time_s"), "grams": st.get("per_unit_g") or st.get("grams"),
                          "cost": cost["total"] if cost else None,
                          "problems": len(r.problems or []) if r else len(slicing.problems_for(m, p)),
                          "error": (r.error or "")[:200] if r else None})
        rows.append({"id": m.id, "code": m.code, "name": m.name, "colors_needed": slicing.colors_needed(m),
                     "has_product": bool(m.product), "cells": cells})
    counts = dict(db.execute(select(SliceResult.state, func.count()).group_by(SliceResult.state)).all())
    return {"printers": [{"id": p.id, "name": p.name, "engine": slicing.engine_of(p), "is_default": p.is_default,
                          "tools": len(p.tools or []) or 1} for p in profs],
            "rows": rows, "total": total, "counts": counts}


@router.get("/slicing/problems")
def problems(db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    """Everything that needs a person: failed and stale slices, slices waiting for review, tool and material mismatches,
    and products whose default printer has no approved file."""
    out = []
    profs = {p.id: p for p in db.execute(select(PrinterProfile)).scalars()}
    # slices that are only waiting for review live on the Review tab, not here
    q = select(SliceResult).where(or_(SliceResult.state.in_(("failed", "stale")),
                                      func.json_array_length(SliceResult.problems) > 0))
    for r in db.execute(q.limit(1000)).scalars():
        m, p = db.get(Model, r.model_id), profs.get(r.profile_id)
        if not m or not p:
            continue
        kind = r.state if r.state in ("failed", "stale") else "problem"
        msg = {"failed": r.error or "slicing failed", "stale": "something it was sliced from changed; re-slice",
               "review": "sliced; waiting for review"}.get(kind) or "; ".join(x["msg"] for x in r.problems or [])
        out.append({"kind": kind, "model_id": m.id, "code": m.code, "name": m.name, "profile_id": p.id, "printer": p.name,
                    "result_id": r.id, "message": msg[:400],
                    "details": [x["msg"] for x in (r.problems or [])]})
    d = slicing.default_profile(db)
    if d:
        approved = select(SliceResult.model_id).where(SliceResult.profile_id == d.id, func.json_array_length(SliceResult.approved_file_ids) > 0)
        # live on the store but nothing approved to print: an order would have no file
        for m in db.execute(select(Model).join(Product, Product.model_id == Model.id)
                            .where(Model.status != "archived", Product.live.is_(True), ~Model.id.in_(approved)).limit(500)).scalars():
            out.append({"kind": "unsliced", "model_id": m.id, "code": m.code, "name": m.name, "profile_id": d.id,
                        "printer": d.name, "result_id": None,
                        "message": f"live on the store, but no approved file for {d.name} (the default printer)",
                        "details": []})
    order = {"failed": 0, "stale": 1, "problem": 2, "review": 3, "unsliced": 4}
    out.sort(key=lambda x: (order.get(x["kind"], 9), x["code"] or ""))
    return {"items": out, "counts": {k: sum(1 for x in out if x["kind"] == k) for k in order}}


class ResliceIn(BaseModel):
    states: list[str] = ["stale", "failed"]
    profile_id: int | None = None


@router.post("/slicing/reslice")
def reslice(body: ResliceIn, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    states = [s for s in body.states if s in ("stale", "failed", "review", "approved")]
    q = select(SliceResult).where(SliceResult.state.in_(states))
    if body.profile_id:
        q = q.where(SliceResult.profile_id == body.profile_id)
    n = 0
    for r in db.execute(q.limit(2000)).scalars():
        opts = dict((r.spec or {}).get("_opts") or {})
        opts.pop("approve", None)
        _queue(db, r.model_id, r.profile_id, opts, p.actor, priority=6)
        n += 1
    db.commit()
    return {"queued": n}


@router.post("/slicing/check-stale")
def check_stale(db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    j = enqueue(db, "slicing.check_stale", ref="stale:all", actor=p.actor)
    db.commit()
    return {"job": j.id}


# ---------- real prints ----------

class PrintIn(BaseModel):
    outcome: str
    duration_min: float | None = None
    printer_name: str | None = None
    note: str | None = None


@router.post("/slice-results/{rid}/prints")
def add_print(rid: int, body: PrintIn, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    r = _result(db, rid)
    try:
        rec = slicing.record_print(db, r, r.model_id, body.outcome, printer_name=body.printer_name or db.get(PrinterProfile, r.profile_id).name,
                                   duration_s=int(body.duration_min * 60) if body.duration_min else None, note=body.note,
                                   actor=p.actor)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"id": rec.id, "prints": slicing.print_summary(db, r)}


@router.delete("/prints/{pid}")
def delete_print(pid: int, db: Session = Depends(get_db), _: Principal = Depends(need("slice"))):
    from ..models import PrintRecord
    rec = db.get(PrintRecord, pid)
    if rec:
        r = db.get(SliceResult, rec.result_id) if rec.result_id else None
        db.delete(rec)
        db.flush()
        if r:
            slicing.refresh_print_problems(db, r)
        db.commit()
    return {"ok": True}


# ---------- preset conversion ----------

class ConvertIn(BaseModel):
    ini: str
    base: str | None = None
    keep_gcode: bool = False


@router.post("/slicer/convert-ini")
def convert_ini(body: ConvertIn, _: Principal = Depends(need("slice"))):
    """A PrusaSlicer printer preset (.ini) as an OrcaSlicer printer preset that builds on `base`."""
    from ..services import preset_convert
    if len(body.ini) > 512_000:
        raise HTTPException(400, "printer preset file too large")
    try:
        return preset_convert.prusa_ini_to_orca(body.ini, body.base, keep_gcode=body.keep_gcode)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- review queue ----------

@router.get("/slicing/review")
def review_queue(profile_id: int | None = None, problems: str = "", q: str = "", limit: int = 48, offset: int = 0,
                 db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    """Slices waiting for a person, oldest first, with what is needed to judge them at a glance."""
    from ..serialize import hero, img_url
    cond = or_(SliceResult.state == "review",
               (SliceResult.state == "stale") & (func.json_array_length(SliceResult.file_ids) > 0))
    qr = select(SliceResult).join(Model, Model.id == SliceResult.model_id).where(cond)
    if profile_id:
        qr = qr.where(SliceResult.profile_id == profile_id)
    if q:
        like = f"%{q}%"
        qr = qr.where(or_(Model.name.ilike(like), Model.code.ilike(like)))
    if problems == "none":
        qr = qr.where(func.json_array_length(SliceResult.problems) == 0)
    elif problems == "some":
        qr = qr.where(func.json_array_length(SliceResult.problems) > 0)
    total = db.execute(select(func.count()).select_from(qr.subquery())).scalar()
    rows = list(db.execute(qr.order_by(SliceResult.sliced_at.asc().nulls_last(), SliceResult.id)
                           .limit(min(limit, 200)).offset(offset)).scalars())
    profs = {p.id: p for p in db.execute(select(PrinterProfile)).scalars()}
    items = []
    for r in rows:
        m, p = db.get(Model, r.model_id), profs.get(r.profile_id)
        if not m or not p:
            continue
        if r.state == "stale" and r.file_ids == (r.approved_file_ids or []):
            continue
        st = r.stats or {}
        h = hero(m)
        items.append({"id": r.id, "state": r.state, "model_id": m.id, "code": m.code, "name": m.name,
                      "photo": img_url(h, "thumb") if h else None, "printer": p.name, "profile_id": p.id,
                      "engine": slicing.engine_of(p), "previews": [f"/api/slice-results/{r.id}/preview/{n}" for n in (r.previews or [])],
                      "time_s": st.get("time_s"), "per_unit_g": st.get("per_unit_g"), "copies": st.get("copies"),
                      "plates": st.get("plates"), "tools": st.get("tools") or [], "tower_g": st.get("tower_g"),
                      "process": st.get("process"), "warnings": st.get("warnings") or [], "problems": r.problems or [],
                      "cost": slicing.cost_of(db, m, p, st),
                      "replaces": {"time_s": (r.approved_stats or {}).get("time_s"), "per_unit_g": (r.approved_stats or {}).get("per_unit_g")}
                      if r.approved_file_ids else None,
                      "sliced_at": r.sliced_at.isoformat() if r.sliced_at else None,
                      "colors_needed": slicing.colors_needed(m)})
    queued = db.execute(select(func.count()).select_from(SliceResult)
                        .where(SliceResult.state.in_(("queued", "slicing")))).scalar()
    return {"items": items, "total": total, "queued": queued,
            "printers": [{"id": p.id, "name": p.name} for p in profs.values() if p.active]}


class BulkResultsIn(BaseModel):
    ids: list[int]


@router.post("/slice-results/bulk-approve")
def bulk_approve(body: BulkResultsIn, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    from ..tasks import _after_approve
    from ..jobs import event
    ok, skipped, bom = 0, [], False
    for rid in body.ids[:500]:
        r = db.get(SliceResult, rid)
        if not r:
            continue
        try:
            out = slicing.approve(db, r, p.actor)
        except ValueError as e:
            skipped.append({"id": rid, "reason": str(e)})
            continue
        m = db.get(Model, r.model_id)
        _after_approve(db, m, out)
        bom = bom or bool(out.get("bom_updated"))
        ok += 1
    if ok:
        event(db, "slicing", f"Approved {ok} slice(s) in bulk", actor=p.actor)
    if bom:
        enqueue(db, "store.sync_stock", ref="stock:all", delay=5)
    db.commit()
    return {"approved": ok, "skipped": skipped}


@router.post("/slice-results/bulk-reject")
def bulk_reject(body: BulkResultsIn, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    n = 0
    for rid in body.ids[:500]:
        r = db.get(SliceResult, rid)
        if r and r.state in ("review", "stale"):
            slicing.reject(db, r, p.actor)
            n += 1
    db.commit()
    return {"discarded": n}


class SliceAllIn(BaseModel):
    profile_id: int | None = None
    only_unsliced: bool = True


@router.post("/slicing/slice-all")
def slice_all(body: SliceAllIn, db: Session = Depends(get_db), p: Principal = Depends(need("slice"))):
    """Queue every active model that has something to slice for one printer (default: the default printer)."""
    pid = body.profile_id or (slicing.default_profile(db).id if slicing.default_profile(db) else None)
    if not pid or not db.get(PrinterProfile, pid):
        raise HTTPException(400, "add a printer first")
    have = set(db.execute(select(SliceResult.model_id).where(SliceResult.profile_id == pid)).scalars()) if body.only_unsliced else set()
    n = skipped = 0
    for m in db.execute(select(Model).where(Model.status != "archived")).scalars():
        if m.id in have:
            continue
        if slicing.skips_on(db, m, db.get(PrinterProfile, pid)):
            skipped += 1
            continue
        try:
            slicing.choose_sources(m)
        except ValueError:
            skipped += 1
            continue
        _queue(db, m.id, pid, {}, p.actor, priority=8)
        n += 1
    db.commit()
    return {"queued": n, "nothing_to_slice": skipped}


# ---------- requested filaments and the team's brand ----------

@router.get("/filaments/catalog")
def filament_catalog(_: Principal = Depends(need("view"))):
    from ..services import filaments
    return [c for c in filaments.bambu_catalog() if not c["clear"]]


@router.get("/models/{model_id}/filaments")
def model_filaments(model_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404)
    ex = m.extra or {}
    pr = slicing.production_result(db, m)
    prod = None
    if pr is not None:
        pp = db.get(PrinterProfile, pr.profile_id)
        f = next((x for x in m.files if x.id == pr.approved_file_ids[0]), None)
        prod = {"printer": pp.name if pp else None, "source": pr.source or "hub", "file": f.filename if f else None}
    return {"request": ex.get("filament_request"), "applied": ex.get("filament_applied"),
            "brand": slicing.cfg(db, "filament_brand"), "production": prod}


@router.get("/models/{model_id}/filament-sets")
def filament_sets(model_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    """Each printer's approved slice: the filament it prints, how many could be made with it from what is on hand,
    and whether its colours differ from production (the default printer's slice, which is the materials list)."""
    from types import SimpleNamespace
    from ..services import availability
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404)
    prod = m.product
    pr = slicing.production_result(db, m)
    prod_set = slicing.filament_set(db, pr, m) if pr else []

    def sig(fs):
        return sorted((slicing.material_type(x["material"]), x.get("color_hex") or "") for x in fs)
    snap = availability.load_snapshot(db) if prod else None
    waste = {b.slot: b.waste_pct for b in (prod.filaments if prod else [])}
    out = []
    rs = db.execute(select(SliceResult).where(SliceResult.model_id == m.id)).scalars()
    for r in sorted(rs, key=lambda r: (pr is None or r.id != pr.id, r.profile_id)):
        if not r.approved_file_ids:
            continue
        p = db.get(PrinterProfile, r.profile_id)
        fs = slicing.filament_set(db, r, m)
        can = None
        if prod is not None and fs:
            lines = [SimpleNamespace(slot=x["slot"], grams=x["per_unit_g"], waste_pct=waste.get(x["slot"], 5), follows_variant=False,
                                     material=x["material"], color_name=x.get("color_name"), color_hex=x.get("color_hex"),
                                     spoolman_filament_id=None) for x in fs]
            can = availability.buildable(snap, SimpleNamespace(parts=prod.parts, filaments=lines), None)
        names = [f.filename for f in m.files if f.id in set(r.approved_file_ids)]
        out.append({"profile_id": r.profile_id, "printer": p.name if p else None, "production": pr is not None and r.id == pr.id,
                    "source": r.source or "hub", "locked": bool(r.locked), "state": r.state, "files": names, "set": fs,
                    "grams": round(sum(x["per_unit_g"] for x in fs), 2), "units": can["units"] if can else None,
                    "limiting": can["limiting"] if can else None,
                    "differs": bool(prod_set) and pr is not None and r.id != pr.id and sig(fs) != sig(prod_set)})
    production = None
    if pr is not None:
        pp = db.get(PrinterProfile, pr.profile_id)
        production = {"printer": pp.name if pp else None, "source": pr.source or "hub"}
    return {"production": production, "sets": out}


@router.post("/models/{model_id}/filaments/match")
def match_model_filaments(model_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("models"))):
    from ..services import filaments
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404)
    brand = slicing.cfg(db, "filament_brand")
    out = filaments.apply_brand(db, m, brand, refresh=True) if brand else {"request": filaments.document(db, m, refresh=True)}
    pr = slicing.production_result(db, m)
    if pr is not None and m.product is not None:     # what production really prints is the materials list
        slicing.refresh_file_result(db, pr, m)
        if slicing.sync_bom(db, m, pr):
            out["bom_updated"] = True
            enqueue(db, "store.sync_stock", ref="stock:all", delay=5)
    db.commit()
    return {**out, **model_filaments(model_id, db, p)}


class SlotChoiceIn(BaseModel):
    family: str
    name: str


@router.put("/models/{model_id}/filaments/{slot}")
def choose_filament(model_id: int, slot: int, body: SlotChoiceIn, db: Session = Depends(get_db), p: Principal = Depends(need("models"))):
    """Pick a different Bambu Lab colour for one slot by hand (the designer's request is kept as it was)."""
    from ..services import filaments
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404)
    pr = slicing.production_result(db, m)
    if pr is not None and pr.source == "file":
        raise HTTPException(400, "the colours come from the file chosen for the default printer; choose another file "
                                 "(or remove that slice) to pick colours here")
    c = next((c for c in filaments.bambu_catalog() if c["family"] == body.family and c["name"] == body.name), None)
    if not c:
        raise HTTPException(400, "not in the Bambu Lab catalog")
    req = filaments.document(db, m)
    s = next((x for x in req["slots"] if x["slot"] == slot), None)
    if not s:
        s = {"slot": slot, "brand": None, "material": c["family"], "finish": [], "name": None, "hex": None, "hex_source": None}
        req["slots"].append(s)
    s["bambu"] = {"family": c["family"], "fila_id": c["fila_id"], "name": c["name"], "code": c["code"], "hex": c["hex"],
                  "delta_e": round(filaments.delta_e(s["hex"], c["hex"]), 1) if s.get("hex") else None,
                  "why": f"chosen by {p.actor}"}
    ex = dict(m.extra or {})
    ex["filament_request"] = req
    m.extra = ex
    out = filaments.apply_brand(db, m, slicing.cfg(db, "filament_brand") or "Bambu Lab")
    db.commit()
    return out


@router.post("/filaments/match-all")
def match_all(db: Session = Depends(get_db), p: Principal = Depends(need("models"))):
    j = enqueue(db, "filaments.match_all", ref="filaments:match_all", payload={"refresh": True}, actor=p.actor)
    db.commit()
    return {"job": j.id}
