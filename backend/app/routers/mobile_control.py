"""Mobile API v1, mobile control (2026-10-23): the one spool library, filaments and barcodes, locations, scanning,
labels, printer hardware (nozzles, filament paths, what is loaded, the job's plate image), the store from the phone
and order status. Same prefix, keys and permissions as routers/mobile.py; docs/mobile-api.md has the shapes.

Permissions: inventory for spools, filaments, locations, labels and scanning; farm for printer hardware; view to
read the store, models to import and edit, store to push and change order status; settings for label printers and
the vision model.
"""
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, need
from ..db import get_db
from ..services import printer_access
from ..jobs import event
from ..services import spool_library as lib

router = APIRouter(prefix="/api/mobile/v1", tags=["mobile"])


def _actor(p: Principal) -> str:
    return p.get("username") or p.get("email") or "api"


def _lib_error(e: Exception):
    """LibraryError -> the HTTP answer (409 keeps the engine's allowed list); other errors are 502."""
    if isinstance(e, lib.LibraryError):
        if e.status == 409 and e.allowed is not None:
            return JSONResponse(status_code=409, content={"detail": e.detail, "allowed": e.allowed})
        raise HTTPException(e.status, e.detail)
    from ..services.connections import NotConnected
    if isinstance(e, NotConnected):
        raise HTTPException(400, str(e))
    raise HTTPException(502, str(getattr(e, "message", None) or e)[:500])


def _farm():
    """The OpenPrintHQ farm, for routes that need it."""
    from ..clients import farm
    f = farm()
    if f.kind != "openprinthq":
        raise HTTPException(400, "this needs the farm as the team's farm")
    return f


# ---------------------------------------------------------------- spools

@router.get("/spools")
def spools(q: str | None = None, location: str | None = None, material: str | None = None,
           db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """The team's spools: from the farm library when OpenPrintHQ is the farm, else the hub's Spoolman cache.
    Each has code (S<id>), location_code, assigned {printer, ams_id, tray_id, extruder_index, slot_label} and barcode."""
    try:
        return {"spools": lib.list_spools(db, q, location, material), "source": "farm" if lib.farm_library() else "spoolman"}
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)


@router.get("/spools/{spool_id}")
def spool(spool_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    from ..services import print_defaults
    try:
        out = lib.get_spool(db, spool_id)
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)
    if isinstance(out, dict):
        out["print_defaults"] = print_defaults.for_spool(db, out)
        db.commit()
    return out


class DefaultsIn(BaseModel):
    extruder_temp: float | None = None
    bed_temp: float | None = None
    density: float | None = None
    diameter_mm: float | None = None
    spool_weight_g: float | None = None
    color_hex: str | None = None


@router.get("/filaments/{filament_id}/print-defaults")
def filament_defaults(filament_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    from ..services import print_defaults
    return {"filament_id": filament_id, "print_defaults": print_defaults.get(db, filament_id)}


@router.put("/filaments/{filament_id}/print-defaults")
def filament_defaults_set(filament_id: int, body: DefaultsIn, db: Session = Depends(get_db),
                          p: Principal = Depends(need("inventory"))):
    """Set by hand: SpoolmanDB never overwrites these."""
    from ..services import print_defaults
    row = print_defaults.set_manual(db, filament_id, body.model_dump(exclude_none=True), _actor(p))
    db.commit()
    return {"filament_id": filament_id, "print_defaults": row}


@router.post("/print-defaults/backfill")
def print_defaults_backfill(replace: bool = False, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """SpoolmanDB defaults for every library filament that has none (replace: refresh them all, except hand-set)."""
    from ..services import print_defaults
    out = print_defaults.backfill(db, lib.library_filaments(db), replace=replace)
    event(db, "spools", f"Print defaults: {out['found']} found, {len(out['missing'])} not in SpoolmanDB", actor=_actor(p))
    db.commit()
    return out


class FilamentIn(BaseModel):
    vendor: str | None = None
    name: str | None = None
    material: str | None = None
    color_hex: str | None = None
    color_name: str | None = None
    weight_g: float | None = None
    diameter_mm: float | None = None
    spool_weight_g: float | None = None
    price: float | None = None
    barcode: str | None = None


class PrintLabelsIn(BaseModel):
    format: str = "pdf_62x29"
    printer_id: int | None = None
    copies: int = 1


class SpoolCreateIn(BaseModel):
    filament_id: int | None = None
    filament: FilamentIn | None = None
    quantity: int = Field(1, ge=1, le=50)
    remaining_g: float | None = None
    location_code: str | None = None
    lot: str | None = None
    note: str | None = None
    print_labels: PrintLabelsIn | None = None


@router.post("/spools")
def spool_create(body: SpoolCreateIn, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """New spools (1 to 50) of a library filament, or of a filament described here (made with its vendor when
    missing). A barcode on the filament is learned. print_labels prints them (format printer + printer_id) or
    answers a link to the label file."""
    data = body.model_dump()
    data["filament"] = body.filament.model_dump() if body.filament else None
    try:
        out = lib.create_spools(db, data, actor=_actor(p))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return _lib_error(e)
    labels = None
    if body.print_labels and out["spools"]:
        ids = [s["id"] for s in out["spools"]]
        pl = body.print_labels
        if pl.format == "printer":
            try:
                n = _print_to(pl.printer_id, [_spool_label(s) for s in out["spools"]], pl.copies)
                labels = {"printed": n}
            except HTTPException as e:
                labels = {"error": e.detail}
        elif pl.format in _labels().FORMATS:
            from urllib.parse import urlencode
            labels = {"pdf_url" if pl.format.startswith("pdf") else "file_url": "/api/mobile/v1/labels/file?" + urlencode(
                {"targets": ",".join(f"spool:{i}" for i in ids), "format": pl.format, "copies": pl.copies})}
    from ..services import print_defaults
    from ..services.material_names import clean_filament
    f_in = clean_filament(body.filament.model_dump()) if body.filament else {}
    for fid in {s.get("filament_id") for s in out["spools"] if s.get("filament_id") is not None}:
        s0 = next(s for s in out["spools"] if s.get("filament_id") == fid)
        try:
            print_defaults.set_for(db, fid, f_in.get("vendor") or s0.get("vendor"), f_in.get("material") or s0.get("material"),
                                   " ".join(x for x in (f_in.get("name"), s0.get("subtype")) if x),
                                   f_in.get("color_name") or s0.get("color_name"), f_in.get("weight_g") or s0.get("label_weight_g"))
        except Exception:  # noqa: BLE001
            pass
    event(db, "spools", f"{len(out['spools'])} spool(s) added from the mobile API: "
                        + ", ".join(s["code"] for s in out["spools"]), actor=_actor(p))
    db.commit()
    return {**out, "labels": labels}


class SpoolPatchIn(BaseModel):
    remaining_weight: float | None = None
    location: str | None = None
    comment: str | None = None


@router.patch("/spools/{spool_id}")
def spool_patch(spool_id: int, body: SpoolPatchIn, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """Weigh-in (remaining_weight), move by name (location) or a comment."""
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(400, "nothing to change")
    try:
        out = lib.patch_spool(db, spool_id, data)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return _lib_error(e)
    event(db, "spools", f"Spool #{spool_id} changed from the mobile API: {', '.join(data)}", actor=_actor(p))
    db.commit()
    return out


class SpoolUseIn(BaseModel):
    grams: float


@router.post("/spools/{spool_id}/use")
def spool_use(spool_id: int, body: SpoolUseIn, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    if not 0 < body.grams <= 5000:
        raise HTTPException(400, "grams is between 0 and 5000")
    try:
        lib.use(db, spool_id, body.grams)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return _lib_error(e)
    event(db, "spools", f"Spool #{spool_id}: {body.grams:g} g used (mobile API)", actor=_actor(p))
    db.commit()
    return {"ok": True}


@router.delete("/spools/{spool_id}")
def spool_archive(spool_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """Archive a spool (nothing is deleted)."""
    try:
        out = lib.archive(db, spool_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return _lib_error(e)
    event(db, "spools", f"Spool #{spool_id} archived from the mobile API", actor=_actor(p))
    db.commit()
    return out


class MoveIn(BaseModel):
    location_code: str


@router.post("/spools/{spool_id}/move")
def spool_move(spool_id: int, body: MoveIn, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    try:
        out = lib.move(db, spool_id, body.location_code.strip())
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return _lib_error(e)
    event(db, "spools", f"Spool #{spool_id} moved to {body.location_code} (mobile API)", actor=_actor(p))
    db.commit()
    return out


class AssignIn(BaseModel):
    target_code: str | None = None
    printer: str | None = None
    ams_id: int | None = None
    tray_id: int | None = None
    extruder_index: int | None = None


@router.post("/spools/{spool_id}/assign")
def spool_assign(spool_id: int, body: AssignIn, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """Install a spool in a slot: target_code (a slot's L code) or printer + ams_id + tray_id, and extruder_index when
    the slot can feed more than one. 409 {"detail", "allowed"} when that extruder is not on the slot's path."""
    try:
        out = lib.assign(db, spool_id, body.target_code, body.printer, body.ams_id, body.tray_id, body.extruder_index)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return _lib_error(e)
    a = out.get("assigned") or {}
    event(db, "spools", f"Spool #{spool_id} installed in {a.get('printer') or body.printer or body.target_code} "
                        f"{a.get('slot_label') or ''} (mobile API)".rstrip(), actor=_actor(p))
    db.commit()
    return out


@router.post("/spools/{spool_id}/unassign")
def spool_unassign(spool_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    try:
        out = lib.unassign(db, spool_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return _lib_error(e)
    event(db, "spools", f"Spool #{spool_id} taken out of its slot (mobile API)", actor=_actor(p))
    db.commit()
    return out


# ---------------------------------------------------------------- filaments and barcodes

@router.get("/filaments")
def filaments(q: str | None = None, material: str | None = None, catalog: bool = True,
              db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """Library filaments (source farm or spoolman), then SpoolmanDB catalog hits (source catalog, id null)."""
    try:
        return {"filaments": lib.search_filaments(db, q, material, catalog)}
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)


@router.get("/filaments/lookup")
def filament_lookup(barcode: str, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    from ..services import barcodes
    out = barcodes.lookup(db, barcode)
    db.commit()            # keeps the UPC cache
    return out


class LearnIn(BaseModel):
    barcode: str
    filament_id: int


@router.post("/filaments/barcodes")
def filament_learn(body: LearnIn, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    from ..services import barcodes
    try:
        row = barcodes.learn(db, body.barcode, body.filament_id, actor=_actor(p))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)
    event(db, "spools", f"Barcode {row.barcode} learned as filament #{row.filament_id}", actor=_actor(p))
    db.commit()
    return {"barcode": row.barcode, "filament_id": row.filament_id, "vendor": row.vendor, "name": row.name,
            "material": row.material, "color_hex": row.color_hex}


class HideIn(BaseModel):
    code: str
    all_printers: bool = False


@router.post("/printers/{name}/hide-error")
def printer_hide_error(name: str, body: HideIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """A nuisance code: never an error again, for this printer or (all_printers) every printer."""
    printer_access.require(db, p, name)
    from ..services import printer_status
    try:
        row = printer_status.hide(db, body.code, None if body.all_printers else name, _actor(p))
    except ValueError:
        raise HTTPException(400, "no code")
    event(db, "farm", f"Hid printer code {printer_status.norm(body.code)} on {'every printer' if body.all_printers else name}",
          actor=_actor(p))
    db.commit()
    return {"hidden": {"code": printer_status.norm(body.code), **row}}


@router.get("/printer-errors/hidden")
def printer_errors_hidden(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from ..services import printer_status
    return {"hidden": printer_status.hidden_list(db)}


@router.delete("/printer-errors/hidden/{code}")
def printer_errors_unhide(code: str, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from ..services import printer_status
    gone = printer_status.unhide(db, code)
    db.commit()
    return {"removed": gone}


class MatchIn(BaseModel):
    guess: dict
    barcode: str | None = None
    text: str | None = None


@router.get("/filaments/search")
def filament_search(barcode: str, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """Google, through Gemini, for a barcode nothing local knows: {matches, sources, engine, note}."""
    from ..services import product_search
    return product_search.by_barcode(barcode)


@router.post("/filaments/match")
def filament_match(body: MatchIn, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """Google, through Gemini, for the real product behind what the label read: {matches, sources, engine, note}."""
    from ..services import product_search
    return product_search.by_guess(body.guess or {}, body.barcode or "", body.text or "")


@router.post("/filaments/identify")
async def filament_identify(request: Request, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """A photo (multipart field photo, JPEG) and/or OCR text (multipart field text, or JSON {"text", "hints"}).
    Answers {guess, candidates, engine}: the hub's vision model when it answers, else the rules parser."""
    from starlette.concurrency import run_in_threadpool
    from ..services import barcodes
    photo, text, hints = None, None, {}
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        form = await request.form()
        f = form.get("photo")
        if f is not None and hasattr(f, "read"):
            photo = await f.read()
        text = form.get("text") or None
        try:
            hints = json.loads(form.get("hints") or "{}")
        except ValueError:
            hints = {}
    else:
        try:
            doc = await request.json()
        except ValueError:
            raise HTTPException(422, "send multipart (photo, text, hints) or JSON {text, hints}")
        text, hints = doc.get("text"), doc.get("hints") or {}
    if photo is not None and len(photo) > 12 * 1024 * 1024:
        raise HTTPException(413, "photo too large (12 MB)")
    if not photo and not text:
        raise HTTPException(422, "send a photo, text, or both")
    if not isinstance(hints, dict):
        hints = {}
    return await run_in_threadpool(barcodes.identify, db, photo, text, hints)


# ---------------------------------------------------------------- locations and scanning

def _tree(rows: list[dict]) -> list[dict]:
    by = {r["id"]: {**r, "children": []} for r in rows}
    roots = []
    for r in by.values():
        par = by.get(r.get("parent_id"))
        (par["children"] if par else roots).append(r)
    return roots


@router.get("/locations")
def locations(kind: str | None = None, flat: bool = False, db: Session = Depends(get_db),
              p: Principal = Depends(need("inventory"))):
    """Locations from the farm, as a tree (children) or flat (?flat=true); ?kind= filters (flat)."""
    try:
        rows = _farm().locations(kind=kind)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        return _lib_error(lib.LibraryError(502, str(getattr(e, "message", e))[:300]))
    try:
        by = lib.spools_by_location(db)
    except Exception:  # noqa: BLE001
        by = {}
    rows = [{**r, "spools": by.get(r["id"], [])} for r in rows]
    return {"locations": rows if (flat or kind) else _tree(rows)}


class LocationIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    parent_id: int | None = None
    printer_id: int | None = None
    ams_id: int | None = None
    tray_id: int | None = None
    code: str | None = None
    note: str | None = None


@router.post("/locations")
def location_create(body: LocationIn, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    if not (body.name or "").strip():
        raise HTTPException(422, "name is required")
    try:
        out = lib._call(_farm().create_location, body.model_dump(exclude_none=True))
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)
    event(db, "spools", f"Location {out.get('name')} ({out.get('code')}) added from the mobile API", actor=_actor(p))
    db.commit()
    return out


@router.patch("/locations/{location_id}")
def location_patch(location_id: int, body: LocationIn, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "nothing to change")
    try:
        return lib._call(_farm().patch_location, location_id, data)
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)


@router.delete("/locations/{location_id}")
def location_delete(location_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    try:
        lib._call(_farm().delete_location, location_id)
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)
    event(db, "spools", f"Location #{location_id} deleted from the mobile API", actor=_actor(p))
    db.commit()
    return {"ok": True}


@router.post("/locations/sync-printers")
def locations_sync(db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """One location per printer, AMS unit, slot and external holder (idempotent); answers the printer-bound ones."""
    try:
        return {"locations": lib._call(_farm().sync_printer_locations)}
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)


@router.get("/scan/{code:path}")
def scan(code: str, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """What a scanned code is and what can be done with it (see services/scan.py)."""
    from ..services import scan as sc
    try:
        out = sc.resolve(db, code)
    except lib.LibraryError as e:
        if e.status == 404:
            return {"kind": "unknown", "code": sc.normalize(code).upper(), "title": "Not found", "subtitle": e.detail,
                    "actions": []}
        return _lib_error(e)
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)
    db.commit()
    return out


# ---------------------------------------------------------------- labels

def _labels():
    from ..services import labels
    return labels


def _spool_label(s: dict) -> dict:
    return _labels().spool_item(s)


def _items(db: Session, targets: list[dict]) -> list[dict]:
    L = _labels()
    out = []
    locs = None
    for t in targets:
        kind, tid = t.get("kind"), t.get("id")
        if kind == "spool":
            try:
                out.append(L.spool_item(lib.get_spool(db, int(tid))))
            except lib.LibraryError as e:
                raise HTTPException(e.status if e.status != 502 else 502, f"spool {tid}: {e.detail}")
        elif kind == "location":
            if locs is None:
                try:
                    locs = {x["id"]: x for x in _farm().locations()}
                except HTTPException:
                    raise
                except Exception as e:  # noqa: BLE001
                    raise HTTPException(502, str(getattr(e, "message", e))[:300])
            loc = locs.get(int(tid)) if str(tid).isdigit() else next((x for x in locs.values()
                                                                        if (x.get("code") or "").upper() == str(tid).upper()), None)
            if not loc:
                raise HTTPException(404, f"no location {tid}")
            if loc.get("printer_id") and not loc.get("printer_name"):
                par = locs.get(loc.get("parent_id"))
                while par and par.get("kind") != "printer":
                    par = locs.get(par.get("parent_id"))
                loc = {**loc, "printer_name": par.get("name") if par else None}
            out.append(L.location_item(loc))
        else:
            raise HTTPException(422, "target kind is spool or location")
    if not out:
        raise HTTPException(422, "give at least one target")
    return out


def _print_to(printer_id: int | None, items: list[dict], copies: int) -> int:
    L = _labels()
    ps = L.printers()
    pr = next((x for x in ps if x["id"] == printer_id), None) if printer_id is not None else (ps[0] if len(ps) == 1 else None)
    if not pr:
        raise HTTPException(404 if ps else 400, "no such label printer" if ps else "no label printers set up")
    try:
        return L.print_labels(pr, items, copies)
    except L.LabelError as e:
        raise HTTPException(502, str(e))


def _file(items: list[dict], fmt: str, copies: int) -> Response:
    L = _labels()
    if fmt.startswith("pdf_"):
        return Response(L.pdf(items, fmt, copies), media_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="labels.pdf"', "Cache-Control": "no-store"})
    data, ctype = L.png(items, fmt, copies)
    name = "labels.zip" if ctype == "application/zip" else "label.png"
    return Response(data, media_type=ctype, headers={"Content-Disposition": f'inline; filename="{name}"',
                                                     "Cache-Control": "no-store"})


class TargetIn(BaseModel):
    kind: str
    id: int | str


class LabelsIn(BaseModel):
    targets: list[TargetIn]
    format: str = "pdf_62x29"
    printer_id: int | None = None
    copies: int = Field(1, ge=1, le=20)


@router.post("/labels")
def labels(body: LabelsIn, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    """Labels for spools and locations: a PDF or PNG (ZIP for several PNGs), or format printer to print them on a
    label printer (answers {"printed": n})."""
    L = _labels()
    if body.format not in L.FORMATS:
        raise HTTPException(422, f"format is one of {', '.join(L.FORMATS)}")
    if len(body.targets) > 300:
        raise HTTPException(422, "at most 300 labels at once")
    items = _items(db, [t.model_dump() for t in body.targets])
    if body.format == "printer":
        n = _print_to(body.printer_id, items, body.copies)
        event(db, "spools", f"{n} label(s) printed from the mobile API", actor=_actor(p))
        db.commit()
        return {"printed": n}
    return _file(items, body.format, body.copies)


@router.get("/labels/file")
def labels_file(targets: str, format: str = "pdf_62x29", copies: int = 1, db: Session = Depends(get_db),
                p: Principal = Depends(need("inventory"))):
    """The same as POST /labels for a file, as a link: targets=spool:12,location:L7K2QX,..."""
    L = _labels()
    if format not in L.FORMATS or format == "printer":
        raise HTTPException(422, "format is a pdf_ or png_ format")
    ts = []
    for t in targets.split(","):
        k, _, i = t.partition(":")
        if k and i:
            ts.append({"kind": k.strip(), "id": i.strip()})
    return _file(_items(db, ts[:300]), format, max(1, min(20, copies)))


@router.get("/labels/printers")
def label_printers(p: Principal = Depends(need("inventory"))):
    """Configured label printers; kind pdf is the built-in PDF/PNG output, always there."""
    L = _labels()
    ps = [{k: x[k] for k in ("id", "name", "kind", "model", "tape", "dpi")} for x in L.printers()]
    return {"printers": ps + [{"id": None, "name": "PDF or PNG on this phone", "kind": "pdf", "model": None,
                               "tape": None, "dpi": 300}], "formats": list(L.FORMATS)}


class LabelPrinterIn(BaseModel):
    id: int | None = None
    name: str | None = None
    kind: str = "brother_ql"
    host: str
    port: int = 9100
    model: str | None = None
    tape: str | None = None
    dpi: int | None = None


def _save_printers(db: Session, items: list[dict]):
    from ..services import connections
    connections.save(db, "labelprinter", {"printers": items}, None, enabled=True)


@router.post("/labels/printers")
def label_printer_save(body: LabelPrinterIn, db: Session = Depends(get_db), p: Principal = Depends(need("settings"))):
    """Add a label printer, or change one (with its id)."""
    L = _labels()
    try:
        new = L.validate_printer(body.model_dump())
    except L.LabelError as e:
        raise HTTPException(422, str(e))
    cur = L.printers()
    if body.id is not None:
        if not any(x["id"] == body.id for x in cur):
            raise HTTPException(404, "no such label printer")
        cur = [{**new, "id": body.id} if x["id"] == body.id else x for x in cur]
        pid = body.id
    else:
        pid = max([x["id"] for x in cur] or [0]) + 1
        cur.append({**new, "id": pid})
    try:
        _save_printers(db, cur)
    except ValueError as e:
        raise HTTPException(422, str(e))
    event(db, "team", f"Label printer {new['name']} saved", actor=_actor(p))
    db.commit()
    return next(x for x in L.printers() if x["id"] == pid)


@router.delete("/labels/printers/{printer_id}")
def label_printer_delete(printer_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("settings"))):
    L = _labels()
    cur = L.printers()
    if not any(x["id"] == printer_id for x in cur):
        raise HTTPException(404, "no such label printer")
    _save_printers(db, [x for x in cur if x["id"] != printer_id])
    db.commit()
    return {"ok": True}


@router.post("/labels/printers/{printer_id}/test")
def label_printer_test(printer_id: int, p: Principal = Depends(need("settings"))):
    """Can the hub reach the printer (TCP connect, nothing is printed)?"""
    L = _labels()
    pr = next((x for x in L.printers() if x["id"] == printer_id), None)
    if not pr:
        raise HTTPException(404, "no such label printer")
    ok, msg = L.probe(pr)
    return {"ok": ok, "detail": msg}


# ---------------------------------------------------------------- identification settings

class VisionIn(BaseModel):
    vision_url: str | None = None
    vision_model: str | None = None


@router.get("/settings/identify")
def identify_settings(db: Session = Depends(get_db), p: Principal = Depends(need("settings"))):
    from ..services.barcodes import _vision_cfg
    url, model, timeout = _vision_cfg(db)
    return {"vision_url": url, "vision_model": model, "timeout_s": timeout}


@router.put("/settings/identify")
def identify_settings_put(body: VisionIn, db: Session = Depends(get_db), p: Principal = Depends(need("settings"))):
    from ..kv import set_setting
    if body.vision_url is not None:
        if body.vision_url and not re.match(r"^https?://", body.vision_url):
            raise HTTPException(422, "vision_url starts with http:// or https://")
        set_setting(db, "vision_url", body.vision_url.rstrip("/") or None)
    if body.vision_model is not None:
        set_setting(db, "vision_model", body.vision_model.strip() or None)
    db.commit()
    return identify_settings(db, p)


# ---------------------------------------------------------------- printer hardware (OpenPrintHQ)

def _pid(name: str) -> tuple:
    f = _farm()
    try:
        return f, f.printer_id(name)
    except Exception as e:  # noqa: BLE001
        st = getattr(e, "status", None)
        raise HTTPException(404 if st == 404 else 502, str(getattr(e, "message", e))[:300])


@router.get("/printers/{name}/cover")
def printer_cover(name: str, view: str | None = None, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """The current job's sliced plate image (PNG); 404 when the printer is idle. view: top | side (default)."""
    printer_access.require(db, p, name)
    if view not in (None, "", "top", "side"):
        raise HTTPException(422, "view is top or side")
    f, pid = _pid(name)
    try:
        data = f.cover(pid, "top" if view == "top" else None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(getattr(e, "message", e))[:300])
    if not data:
        raise HTTPException(404, f"no plate image for {name} (not printing)")
    ctype = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if data[:2] == b"\xff\xd8" else "application/octet-stream"
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "private, max-age=60"})


@router.get("/printers/{name}/nozzles")
def printer_nozzles(name: str, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    printer_access.require(db, p, name)
    f, pid = _pid(name)
    try:
        return lib._call(f.nozzles, pid)
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)


class NozzleIn(BaseModel):
    diameter_mm: float
    nozzle_type: str
    volume_type: str


@router.put("/printers/{name}/nozzles/{extruder_index}")
def printer_nozzle_put(name: str, extruder_index: int, body: NozzleIn, db: Session = Depends(get_db),
                       p: Principal = Depends(need("farm"))):
    printer_access.require(db, p, name)
    f, pid = _pid(name)
    try:
        out = lib._call(f.set_nozzle, pid, extruder_index, body.model_dump())
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)
    event(db, "farm", f"{name}: nozzle {extruder_index} set to {body.diameter_mm:g} mm {body.nozzle_type} "
                      f"{body.volume_type} (mobile API)", actor=_actor(p))
    db.commit()
    return out


@router.delete("/printers/{name}/nozzles/{extruder_index}")
def printer_nozzle_delete(name: str, extruder_index: int, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    printer_access.require(db, p, name)
    f, pid = _pid(name)
    try:
        return lib._call(f.clear_nozzle, pid, extruder_index)
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)


@router.get("/printers/{name}/filament-paths")
def printer_paths(name: str, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    printer_access.require(db, p, name)
    f, pid = _pid(name)
    try:
        return lib._call(f.filament_paths, pid)
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)


class PathSlot(BaseModel):
    ams_id: int
    tray_id: int | None = None
    extruders: list[int]


class PathsIn(BaseModel):
    track_switch_mode: str | None = None
    slots: list[PathSlot] | None = None


@router.put("/printers/{name}/filament-paths")
def printer_paths_put(name: str, body: PathsIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """track_switch_mode auto | installed | absent; slots replaces the hand-set paths ([] clears, leave out to keep)."""
    printer_access.require(db, p, name)
    f, pid = _pid(name)
    try:
        out = lib._call(f.set_filament_paths, pid, body.model_dump(exclude_unset=True))
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)
    event(db, "farm", f"{name}: filament paths changed (mobile API)", actor=_actor(p))
    db.commit()
    return out


@router.get("/printers/{name}/loaded")
def printer_loaded(name: str, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    printer_access.require(db, p, name)
    f, pid = _pid(name)
    try:
        return lib._call(f.loaded, pid)
    except Exception as e:  # noqa: BLE001
        return _lib_error(e)


# ---------------------------------------------------------------- store from the phone

@router.get("/sources/status")
def sources_status(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from . import sourcing
    return sourcing.sources_status(db, p)


@router.get("/sources/search")
def sources_search(platform: str = "printables", q: str = "", sellable: bool = True, sort: str = "downloads",
                   offset: int = 0, limit: int = 30, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """Search a model site; sellable licences only unless sellable=false."""
    from . import sourcing
    if platform not in ("printables", "makerworld", "thingiverse", "creality"):
        raise HTTPException(422, "platform is printables, makerworld, thingiverse or creality")
    return sourcing.sources_search(platform, q, "sellable" if sellable else "", sort, offset, max(1, min(limit, 40)), db, p)


@router.get("/sources/preview")
def sources_preview(url: str, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """What a model link would import (any of the supported sites, or any public link). files_in_browser means the
    site wants a signed-in browser for the files: details import, files come later (browser add-on or Files tab)."""
    from . import sourcing
    out = sourcing.sources_preview(url, db, p)
    out.setdefault("files_in_browser", False)
    return out


@router.get("/sources/sites")
def sources_sites(p: Principal = Depends(need("view"))):
    """The sites "add by link" knows, and whether the hub or a signed-in browser fetches their files."""
    from .clipper import SITES
    return {"sites": SITES}


class ImportIn(BaseModel):
    url: str
    profile_id: str | None = None
    category: str | None = None            # chosen (made if new); else suggested from the source's
    overrides: dict | None = None          # English name, summary, description, tags translated on the phone


@router.get("/categories")
def categories(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """The team's categories (names, sorted), for picking one when editing or importing."""
    from ..models import Category
    return {"categories": sorted(c.name for c in db.execute(select(Category)).scalars())}


@router.get("/sources/plan")
def sources_plan(url: str, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """What the import sheet needs: the text that is probably not English (translate it on the phone, then send it
    as overrides), the suggested category and the team's categories. File names are translated by the hub."""
    from . import sourcing
    from ..models import Category
    from ..services import import_language as il
    pv = sourcing.sources_preview(url, db, p)
    text = {"name": pv.get("title") or "", "summary": (pv.get("summary") or "")[:500],
            "description": pv.get("description") or "", "tags": ", ".join(pv.get("tags") or [])}
    return {"platform": pv.get("platform"), "title": pv.get("title"), "text": text,
            "not_english": [k for k, v in text.items() if il.needs_english(v)],
            "files_not_english": sum(1 for f in pv.get("files") or [] if il.needs_english(f.get("name"))),
            "source_category": pv.get("source_category"),
            "suggested_category": il.suggest_category(db, pv.get("platform") or "", pv.get("source_category")),
            "categories": sorted(c.name for c in db.execute(select(Category)).scalars()),
            "existing_model_id": pv.get("existing_model_id"), "options": il.options(db)}


@router.post("/sources/import")
def sources_import(body: ImportIn, db: Session = Depends(get_db), p: Principal = Depends(need("models"))):
    """Import a model as a draft product. Answers {job_id, model_id}; model_id is set when it was already imported.
    Follow GET /jobs/{job_id}: its result has model_id and product_id when done."""
    from . import sourcing
    from .. import sources as srcs
    from ..models import Model
    try:
        platform, ext = srcs.detect(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    have = db.execute(select(Model.id).where(Model.source_platform == platform, Model.source_ext_id == str(ext))).scalar()
    if not have and platform not in ("makerworld",):
        found = srcs.base.find_existing(db, platform, ext, body.url.strip())
        have = found.id if found else None
    r = sourcing.sources_import(sourcing.SourceImportIn(url=body.url, profile_id=body.profile_id, category=body.category,
                                                        overrides=body.overrides), db, p)
    return {"job_id": r.get("job"), "model_id": have, "platform": platform,
            "files_in_browser": platform in srcs.BROWSER_FILES}


@router.get("/jobs/{job_id}")
def job(job_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from ..models import Job
    j = db.get(Job, job_id)
    if not j:
        raise HTTPException(404)
    res = j.result or {}
    st = {"queued": "queued", "running": "running", "done": "done", "failed": "failed"}.get(j.state, j.state)
    return {"id": j.id, "kind": j.kind, "status": st, "attempts": j.attempts,
            "result": {"model_id": res.get("model_id"), "product_id": res.get("product_id"), **res} if j.state == "done" else None,
            "error": (j.error or "")[:1000] or None}


def _product_detail(db: Session, pr) -> dict:
    from ..models import SliceResult
    from ..serialize import product as ser_product
    from ..services.shop import public_image_url
    from ..services import licensing
    from ..clients import public_url
    m = pr.model
    imgs = sorted([i for i in m.images if i.role != "archive"], key=lambda i: (i.role != "hero", i.sort, i.id))
    sp = ser_product(pr)
    slices = list(db.execute(select(SliceResult).where(SliceResult.model_id == m.id)).scalars())
    woo = (public_url("woo", "url") or "").rstrip("/")
    return {"id": pr.id, "model_id": m.id, "title": pr.title, "sku": pr.sku, "price": pr.price, "sale_price": pr.sale_price,
            "short_description": pr.short_description, "description": pr.description,
            "category": m.category.name if m.category else None, "categories": [m.category.name] if m.category else [],
            "images": [{"id": i.id, "thumb": _mimg(i, "thumb"), "large": _mimg(i, "large"), "role": i.role,
                        "public": public_image_url(i)} for i in imgs],
            "variants": sp["variants"],
            "bom": {"filaments": len(sp["filaments"]), "grams": round(sum((f["grams"] or 0) for f in sp["filaments"]), 1),
                    "parts": len(sp["parts"]), "filament_lines": sp["filaments"], "part_lines": sp["parts"]},
            "license": {**{k: v for k, v in licensing.info(m.license_code).items() if k in ("code", "label", "sell", "credit")},
                        "code": m.license_code, "problems": licensing.sale_problems(m), "credit_line": licensing.credit_line(m)},
            "slice": {"approved": sum(1 for s in slices if s.state == "approved"),
                      "states": {s.state: sum(1 for x in slices if x.state == s.state) for s in slices},
                      "print_ready": any(f.kind in ("3mf_sliced", "gcode") for f in m.files)},
            "live": pr.live, "woo_product_id": pr.woo_product_id, "woo_status": pr.woo_status, "woo_error": pr.woo_error,
            "store_url": sp.get("woo_view_url") or (f"{woo}/?p={pr.woo_product_id}" if woo and pr.woo_product_id else None),
            "source": {"platform": m.source_platform, "url": m.source_url}, "status": m.status,
            "model": _model_fields(m)}


def _model_fields(m) -> dict:
    """The model's own editable fields (PATCH /models/{id})."""
    return {"id": m.id, "code": m.code, "name": m.name, "summary": m.summary, "description": m.description,
            "tags": list(m.tags or []), "model_dims": m.model_dims, "print_dims": m.print_dims,
            "license_note": m.license_note, "designer": m.designer.name if getattr(m, "designer", None) else None}


class ModelPatchIn(BaseModel):
    name: str | None = Field(None, max_length=300)
    summary: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    model_dims: str | None = Field(None, max_length=120)
    print_dims: str | None = Field(None, max_length=120)
    license_note: str | None = None


@router.patch("/models/{model_id}")
def model_patch(model_id: int, body: ModelPatchIn, db: Session = Depends(get_db), p: Principal = Depends(need("models"))):
    """Edit a model's name, summary, description, tags, dimensions and licence note. Only the fields sent change.
    The library folder keeps its name (files keep their stored paths); the hub's model page renames it."""
    from ..models import Model
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404)
    o = body.model_dump(exclude_unset=True)
    if "name" in o and not (o["name"] or "").strip():
        raise HTTPException(400, "a model needs a name")
    for k, v in o.items():
        if k == "tags":
            v = [t.strip() for t in (v or []) if t and t.strip()][:40]
        elif isinstance(v, str):
            v = v.strip() if k in ("name", "model_dims", "print_dims") else v
        setattr(m, k, v)
    event(db, f"model:{m.id}", f"Model changed from the mobile API: {', '.join(o)}", actor=_actor(p))
    db.commit()
    return _model_fields(m)


def _mimg(img, size: str) -> str:
    return f"/api/mobile/v1/media/{img.id}/{size}?v={img.sha256[:8]}"


@router.get("/media/{image_id}/{size}")
def media(image_id: int, size: str, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """A product or model image for the phone, with the phone's key (thumb, large, original). Product detail
    answers these URLs; each image also has a signed public JPEG link (public) that needs no headers."""
    from . import ops
    return ops.media(image_id, size, db, p)


@router.get("/products/{product_id}")
def product_detail(product_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from ..models import Product
    pr = db.get(Product, product_id)
    if not pr:
        raise HTTPException(404)
    return _product_detail(db, pr)


class ProductPatchIn(BaseModel):
    title: str | None = None
    short_description: str | None = None
    description: str | None = None
    price: float | None = None
    sale_price: float | None = None
    categories: list[str | int] | None = None


@router.patch("/products/{product_id}")
def product_patch(product_id: int, body: ProductPatchIn, db: Session = Depends(get_db), p: Principal = Depends(need("models"))):
    """Edit what the store shows. categories: names or ids; a model has one category, the first one is used (a new
    name is created). Push to send the changes to the store."""
    from slugify import slugify
    from ..models import Category, Product
    pr = db.get(Product, product_id)
    if not pr:
        raise HTTPException(404)
    o = body.model_dump(exclude_unset=True)
    for k in ("price", "sale_price"):
        if o.get(k) is not None and o[k] < 0:
            raise HTTPException(422, f"{k} can't be negative")
    if o.get("sale_price") is not None and (o.get("price") or pr.price) is not None and o["sale_price"] >= (o.get("price") or pr.price):
        raise HTTPException(422, "sale_price must be below price")
    if "title" in o and not (o["title"] or "").strip():
        raise HTTPException(422, "title can't be empty")
    for k in ("title", "short_description", "description", "price", "sale_price"):
        if k in o:
            setattr(pr, k, o[k].strip() if isinstance(o[k], str) and k == "title" else o[k])
    if "categories" in o:
        cats = o["categories"] or []
        if not cats:
            pr.model.category_id = None
        else:
            c0 = cats[0]
            cat = db.get(Category, int(c0)) if isinstance(c0, int) or str(c0).isdigit() else \
                db.execute(select(Category).where(Category.name == str(c0).strip())).scalar()
            if cat is None:
                if isinstance(c0, int) or str(c0).isdigit():
                    raise HTTPException(404, f"no category {c0}")
                cat = Category(name=str(c0).strip()[:120], slug=slugify(str(c0))[:140] or "category")
                db.add(cat)
                db.flush()
            pr.model.category_id = cat.id
    event(db, f"product:{pr.id}", f"Product changed from the mobile API: {', '.join(o)}", actor=_actor(p))
    db.commit()
    db.refresh(pr)
    return _product_detail(db, pr)


@router.post("/products/{product_id}/push")
def product_push(product_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("store"))):
    from . import catalog
    r = catalog.push(product_id, db, p)
    return {"job_id": None, "stores": r.get("stores") or []}


class OrderStatusIn(BaseModel):
    status: str


@router.post("/orders/{order_id}/status")
def order_status(order_id: int, body: OrderStatusIn, db: Session = Depends(get_db), p: Principal = Depends(need("store"))):
    """Set a store order to processing, completed or on-hold (in its store and here). Shopify and Etsy orders take
    completed only (marked shipped there); their other states follow the store."""
    from ..clients import woo
    from ..models import Order
    from ..services import stores
    if body.status not in ("processing", "completed", "on-hold"):
        raise HTTPException(422, "status is processing, completed or on-hold")
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404)
    if (o.channel or "woo") != "woo":
        if body.status != "completed":
            raise HTTPException(400, f"{stores.LABEL[o.channel]} orders can only be marked shipped from the hub")
        old = o.status
        try:
            stores.mark_shipped(db, o, actor=_actor(p))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"the store did not take it: {str(getattr(e, 'message', e))[:300]}")
        event(db, f"order:{o.id}", f"Order {o.number}: {old} -> {o.status} (mobile API)", actor=_actor(p))
        db.commit()
        return {"id": o.id, "number": o.number, "status": o.status}
    try:
        r = woo().update_order(o.woo_order_id, {"status": body.status}) or {}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"the store did not take it: {str(getattr(e, 'message', e))[:300]}")
    old = o.status
    o.status = r.get("status") or body.status
    event(db, f"order:{o.id}", f"Order {o.number}: {old} -> {o.status} (mobile API)", actor=_actor(p))
    db.commit()
    return {"id": o.id, "number": o.number, "status": o.status}


# ---------------------------------------------------------------- build plates, out of service, print now, orders
# The same services the web uses (services/plates.py, farm.print_now, services/manual_orders.py), so the app and the
# web behave alike.

def _profile_for(db: Session, name: str):
    from ..services.plate_reslice import profile_for
    prof = profile_for(db, name)
    if not prof:
        raise HTTPException(404, f"no printer profile in the hub for {name}")
    return prof


@router.get("/printers/{name}/plate")
def printer_plate(name: str, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """The plate fitted on a printer, its usual plate, and the catalog to pick from (grouped by maker)."""
    printer_access.require(db, p, name)
    from ..models import BuildPlate
    from ..services import plates
    from .plates import plate_dict
    plates.ensure_seed(db)
    db.commit()
    prof = _profile_for(db, name)
    cur = plates.installed(db, prof)
    cat = db.execute(select(BuildPlate).order_by(BuildPlate.maker, BuildPlate.name)).scalars()
    return {"printer": name, "usual_plate": prof.bed_type, "bambu": plates.is_bambu(prof),
            "installed": {**plate_dict(cur), "slices_as": plates.slicer_type_for(cur, prof)} if cur else None,
            "catalog": [plate_dict(x) for x in cat]}


class PlateSetIn(BaseModel):
    plate_id: int | None = None


@router.put("/printers/{name}/plate")
def printer_plate_set(name: str, body: PlateSetIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """Record a plate swap: updates the farm's plate check and the Bambu marker detection, and logs maintenance."""
    printer_access.require(db, p, name)
    from ..jobs import enqueue
    from ..models import BuildPlate
    from ..services import plates
    prof = _profile_for(db, name)
    plate = db.get(BuildPlate, body.plate_id) if body.plate_id is not None else None
    if body.plate_id is not None and not plate:
        raise HTTPException(404, "plate not found")
    out = plates.set_installed(db, prof, plate, actor=_actor(p))
    db.commit()
    enqueue(db, "farm.plate_scan", ref="plate:scan", delay=2)
    db.commit()
    return out


class ServiceIn(BaseModel):
    down: bool
    reason: str | None = None


@router.post("/printers/{name}/service")
def printer_service(name: str, body: ServiceIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """Out of service (no new jobs; a running print carries on) or back in service."""
    printer_access.require(db, p, name)
    try:
        _farm().set_down(name, body.down, (body.reason or "").strip() or f"Set from the app by {_actor(p)}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(getattr(e, "message", e))[:300])
    event(db, "farm", f"{name}: {'out of service' if body.down else 'back in service'} (mobile API)", actor=_actor(p))
    db.commit()
    return {"ok": True}


def _product_model(db: Session, product_id: int):
    from ..models import Model, Product
    pr = db.get(Product, product_id)
    m = db.get(Model, pr.model_id) if pr else None
    if not m:
        raise HTTPException(404, "product not found")
    return m


@router.get("/products/{product_id}/print-options")
def product_print_options(product_id: int, quantity: int = 1, db: Session = Depends(get_db),
                          p: Principal = Depends(need("view"))):
    """What Print now and Create order need: printers with an approved slice, whether the farm can pin a job to one,
    and a suggested order price."""
    from ..models import PrinterProfile
    from ..services import farm, manual_orders, slicing
    m = _product_model(db, product_id)
    out = []
    for prof in db.execute(select(PrinterProfile).where(PrinterProfile.active.is_(True))
                           .order_by(PrinterProfile.is_default.desc(), PrinterProfile.name)).scalars():
        r = slicing.result_for(db, m.id, prof.id, create=False)
        if r and r.approved_file_ids:
            st = r.approved_stats or {}
            out.append({"profile_id": prof.id, "printer": prof.name, "is_default": prof.is_default,
                        "time_s": st.get("time_s"), "grams": st.get("per_unit_g") or st.get("grams")})
    return {"model_id": m.id, "printers": out, "can_print_now": farm.can_pin(),
            "price": manual_orders.suggest(db, m, quantity)}


class PrintNowIn(BaseModel):
    profile_id: int
    quantity: int = 1


@router.post("/products/{product_id}/print")
def product_print_now(product_id: int, body: PrintNowIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    from ..models import PrinterProfile
    from ..services import farm
    m = _product_model(db, product_id)
    prof = db.get(PrinterProfile, body.profile_id)
    if not prof:
        raise HTTPException(404, "printer not found")
    try:
        out = farm.print_now(db, m, prof, _actor(p), body.quantity)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(getattr(e, "message", e))[:300])
    db.commit()
    return out


@router.post("/products/{product_id}/order")
def product_order(product_id: int, body: dict, db: Session = Depends(get_db), p: Principal = Depends(need("store"))):
    """Create an order (same body as the web's POST /api/models/{id}/order): a pending WooCommerce order with its
    pay link, the invoice emailed when send_invoice, sent to the farm now when print_now."""
    from pydantic import ValidationError
    from .manual_orders import OrderIn
    from ..services import manual_orders
    m = _product_model(db, product_id)
    try:
        data = OrderIn(**body).model_dump()
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False))
    try:
        out = manual_orders.create(db, m, data, _actor(p))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(getattr(e, "message", e))[:300])
    db.commit()
    return out


@router.post("/orders/{order_id}/invoice")
def order_invoice(order_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("store"))):
    from ..models import Order
    from ..services import manual_orders
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    try:
        out = manual_orders.send_invoice(db, o, _actor(p))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(getattr(e, "message", e))[:300])
    db.commit()
    return out


# ---- alarm feed (farm capability "alarms") ----

@router.get("/alarms")
def alarms(state: str = "active", printer: str | None = None, unacknowledged: bool = False,
           db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """The farm's alarms: active ones worst first (fatal, serious, warning), or history with state=cleared|all."""
    if state not in ("active", "cleared", "all"):
        raise HTTPException(400, "state is active, cleared or all")
    printer_access.require(db, p, printer)
    try:
        return {"alarms": printer_access.visible_items(
            db, p, _farm().alarms(state=state, printer=printer, unacknowledged=unacknowledged))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(getattr(e, "message", e))[:300])


@router.get("/alarms/summary")
def alarm_summary(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """Counts for a badge: active, unacknowledged, by_severity, and the printers with any (ids and names)."""
    try:
        return printer_access.alarm_summary(db, p, _farm().alarm_summary())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(getattr(e, "message", e))[:300])


def _alarm_error(e: Exception, what: str = "Alarm"):
    if getattr(e, "status", None) == 404:
        raise HTTPException(404, f"{what} not found")
    raise HTTPException(502, str(getattr(e, "message", e))[:300])


def _my_alarm(db: Session, p: Principal, alarm_id: int) -> dict:
    """The alarm, if it is on one of the caller's printers (404 otherwise)."""
    try:
        a = _farm().alarm(alarm_id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        _alarm_error(e)
    if not a or not printer_access.visible_items(db, p, [a]):
        raise HTTPException(404, "Alarm not found")
    return a


def _alarm_label(a: dict) -> str:
    return f"{a.get('printer_name') or 'printer ' + str(a.get('printer_id'))} {a.get('code') or ''}".strip()


@router.post("/alarms/{alarm_id}/ack")
def alarm_ack(alarm_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    _my_alarm(db, p, alarm_id)
    try:
        out = _farm().ack_alarm(alarm_id, _actor(p))
    except Exception as e:  # noqa: BLE001
        _alarm_error(e)
    event(db, "farm", f"Alarm {alarm_id} acknowledged (mobile API)", actor=_actor(p))
    db.commit()
    return {"ok": True, **(out or {})}


@router.post("/printers/{name}/alarms/ack")
def printer_alarms_ack(name: str, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    printer_access.require(db, p, name)
    try:
        out = _farm().ack_printer_alarms(name, _actor(p))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(getattr(e, "message", e))[:300])
    event(db, "farm", f"{name}: alarms acknowledged (mobile API)", actor=_actor(p))
    db.commit()
    return {"ok": True, **(out or {})}


@router.post("/alarms/{alarm_id}/dismiss")
def alarm_dismiss(alarm_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """Take the alarm off the active list now. If the printer clears it and reports it again, it shows again."""
    a = _my_alarm(db, p, alarm_id)
    try:
        out = _farm().dismiss_alarm(alarm_id, _actor(p))
    except Exception as e:  # noqa: BLE001
        _alarm_error(e)
    event(db, "farm", f"Alarm dismissed: {_alarm_label(a)} (mobile API)", actor=_actor(p))
    db.commit()
    return {"ok": True, **(out or {})}


@router.post("/alarms/{alarm_id}/suppress")
def alarm_suppress(alarm_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """No more alerts for this alarm's code on its printer (no alarm, no notification); dismisses this one."""
    a = _my_alarm(db, p, alarm_id)
    try:
        out = _farm().suppress_alarm(alarm_id, _actor(p))
    except Exception as e:  # noqa: BLE001
        _alarm_error(e)
    event(db, "farm", f"Alerts suppressed: {_alarm_label(a)} (mobile API)", actor=_actor(p))
    db.commit()
    return {"ok": True, "suppression": out}


@router.get("/alarm-suppressions")
def alarm_suppressions(printer: str | None = None, db: Session = Depends(get_db),
                       p: Principal = Depends(need("view"))):
    """The alarm codes someone chose not to be alerted about, per printer, newest first."""
    printer_access.require(db, p, printer)
    try:
        rows = _farm().alarm_suppressions(printer)
    except Exception as e:  # noqa: BLE001
        _alarm_error(e)
    return {"suppressions": printer_access.visible_items(db, p, rows)}


@router.delete("/alarm-suppressions/{suppression_id}")
def alarm_unsuppress(suppression_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """Alert about that code again."""
    try:
        mine = printer_access.visible_items(db, p, _farm().alarm_suppressions())
        row = next((x for x in mine if x.get("id") == suppression_id), None)
        if row is None:
            raise HTTPException(404, "Suppression not found")
        _farm().unsuppress_alarm(suppression_id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        _alarm_error(e, "Suppression")
    event(db, "farm", f"Alerts back on: {_alarm_label(row)} (mobile API)", actor=_actor(p))
    db.commit()
    return {"ok": True}
