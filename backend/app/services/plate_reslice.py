"""Plate-aware reslicing (design B: the engine dispatches, the hub handles plate holds).

The engine holds a job whose file was sliced for another plate than the one on a printer. Every minute the hub
looks at its pending farm jobs; for one whose only obstacle on some idle printer is the plate, it checks that the
plate on that printer suits the job's filaments, reslices the model from its source for that plate (or reuses the
cached PlateSlice), and points the engine's queue item at the new file pinned to that printer. A reslice with no
problems starts straight away; one with problems waits in review.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FarmJob, Model, ModelFile, PlateSlice, PrinterProfile
from . import plates

log = logging.getLogger(__name__)

PENDING = ("queued", "assigned")
STUCK = timedelta(minutes=30)      # a "slicing" state older than this is retried
_PLATE_REASON = re.compile(r"^needs the .+ \(has .+\)$")


def _now():
    return datetime.now(timezone.utc)


def _state(fj: FarmJob, state: str, **kw) -> None:
    fj.plate_reslice = {"state": state, "at": _now().isoformat(timespec="seconds"), **kw}


def only_plate(row: dict) -> bool:
    """The printer would take this job if it weren't for the plate."""
    reasons = row.get("reasons") or []
    return bool(row.get("plate_mismatch")) and bool(reasons) and all(_PLATE_REASON.match(r) for r in reasons)


def profile_for(db: Session, printer_name: str) -> PrinterProfile | None:
    """The hub profile for a farm printer. Case-insensitive: the farm says "Voron01" where the profile says
    "voron01"."""
    want = (printer_name or "").strip().lower()
    for p in db.execute(select(PrinterProfile).where(PrinterProfile.active.is_(True))).scalars():
        if (p.farm_printer or p.name or "").strip().lower() == want:
            return p
    return None


def opts_for(plate: str) -> dict:
    return {"bed_type": plate}


def cached(db: Session, model_id: int, profile_id: int, plate: str) -> PlateSlice | None:
    return db.execute(select(PlateSlice).where(PlateSlice.model_id == model_id, PlateSlice.profile_id == profile_id,
                                               PlateSlice.plate == plate)).scalar()


def is_current(db: Session, ps: PlateSlice, model: Model, prof: PrinterProfile) -> bool:
    """The approved files still match what would be sliced now (sources, profile, slicer version)."""
    from . import slicing
    if ps.state != "approved" or not ps.approved_file_ids:
        return False
    try:
        h = slicing.inputs_hash(model, prof, opts_for(ps.plate), slicing.engine_version(prof))
    except Exception:  # noqa: BLE001
        return False
    return h == ps.approved_hash


def approve(db: Session, ps: PlateSlice, actor: str) -> None:
    from . import slicing
    model = db.get(Model, ps.model_id)
    old = [i for i in (ps.approved_file_ids or []) if i not in set(ps.file_ids or [])]
    if old and model:
        slicing._retire(db, model, old)
    ps.approved_file_ids = list(ps.file_ids or [])
    ps.approved_hash = ps.inputs_hash
    ps.approved_at = _now()
    ps.state = "approved"
    db.flush()


def pick_file(db: Session, fj: FarmJob, ps: PlateSlice) -> ModelFile | None:
    """The plate slice's file for the same plate of the model the job prints (multi-plate models give one file
    per plate)."""
    ids = list(ps.approved_file_ids or [])
    if not ids:
        return None
    idx = 0
    if len(ids) > 1 and fj.file_ref:
        orig = db.execute(select(ModelFile).where(ModelFile.model_id == fj.model_id,
                                                  ModelFile.farm_file_ref == str(fj.file_ref))).scalar()
        plate_of = ((orig.settings or {}).get("plate_of") if orig else None) or None
        if not plate_of:
            return None
        idx = int(plate_of[0]) - 1
    return db.get(ModelFile, ids[idx]) if idx < len(ids) else None


def swap(db: Session, c, fj: FarmJob, ps: PlateSlice, printer_id: int, printer_name: str, plate_name: str) -> bool:
    """Re-check the printer is still free for this job, then point the queue item at the plate slice's file pinned
    to it. False (and the slice stays cached) when the printer was taken meanwhile."""
    from .shop import ensure_farm_file
    fresh = c.preflight(fj.external_id) or {}
    row = next((r for r in fresh.get("printers") or [] if r.get("printer_id") == printer_id), None)
    if not row or not only_plate(row):
        _state(fj, "waiting_printer", printer=printer_name, plate=ps.plate, plate_slice_id=ps.id,
               msg=f"resliced for {plate_name}; waiting for {printer_name} to be free")
        return False
    f = pick_file(db, fj, ps)
    if not f:
        _state(fj, "failed", plate_slice_id=ps.id, msg="no matching file in the plate slice")
        return False
    ref = ensure_farm_file(f)
    c.swap_job_file(fj.external_id, ref, printer_id)
    fj.file_ref = str(ref)
    fj.printer_name = printer_name
    _state(fj, "swapped", printer=printer_name, plate=ps.plate, plate_slice_id=ps.id,
           msg=f"resliced for {plate_name} on {printer_name}")
    from ..jobs import event
    event(db, f"farmjob:{fj.id}", f"{fj.name or fj.external_id}: resliced for {plate_name} on {printer_name}")
    db.flush()
    return True


def consider(db: Session, c, fj: FarmJob) -> str:
    """Look at one pending farm job; returns what happened (for the scan report)."""
    from ..jobs import enqueue
    st = fj.plate_reslice or {}
    if st.get("state") == "slicing":
        at = datetime.fromisoformat(st["at"]) if st.get("at") else None
        if at and _now() - at < STUCK:
            return "slicing"
    pf = c.preflight(fj.external_id) or {}
    if "job_plate" not in pf:
        return "engine has no plate fields"
    rows = [r for r in pf.get("printers") or [] if only_plate(r)]
    if not rows:
        return "not a plate hold"
    model = db.get(Model, fj.model_id)
    if not model:
        _state(fj, "no_source", msg="the model is not in the hub")
        return "no model"
    types = pf.get("filament_types") or []
    why_not: list[str] = []
    for row in rows:
        prof = profile_for(db, row["name"])
        if not prof:
            why_not.append(f"{row['name']}: no printer profile in the hub")
            continue
        plate = plates.installed(db, prof) or plates.by_slicer_type(db, row.get("has_plate"))
        if not plate:
            why_not.append(f"{row['name']}: the installed plate ({row.get('has_plate')}) is not in the catalog")
            continue
        ok, why = plates.suits(plate, types)
        if not ok:
            why_not.append(why)
            continue
        ptype = plates.slicer_type_for(plate, prof)
        ps = cached(db, model.id, prof.id, ptype)
        if ps and ps.state == "review":
            _state(fj, "review", printer=row["name"], plate=ptype, plate_slice_id=ps.id,
                   msg=f"reslice for {plate.name} needs review: " + "; ".join(p.get("msg", "") for p in ps.problems or []))
            return "review"
        if ps and is_current(db, ps, model, prof):
            return "swapped" if swap(db, c, fj, ps, row["printer_id"], row["name"], plate.name) else "waiting_printer"
        try:
            from . import slicing
            slicing.choose_sources(model)
        except ValueError:
            _state(fj, "no_source", msg="no source file to reslice from (Manyfold)")
            return "no_source"
        enqueue(db, "farm.plate_reslice", ref=f"farmjob:{fj.id}",
                payload={"farm_job_id": fj.id, "profile_id": prof.id, "plate": ptype,
                         "plate_name": plate.name, "printer_id": row["printer_id"], "printer_name": row["name"]})
        _state(fj, "slicing", printer=row["name"], plate=ptype, msg=f"reslicing for {plate.name}")
        return "enqueued"
    _state(fj, "unsuitable", msg="; ".join(why_not) or "no fitted plate suits this job")
    return "unsuitable"


def scan(db: Session, limit: int = 20) -> dict:
    from ..clients import farm, farm_connected
    if not farm_connected():
        return {"skipped": "The farm is not connected"}
    c = farm()
    out: dict[str, int] = {}
    jobs = db.execute(select(FarmJob).where(FarmJob.status.in_(PENDING), FarmJob.model_id.is_not(None))
                      .order_by(FarmJob.id).limit(limit)).scalars().all()
    for fj in jobs:
        try:
            r = consider(db, c, fj)
        except Exception as e:  # noqa: BLE001
            log.warning("plate reslice: job %s: %s", fj.external_id, e)
            r = "error"
        out[r] = out.get(r, 0) + 1
    db.flush()
    return out


def run_reslice(db: Session, payload: dict) -> dict:
    """The farm.plate_reslice task: slice, approve when the slicer reports no problems, then swap."""
    from ..clients import farm
    from . import slicing
    fj = db.get(FarmJob, int(payload["farm_job_id"]))
    prof = db.get(PrinterProfile, int(payload["profile_id"]))
    if not fj or not prof or fj.status not in PENDING:
        return {"skipped": "job no longer waiting"}
    model = db.get(Model, fj.model_id)
    plate = payload["plate"]
    ps = cached(db, model.id, prof.id, plate)
    if not ps:
        ps = PlateSlice(model_id=model.id, profile_id=prof.id, plate=plate, state="queued")
        db.add(ps)
        db.flush()
    try:
        report = slicing._slice_into(db, model, prof, ps, opts_for(plate), "plate reslicer")
    except ValueError as e:
        ps.state, ps.error = "failed", str(e)[:4000]
        _state(fj, "failed", plate=plate, plate_slice_id=ps.id, msg=f"reslice failed: {str(e)[:300]}")
        db.flush()
        return {"failed": str(e)[:300]}
    if ps.problems:
        _state(fj, "review", printer=payload.get("printer_name"), plate=plate, plate_slice_id=ps.id,
               msg=f"reslice for {payload.get('plate_name') or plate} needs review: "
                   + "; ".join(p.get("msg", "") for p in ps.problems))
        db.flush()
        return {"review": ps.id, "problems": ps.problems}
    approve(db, ps, "auto-approve (no problems)")
    swapped = swap(db, farm(), fj, ps, int(payload["printer_id"]), payload.get("printer_name") or "",
                   payload.get("plate_name") or plate)
    return {"plate_slice_id": ps.id, "swapped": swapped, "files": report.get("files")}
