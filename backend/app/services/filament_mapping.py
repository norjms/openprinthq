"""Filament mapping: a waiting farm job whose filament doesn't match what is loaded, and the person's answer.

The farm engine decides what matches (its /filament-matching routes, docs/filament-matching.md there). When the
next job can't start because of filament, the engine marks it unmatched; the hub mirrors it as waiting_filament
and alerts (services/farm.py). The mapping screen (web and phone) shows what the job needs and what each printer
has loaded, and the person picks which loaded filament prints each of the model's filaments.

- Same printer model and, for every filament, the same material group: the engine pins the job to the printer
  with that mapping and it starts.
- Another material, or a printer of another model: the hub reslices the model from its source for that printer
  with the loaded filaments (the plate reslicer's cache, keyed by the filaments), then swaps the file in and maps
  it. A reslice with warnings stops in Slicing review and alerts.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FarmJob, Model, PlateSlice

log = logging.getLogger("hub.filament_mapping")


class MappingError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def farm_job(db: Session, ext_id) -> FarmJob | None:
    return db.execute(select(FarmJob).where(FarmJob.external_id == str(ext_id))).scalar()


def _group(material: str | None) -> str:
    from .slicing import material_type
    return material_type(material)


def options(db: Session, c, ext_id, all_printers: bool = False) -> dict:
    """The engine's options for the job, plus what the hub can do: reslice (a model with a source) and how far
    a reslice for this job has got."""
    out = dict(c.filament_options(ext_id, all_printers=all_printers) or {})
    fj = farm_job(db, ext_id)
    model = db.get(Model, fj.model_id) if fj and fj.model_id else None
    can, why = False, "this job isn't linked to a model in the hub, so it can't be resliced"
    if model:
        try:
            from . import slicing
            slicing.choose_sources(model)
            can, why = True, None
        except ValueError:
            why = "the model has no source file to reslice from"
    for p in out.get("printers") or []:
        reqs = {int(r.get("slot_id") or 0): r for r in out.get("requirements") or []}
        loaded = {t.get("global_tray_id"): t for t in p.get("loaded") or []}
        for s in p.get("slots") or []:
            tray = loaded.get(s.get("suggested"))
            req = reqs.get(int(s.get("slot_id") or 0))
            s["needs_reslice"] = bool(tray and req and _group(req.get("type")) != _group(tray.get("type")))
    st = (fj.plate_reslice or {}) if fj else {}
    out.update({
        "farm_job_id": fj.id if fj else None,
        "model_id": model.id if model else None,
        "model_name": model.name if model else None,
        "can_reslice": can,
        "reslice_unavailable": why,
        "reslice": st if st.get("kind") == "filament" else None,
    })
    return out


def plan(opts: dict, printer_id: int, slots: list[dict]) -> dict:
    """Whether the chosen mapping prints as sliced or needs a reslice, and what each slot would print with."""
    row = next((p for p in opts.get("printers") or [] if p.get("printer_id") == printer_id), None)
    if row is None:
        raise MappingError(404, "That printer can't take this job")
    reqs = {int(r.get("slot_id") or 0): r for r in opts.get("requirements") or []}
    loaded = {t.get("global_tray_id"): t for t in row.get("loaded") or []}
    chosen = {int(s["slot_id"]): int(s["global_tray_id"]) for s in slots}
    missing = [sid for sid in reqs if sid not in chosen]
    if missing:
        raise MappingError(400, f"Choose a loaded filament for filament {missing[0]}")
    used = [g for g in chosen.values() if g >= 0]
    if len(used) != len(set(used)):
        raise MappingError(400, "Each loaded filament can print one of the model's filaments")
    materials, reasons = [], []
    for sid in sorted(reqs):
        tray = loaded.get(chosen[sid])
        if tray is None:
            raise MappingError(400, f"Filament {sid}: that slot has nothing loaded on {row.get('name')}")
        req = reqs[sid]
        if _group(req.get("type")) != _group(tray.get("type")):
            reasons.append(f"filament {sid} prints in {tray.get('type')} instead of {req.get('type')}")
        materials.append({"slot_id": sid, "material": tray.get("type"), "color": tray.get("color")})
    if not row.get("same_model", True):
        reasons.append(f"{row.get('name')} is a {row.get('model')}, the file was sliced for "
                       f"{opts.get('sliced_for_model')}")
    return {"printer": row, "materials": materials, "reslice": bool(reasons), "why": reasons}


def apply(db: Session, c, ext_id, printer_id: int, slots: list[dict], remember: bool, actor: str) -> dict:
    """Map the job's filaments to a printer: start it, or reslice first when the mapping needs it."""
    from ..jobs import enqueue, event
    opts = options(db, c, ext_id, all_printers=True)
    if opts.get("status") not in (None, "pending"):
        raise MappingError(400, "This job is no longer waiting")
    p = plan(opts, printer_id, slots)
    name = opts.get("job_name") or f"job {ext_id}"
    pname = p["printer"].get("name")
    fj = farm_job(db, ext_id)
    if not p["reslice"]:
        c.filament_map(ext_id, printer_id, slots, remember=remember, by=actor)
        if fj:
            fj.printer_name = pname
            fj.plate_reslice = {"state": "mapped", "kind": "filament", "at": _now(), "printer": pname}
        event(db, "farm", f"{name}: filament mapped on {pname}", actor=actor)
        db.flush()
        return {"ok": True, "started": True, "reslice": False, "printer": pname}
    if not opts.get("can_reslice") or not fj:
        raise MappingError(409, "This mapping needs the file sliced again, and " + (opts.get("reslice_unavailable") or
                                                                                   "the job isn't in the hub"))
    from .plate_reslice import profile_for
    prof = profile_for(db, pname)
    if not prof:
        raise MappingError(409, f"{pname} has no printer profile in the hub, so the job can't be resliced for it")
    enqueue(db, "farm.filament_reslice", ref=f"farmjob:{fj.id}:filament",
            payload={"farm_job_id": fj.id, "profile_id": prof.id, "printer_id": printer_id, "printer_name": pname,
                     "slots": [{"slot_id": int(s["slot_id"]), "global_tray_id": int(s["global_tray_id"])}
                               for s in slots],
                     "materials": p["materials"], "remember": bool(remember), "actor": actor})
    fj.plate_reslice = {"state": "slicing", "kind": "filament", "at": _now(), "printer": pname,
                        "msg": "reslicing: " + "; ".join(p["why"])}
    event(db, "farm", f"{name}: reslicing for {pname} ({'; '.join(p['why'])})", actor=actor)
    db.flush()
    return {"ok": True, "started": False, "reslice": True, "printer": pname, "why": p["why"]}


def _key(bed: str | None, materials: list[dict]) -> str:
    sig = hashlib.sha256(repr([(m["material"], m["color"]) for m in materials]).encode()).hexdigest()[:10]
    return f"{(bed or 'std')[:28]}#{sig}"


def run_reslice(db: Session, payload: dict) -> dict:
    """The farm.filament_reslice task: slice for the printer with the loaded filaments, approve when clean,
    swap the file in and map it."""
    from ..clients import farm
    from ..models import PrinterProfile
    from . import slicing
    from .alerts import notify
    from .plate_reslice import approve, pick_file
    from .shop import ensure_farm_file
    fj = db.get(FarmJob, int(payload["farm_job_id"]))
    prof = db.get(PrinterProfile, int(payload["profile_id"]))
    if not fj or not prof or fj.status not in ("queued", "assigned", "waiting_filament"):
        return {"skipped": "job no longer waiting"}
    model = db.get(Model, fj.model_id) if fj.model_id else None
    if not model:
        return {"skipped": "no model"}
    materials = payload.get("materials") or []
    key = _key(prof.bed_type, materials)
    ps = db.execute(select(PlateSlice).where(PlateSlice.model_id == model.id, PlateSlice.profile_id == prof.id,
                                             PlateSlice.plate == key)).scalar()
    if not ps:
        ps = PlateSlice(model_id=model.id, profile_id=prof.id, plate=key, state="queued")
        db.add(ps)
        db.flush()
    opts = {"filament_materials": [{"material": m["material"], "color": m["color"]} for m in materials]}
    if prof.bed_type:
        opts["bed_type"] = prof.bed_type
    printer = payload.get("printer_name") or prof.name
    try:
        slicing._slice_into(db, model, prof, ps, opts, "filament mapping")  # noqa: SLF001
    except ValueError as e:
        ps.state, ps.error = "failed", str(e)[:4000]
        fj.plate_reslice = {"state": "failed", "kind": "filament", "at": _now(), "printer": printer,
                            "plate_slice_id": ps.id, "msg": f"reslice failed: {str(e)[:300]}"}
        notify(db, "waiting_filament", f"Reslice failed: {fj.name}", str(e)[:300],
               link=_link(fj), ref=f"alert:fr:{fj.id}:{ps.id}")
        db.flush()
        return {"failed": str(e)[:300]}
    if ps.problems:
        msg = "; ".join(p.get("msg", "") for p in ps.problems)
        fj.plate_reslice = {"state": "review", "kind": "filament", "at": _now(), "printer": printer,
                            "plate_slice_id": ps.id, "msg": f"reslice needs review: {msg}"}
        notify(db, "waiting_filament", f"Reslice needs review: {fj.name}", msg[:300], link=_link(fj),
               ref=f"alert:fr:{fj.id}:{ps.id}")
        db.flush()
        return {"review": ps.id, "problems": ps.problems}
    approve(db, ps, "auto-approve (no problems)")
    f = pick_file(db, fj, ps)
    if not f:
        fj.plate_reslice = {"state": "failed", "kind": "filament", "at": _now(), "printer": printer,
                            "msg": "the reslice made no file for this job's plate"}
        db.flush()
        return {"failed": "no file"}
    ref = ensure_farm_file(f)
    farm().filament_map(fj.external_id, int(payload["printer_id"]), payload.get("slots") or [],
                        remember=bool(payload.get("remember")), file_ref=ref, by=payload.get("actor"))
    fj.file_ref = str(ref)
    fj.printer_name = printer
    fj.plate_reslice = {"state": "mapped", "kind": "filament", "at": _now(), "printer": printer,
                        "plate_slice_id": ps.id, "msg": f"resliced for {printer} with the loaded filament"}
    from ..jobs import event
    event(db, f"farmjob:{fj.id}", f"{fj.name or fj.external_id}: resliced for {printer} with the loaded filament")
    db.flush()
    return {"plate_slice_id": ps.id, "file_ref": ref, "mapped": True}


def _link(fj: FarmJob) -> str:
    from ..config import settings
    return settings().public_url.rstrip("/") + f"/farm/jobs/{fj.external_id}/filament"
