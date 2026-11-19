"""The team's one filament library (2026-10-23).

When the team's farm is OpenPrintHQ, the farm keeps the library: the engine tenant's Spoolman, reached through the
engine's /spoolman/inventory routes (clients/openprinthq_api.py lib_* methods). Every spool read and write in the
hub goes there, the spool cache (SpoolCache, FilamentCatalog) is filled from it, and spools carry scan codes
(S<spool id>), locations with codes (L + 6) and slot assignments with an extruder.

Otherwise (no farm connected) the hub's own LAN Spoolman connection stays the library, as before.

spools.migrate_to_farm copies the hub's Spoolman into the farm library once (idempotent) and then turns the hub's
spoolman connection off (it is kept, not deleted).
"""
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.base import ApiError

log = logging.getLogger("hub.spool_library")


class LibraryError(Exception):
    """A library call failed; status and detail are passed on to the caller (409 keeps the engine's allowed list)."""

    def __init__(self, status: int, detail: str, allowed: list | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.allowed = allowed


def farm_library(team_id: int | None = None) -> bool:
    """The farm keeps the spool library once the farm is connected."""
    from .connections import is_connected
    return is_connected("openprinthq", team_id)


def _ophq():
    from ..clients import openprinthq
    return openprinthq()


def _call(fn, *a, **kw):
    """Run one engine call; ApiError becomes LibraryError with the engine's own message (and allowed on 409)."""
    try:
        return fn(*a, **kw)
    except ApiError as e:
        detail, allowed = e.message, None
        try:
            doc = json.loads(e.message)
            d = doc.get("detail")
            if isinstance(d, dict):
                d = d.get("message") or json.dumps(d)
            detail, allowed = (d or e.message), doc.get("allowed")
        except (ValueError, AttributeError, TypeError):
            pass
        status = e.status if e.status in (400, 404, 409, 422) else 502
        raise LibraryError(status, str(detail)[:500], allowed)


# ---------------------------------------------------------------- helpers

def hexc(v) -> str | None:
    v = (v or "").strip().lstrip("#")
    return "#" + v[:6].upper() if re.fullmatch(r"[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", v) else None


def subtype_of(name: str | None, material: str | None) -> str:
    """The part of a filament name after its material (the engine names filaments "<material> <subtype>")."""
    n = (name or "").strip()
    m = (material or "").strip()
    if m and n.upper().startswith(m.upper()):
        n = n[len(m):].strip()
    return n.lower()


def slot_label(ams_id, tray_id, nozzle_count: int = 1) -> str | None:
    """The engine's slot names: AMS A slot 2, HT 1, External left/right (or External on one nozzle)."""
    if ams_id is None:
        return None
    a, t = int(ams_id), int(tray_id or 0)
    if a == 255:
        if int(nozzle_count or 1) > 1:
            return "External left" if t == 0 else "External right"
        return "External"
    if a >= 128:
        return f"HT {a - 127}"
    return f"AMS {chr(65 + a)} slot {t + 1}"


def filament_key(vendor, material, name, color) -> tuple:
    return ((vendor or "").strip().lower(), (material or "").strip().upper(), subtype_of(name, material),
            (hexc(color) or "").upper())


def _filament_dict(f: dict) -> dict:
    """An engine (normalised Spoolman) filament in the mobile shape."""
    v = f.get("vendor") or {}
    return {"id": f.get("id"), "vendor": v.get("name") if isinstance(v, dict) else v, "name": f.get("name"),
            "material": f.get("material"), "color_hex": hexc(f.get("color_hex")), "color_name": f.get("color_name"),
            "weight_g": f.get("weight"), "spool_weight_g": f.get("spool_weight"), "diameter_mm": f.get("diameter"),
            "source": "farm"}


def _spool_name(s: dict) -> str:
    return s.get("color_name") or (s.get("subtype") or "").strip() or s.get("material") or "Spool"


def farm_spool_dict(s: dict, ctx: dict) -> dict:
    """An engine spool (InventorySpool shape) in the mobile shape, with its code, location code, slot and barcode."""
    sid = int(s["id"])
    label = float(s.get("label_weight") or 0)
    remaining = max(0.0, label - float(s.get("weight_used") or 0)) if label else 0.0
    fid = ctx.get("fil_by_key", {}).get(filament_key(s.get("brand"), s.get("material"),
                                                     f"{s.get('material') or ''} {s.get('subtype') or ''}", s.get("rgba")))
    a = ctx.get("assigned", {}).get(sid)
    loc = ctx.get("locs", {}).get(s.get("location_id"))
    return {"id": sid, "code": f"S{sid}", "filament_id": fid, "vendor": s.get("brand"), "name": _spool_name(s),
            "material": s.get("material"), "subtype": s.get("subtype"), "color_hex": hexc(s.get("rgba")),
            "color_name": s.get("color_name"), "remaining_g": round(remaining, 1), "label_weight_g": label or None,
            "location": s.get("storage_location") or (loc or {}).get("name"), "location_id": s.get("location_id"),
            "location_code": (loc or {}).get("code"), "assigned": a, "barcode": ctx.get("barcodes", {}).get(fid),
            "archived": bool(s.get("archived_at")), "note": s.get("note"), "slicer_preset": s.get("slicer_filament_name"),
            "source": "farm"}


def _context(db: Session, c, want=("filaments", "assigned", "locs", "barcodes")) -> dict:
    """Lookups that turn engine spools into mobile spools; parts that fail are left empty."""
    from ..models import FilamentBarcode
    ctx: dict = {"fil_by_key": {}, "assigned": {}, "locs": {}, "barcodes": {}}
    if "filaments" in want:
        try:
            for f in c.lib_filaments():
                v = f.get("vendor") or {}
                ctx["fil_by_key"].setdefault(filament_key(v.get("name"), f.get("material"), f.get("name"),
                                                          f.get("color_hex")), f.get("id"))
        except ApiError as e:
            log.warning("farm filaments: %s", e)
    if "assigned" in want:
        try:
            ncount = {p.get("id"): int(p.get("nozzle_count") or 1) for p in c._printers_raw()}
            for x in c.lib_assignments():
                ctx["assigned"][int(x["spoolman_spool_id"])] = {
                    "printer": x.get("printer_name"), "printer_id": x.get("printer_id"), "ams_id": x.get("ams_id"),
                    "tray_id": x.get("tray_id"), "extruder_index": x.get("extruder_index"),
                    "slot_label": slot_label(x.get("ams_id"), x.get("tray_id"), ncount.get(x.get("printer_id"), 1))}
        except ApiError as e:
            log.warning("farm slot assignments: %s", e)
    if "locs" in want:
        try:
            ctx["locs"] = {x["id"]: x for x in c.locations()}
        except ApiError as e:
            log.warning("farm locations: %s", e)
    if "barcodes" in want:
        for b in db.execute(select(FilamentBarcode).where(FilamentBarcode.filament_id.is_not(None))).scalars():
            ctx["barcodes"].setdefault(b.filament_id, b.barcode)
    return ctx


def _cache_spool_dict(s) -> dict:
    return {"id": s.spool_id, "code": f"S{s.spool_id}", "filament_id": s.filament_id, "vendor": s.vendor,
            "name": s.name, "material": s.material, "color_hex": s.color_hex, "remaining_g": round(s.remaining_g or 0, 1),
            "location": s.location, "location_id": None, "location_code": None, "assigned": None, "barcode": None,
            "archived": bool(s.archived), "source": "spoolman"}


def _match(x: dict, q: str | None, location: str | None, material: str | None) -> bool:
    if material and (x.get("material") or "").upper() != material.upper() and \
            not (x.get("material") or "").upper().startswith(material.upper()):
        return False
    if location:
        lw = location.strip().lower()
        if lw not in ((x.get("location") or "").lower(), (x.get("location_code") or "").lower()) and \
                lw not in (x.get("location") or "").lower():
            return False
    if q:
        hay = " ".join(str(x.get(k) or "") for k in ("code", "vendor", "name", "material", "color_name", "location",
                                                     "barcode", "note")).lower()
        if not all(w in hay for w in q.lower().split()):
            return False
    return True


# ---------------------------------------------------------------- spools

def list_spools(db: Session, q: str | None = None, location: str | None = None, material: str | None = None) -> list[dict]:
    if farm_library():
        c = _ophq()
        rows = _call(c.lib_spools)
        ctx = _context(db, c)
        out = [farm_spool_dict(s, ctx) for s in rows if not s.get("archived_at")]
    else:
        from ..models import SpoolCache
        b = _barcodes_by_filament(db)
        out = []
        for s in db.execute(select(SpoolCache).where(SpoolCache.archived.is_(False))).scalars():
            d = _cache_spool_dict(s)
            d["barcode"] = b.get(s.filament_id)
            out.append(d)
    out = [x for x in out if _match(x, q, location, material)]
    out.sort(key=lambda x: ((x.get("material") or ""), (x.get("name") or ""), x["id"]))
    return out


def spool_summary(s: dict) -> dict:
    """A short spool for location and slot views: what GET /spools shows, without the details."""
    a = s.get("assigned") or {}
    return {"id": s["id"], "code": s["code"], "vendor": s.get("vendor"), "name": s.get("name"),
            "material": s.get("material"), "color_hex": s.get("color_hex"), "remaining_g": s.get("remaining_g"),
            "printer": a.get("printer"), "slot_label": a.get("slot_label")}


def spools_by_location(db: Session) -> dict:
    """{location id: [spool summary]} from the farm library."""
    out: dict = {}
    for s in list_spools(db):
        if s.get("location_id") is not None:
            out.setdefault(s["location_id"], []).append(spool_summary(s))
    return out


def sync_printer_locations(db: Session, force: bool = False) -> dict:
    """Ask the engine to make the printer, AMS, slot and external locations (idempotent on the engine). Runs when
    a printer has come online since the last run (its AMS is only known while it reports), or every 30 minutes."""
    import time
    from ..kv import setting, set_setting
    if not farm_library():
        return {"skipped": "The farm is not connected"}
    c = _ophq()
    online = sorted(p["name"] for p in c.printers().get("printers", []) if p.get("online"))
    before = set(setting(db, "ophq_online_printers") or [])
    last = float(setting(db, "ophq_locations_synced_at") or 0)
    new = [n for n in online if n not in before]
    set_setting(db, "ophq_online_printers", online)
    if not (force or new or time.time() - last >= 1800):
        return {"skipped": "nothing new", "online": online}
    rows = c.sync_printer_locations()
    set_setting(db, "ophq_locations_synced_at", time.time())
    return {"locations": len(rows), "online": online, "came_online": new}


def _barcodes_by_filament(db: Session) -> dict:
    from ..models import FilamentBarcode
    out: dict = {}
    for b in db.execute(select(FilamentBarcode).where(FilamentBarcode.filament_id.is_not(None))).scalars():
        out.setdefault(b.filament_id, b.barcode)
    return out


def get_spool(db: Session, spool_id: int) -> dict:
    if farm_library():
        c = _ophq()
        s = _call(c.lib_spool, spool_id)
        return farm_spool_dict(s, _context(db, c))
    from ..clients import spoolman
    try:
        s = spoolman().spool(spool_id)
    except ApiError as e:
        raise LibraryError(404 if e.status == 404 else 502, e.message[:300])
    f = s.get("filament") or {}
    return {"id": s["id"], "code": f"S{s['id']}", "filament_id": f.get("id"), "vendor": (f.get("vendor") or {}).get("name"),
            "name": f.get("name"), "material": f.get("material"), "color_hex": hexc(f.get("color_hex")),
            "remaining_g": round(float(s.get("remaining_weight") or 0), 1), "location": s.get("location"),
            "location_id": None, "location_code": None, "assigned": None,
            "barcode": _barcodes_by_filament(db).get(f.get("id")), "archived": bool(s.get("archived")),
            "note": s.get("comment"), "source": "spoolman"}


def location_by_code(code: str) -> dict:
    if not farm_library():
        raise LibraryError(400, "locations with codes need the farm as the farm")
    return _call(_ophq().location_by_code, code)


def create_spools(db: Session, body: dict, actor: str | None = None) -> dict:
    """Create quantity spools of one filament (made, with its vendor, when only the filament is described).
    body: filament_id | filament {vendor, name, material, color_hex, weight_g, spool_weight_g, price, barcode,
    color_name}, quantity, remaining_g, location_code, lot."""
    qty = int(body.get("quantity") or 1)
    if not 1 <= qty <= 50:
        raise LibraryError(422, "quantity is 1 to 50")
    from .material_names import clean_filament
    fil = clean_filament(body.get("filament") or {})
    fid = body.get("filament_id")
    if not fid and not fil.get("material"):
        raise LibraryError(422, "give filament_id, or a filament with at least its material")
    note = " ".join(x for x in [f"Lot {body['lot']}" if body.get("lot") else None, body.get("note")] if x) or None
    if farm_library():
        c = _ophq()
        spool: dict = {"note": note}
        if fid:
            spool["spoolman_filament_id"] = int(fid)
            label = None
            if body.get("remaining_g") is not None:
                f = next((x for x in _call(c.lib_filaments) if x.get("id") == int(fid)), None)
                if not f:
                    raise LibraryError(404, f"no filament {fid} in the farm library")
                label = float(f.get("weight") or 1000)
                spool["label_weight"] = int(label)
        else:
            label = float(fil.get("weight_g") or 1000)
            mat = str(fil["material"]).strip()
            nm = (fil.get("name") or "").strip()
            sub = nm[len(mat):].strip() if nm.upper().startswith(mat.upper()) else nm
            spool.update({"material": mat[:64], "subtype": sub[:64] or None,
                          "brand": (fil.get("vendor") or "").strip()[:128] or None,
                          "rgba": (hexc(fil.get("color_hex")) or "#808080")[1:] + "FF",
                          "label_weight": int(label),
                          "color_name": (fil.get("color_name") or "").strip()[:64] or None})
            if fil.get("price") and label:
                spool["cost_per_kg"] = round(float(fil["price"]) / label * 1000, 2)
            if fil.get("spool_weight_g") is not None:
                spool["core_weight"] = int(fil["spool_weight_g"])
        if body.get("remaining_g") is not None and label:
            spool["weight_used"] = max(0.0, label - float(body["remaining_g"]))
        if body.get("location_code"):
            loc = _call(c.location_by_code, body["location_code"])
            spool["location_id"] = loc["id"]
        spool = {k: v for k, v in spool.items() if v is not None}
        created = _call(c.lib_create_spools, spool, qty)
        ctx = _context(db, c)
        out = [farm_spool_dict(s, ctx) for s in created]
        fid = fid or (out[0]["filament_id"] if out else None)
        f = next((x for x in _call(c.lib_filaments) if x.get("id") == fid), None) if fid else None
        filament = _filament_dict(f) if f else None
    else:
        from ..clients import spoolman
        sm = spoolman()
        try:
            if not fid:
                vendors = {v["name"].lower(): v["id"] for v in sm.vendors()}
                vname = (fil.get("vendor") or "").strip()
                vid = vendors.get(vname.lower()) if vname else None
                if vname and not vid:
                    vid = sm.create_vendor(vname)["id"]
                fb = {"name": fil.get("name") or fil["material"], "material": fil["material"],
                      "density": 1.24, "diameter": float(fil.get("diameter_mm") or 1.75),
                      "weight": float(fil.get("weight_g") or 1000)}
                if vid:
                    fb["vendor_id"] = vid
                if hexc(fil.get("color_hex")):
                    fb["color_hex"] = hexc(fil["color_hex"])[1:]
                if fil.get("price"):
                    fb["price"] = float(fil["price"])
                if fil.get("spool_weight_g") is not None:
                    fb["spool_weight"] = float(fil["spool_weight_g"])
                fid = sm.create_filament(fb)["id"]
            created = []
            for _ in range(qty):
                sb = {"filament_id": int(fid), "comment": note}
                if body.get("remaining_g") is not None:
                    sb["remaining_weight"] = float(body["remaining_g"])
                created.append(sm.create_spool({k: v for k, v in sb.items() if v is not None}))
        except ApiError as e:
            raise LibraryError(502, e.message[:300])
        from .inventory import refresh_spools
        refresh_spools(db)
        out = [get_spool(db, s["id"]) for s in created]
        f = next((x for x in sm.filaments() if x.get("id") == int(fid)), None)
        filament = {"id": f["id"], "vendor": (f.get("vendor") or {}).get("name"), "name": f.get("name"),
                    "material": f.get("material"), "color_hex": hexc(f.get("color_hex")), "weight_g": f.get("weight"),
                    "source": "spoolman"} if f else None
    if fil.get("barcode") and filament and filament.get("id"):
        from .barcodes import learn
        learn(db, fil["barcode"], filament["id"], actor=actor, filament=filament)
        for x in out:
            x["barcode"] = x.get("barcode") or fil["barcode"]
    if farm_library():
        refresh_cache(db)
    return {"spools": out, "filament": filament}


def patch_spool(db: Session, spool_id: int, data: dict) -> dict:
    """remaining_weight (a weigh-in), location (free text) and comment."""
    if farm_library():
        c = _ophq()
        body: dict = {}
        if data.get("remaining_weight") is not None:
            cur = _call(c.lib_spool, spool_id)
            label = float(cur.get("label_weight") or 1000)
            body["weight_used"] = round(max(0.0, label - float(data["remaining_weight"])), 2)
        if data.get("location") is not None:
            body["storage_location"] = data["location"] or None
        if data.get("comment") is not None:
            body["note"] = data["comment"]
        out = _call(c.lib_patch_spool, spool_id, body)
        refresh_cache(db)
        return farm_spool_dict(out, _context(db, c))
    from ..clients import spoolman
    from .inventory import refresh_spools
    try:
        out = spoolman().patch_spool(spool_id, data)
    except ApiError as e:
        raise LibraryError(404 if e.status == 404 else 502, e.message[:300])
    refresh_spools(db)
    return out


def use(db: Session, spool_id: int, grams: float) -> None:
    if farm_library():
        _call(_ophq().lib_use, spool_id, grams)
        refresh_cache(db)
        return
    from ..clients import spoolman
    from .inventory import refresh_spools
    try:
        spoolman().use(spool_id, grams)
    except ApiError as e:
        raise LibraryError(404 if e.status == 404 else 502, e.message[:300])
    refresh_spools(db)


def archive(db: Session, spool_id: int) -> dict:
    if farm_library():
        out = _call(_ophq().lib_archive, spool_id)
        refresh_cache(db)
        return {"ok": True, "id": spool_id, "archived": bool((out or {}).get("archived_at", True))}
    from ..clients import spoolman
    from .inventory import refresh_spools
    try:
        spoolman().patch_spool(spool_id, {"archived": True})
    except ApiError as e:
        raise LibraryError(404 if e.status == 404 else 502, e.message[:300])
    refresh_spools(db)
    return {"ok": True, "id": spool_id, "archived": True}


def move(db: Session, spool_id: int, location_code: str) -> dict:
    if not farm_library():
        raise LibraryError(400, "moving by location code needs the farm as the farm (use PATCH location instead)")
    c = _ophq()
    loc = _call(c.location_by_code, location_code)
    out = _call(c.lib_patch_spool, spool_id, {"location_id": loc["id"]})
    refresh_cache(db)
    return farm_spool_dict(out, _context(db, c))


def assign(db: Session, spool_id: int, target_code: str | None = None, printer: str | None = None,
           ams_id: int | None = None, tray_id: int | None = None, extruder_index: int | None = None) -> dict:
    """Put a spool in a printer slot: by a slot location's code (L...) or by printer name + ams_id + tray_id."""
    if not farm_library():
        raise LibraryError(400, "slot assignments need the farm as the farm")
    c = _ophq()
    if target_code:
        loc = _call(c.location_by_code, target_code)
        if loc.get("kind") not in ("ams_slot", "external") or loc.get("printer_id") is None or loc.get("ams_id") is None:
            raise LibraryError(422, f"{loc.get('code') or target_code} is a {loc.get('kind')} location, not a printer slot; "
                                    "scan a slot (AMS slot or external holder)")
        pid, ams_id, tray_id = int(loc["printer_id"]), int(loc["ams_id"]), int(loc.get("tray_id") or 0)
    else:
        if printer is None or ams_id is None or tray_id is None:
            raise LibraryError(422, "give target_code, or printer with ams_id and tray_id")
        pid = _call(c.printer_id, printer)
    out = _call(c.lib_assign, spool_id, pid, ams_id, tray_id, extruder_index)
    refresh_cache(db)
    d = farm_spool_dict(out, _context(db, c))
    d["extruder_index"] = out.get("extruder_index")
    d["slot_location_id"] = out.get("slot_location_id")
    return d


def unassign(db: Session, spool_id: int) -> dict:
    if not farm_library():
        raise LibraryError(400, "slot assignments need the farm as the farm")
    c = _ophq()
    _call(c.lib_unassign, spool_id)
    return farm_spool_dict(_call(c.lib_spool, spool_id), _context(db, c))


# ---------------------------------------------------------------- filaments

def library_filaments(db: Session) -> list[dict]:
    if farm_library():
        return [_filament_dict(f) for f in _call(_ophq().lib_filaments)]
    from ..models import FilamentCatalog
    return [{"id": f.filament_id, "vendor": f.vendor, "name": f.name, "material": f.material, "color_hex": f.color_hex,
             "weight_g": f.weight_g, "source": "spoolman"} for f in db.execute(select(FilamentCatalog)).scalars()]


def catalog_hits(q: str | None, material: str | None = None, limit: int = 30) -> list[dict]:
    """SpoolmanDB (the community filament catalog) entries matching the words of q."""
    from . import filaments as fsvc
    words = [w for w in (q or "").lower().split() if w]
    if not words and not material:
        return []
    out = []
    try:
        cat = fsvc.spoolmandb() or []
    except Exception:  # noqa: BLE001
        return []
    for x in cat:
        mat = x.get("material") or ""
        if material and not mat.upper().startswith(material.upper()):
            continue
        hay = f"{x.get('manufacturer') or ''} {mat} {x.get('name') or ''}".lower()
        if words and not all(w in hay for w in words):
            continue
        out.append({"id": None, "catalog_id": x.get("id"), "vendor": x.get("manufacturer"), "name": x.get("name"),
                    "material": mat, "color_hex": hexc(x.get("color_hex")), "weight_g": x.get("weight"),
                    "spool_weight_g": x.get("spool_weight"), "diameter_mm": x.get("diameter"), "source": "catalog"})
        if len(out) >= limit:
            break
    return out


def search_filaments(db: Session, q: str | None = None, material: str | None = None, catalog: bool = True) -> list[dict]:
    words = [w for w in (q or "").lower().split() if w]
    lib = []
    for f in library_filaments(db):
        if material and not (f.get("material") or "").upper().startswith(material.upper()):
            continue
        hay = f"{f.get('vendor') or ''} {f.get('material') or ''} {f.get('name') or ''} {f.get('color_name') or ''}".lower()
        if words and not all(w in hay for w in words):
            continue
        lib.append(f)
    b = _barcodes_by_filament(db)
    for f in lib:
        f["barcode"] = b.get(f["id"])
    return lib + (catalog_hits(q, material) if catalog else [])


# ---------------------------------------------------------------- the hub's cache

def refresh_cache(db: Session) -> dict:
    """SpoolCache and FilamentCatalog from the farm library (what the rest of the hub reads)."""
    from ..models import FilamentCatalog, SpoolCache
    from .inventory import _fill, _row
    from sqlalchemy import delete
    c = _ophq()
    spools = c.lib_spools()
    fils = c.lib_filaments()
    by_key = {}
    for f in fils:
        v = f.get("vendor") or {}
        by_key.setdefault(filament_key(v.get("name"), f.get("material"), f.get("name"), f.get("color_hex")), f)
    seen = set()
    now = datetime.now(timezone.utc)
    for s in spools:
        if s.get("archived_at"):
            continue
        label = float(s.get("label_weight") or 0)
        f = by_key.get(filament_key(s.get("brand"), s.get("material"),
                                    f"{s.get('material') or ''} {s.get('subtype') or ''}", s.get("rgba")))
        _fill(_row(db, SpoolCache, "spool_id", int(s["id"])), filament_id=(f or {}).get("id"), vendor=s.get("brand"),
              name=_spool_name(s), material=s.get("material"), color_hex=hexc(s.get("rgba")),
              remaining_g=max(0.0, label - float(s.get("weight_used") or 0)) if label else 0.0,
              spool_weight_g=label or None, location=(s.get("storage_location") or None) and str(s["storage_location"])[:120],
              archived=False, updated_at=now)
        seen.add(int(s["id"]))
    db.execute(delete(SpoolCache).where(SpoolCache.spool_id.not_in(seen or {-1})))
    fseen = set()
    for f in fils:
        v = f.get("vendor") or {}
        _fill(_row(db, FilamentCatalog, "filament_id", int(f["id"])), vendor=v.get("name"), name=f.get("name"),
              material=f.get("material"), color_hex=hexc(f.get("color_hex")), weight_g=f.get("weight"),
              price=f.get("price"), article_number=f.get("article_number"), url=None)
        fseen.add(int(f["id"]))
    db.execute(delete(FilamentCatalog).where(FilamentCatalog.filament_id.not_in(fseen or {-1})))
    db.flush()
    return {"spools": len(seen), "filaments": len(fseen), "source": "farm"}


# ---------------------------------------------------------------- one-time move into the farm library

def migrate_to_farm(db: Session, dry_run: bool = False, disable: bool = True) -> dict:
    """Copy the hub's Spoolman (vendors, filaments, spools) into the farm library, once. Spools already copied
    (farm_library_migration rows) are skipped, so it can run again after a failure. Vendors come along with their
    filaments and filaments with their spools (the engine makes them when it creates a spool); a filament with no
    spool cannot be made through the engine and is listed under filaments_without_spools. Afterwards, product
    filament references (variants and materials lists) are moved to the farm's filament ids, and the hub's spoolman
    connection is turned off (not deleted) when every spool made it."""
    from ..clients import spoolman
    from ..models import BomFilament, Connection, FarmLibraryMigration as Mig, Variant
    from .connections import forget
    if not farm_library():
        raise LibraryError(400, "Connect the farm first (Connections)")
    sm = spoolman()
    c = _ophq()
    src_spools = sm.spools(allow_archived=True)
    src_fils = sm.filaments()
    done = {(m.kind, m.source_id): m for m in db.execute(select(Mig)).scalars()}
    report = {"dry_run": dry_run, "spools_copied": 0, "spools_already": 0, "spools_failed": [], "filaments_mapped": 0,
              "filaments_without_spools": [], "references_moved": 0, "spoolman_disabled": False}
    farm_fils = c.lib_filaments()

    def farm_fid(f: dict) -> int | None:
        v = f.get("vendor") or {}
        want = filament_key(v.get("name"), f.get("material"), f.get("name"), f.get("color_hex"))
        for x in farm_fils:
            xv = x.get("vendor") or {}
            if filament_key(xv.get("name"), x.get("material"), x.get("name"), x.get("color_hex")) == want:
                return x.get("id")
        return None

    used_fils = set()
    for s in src_spools:
        f = s.get("filament") or {}
        if f.get("id"):
            used_fils.add(f["id"])
        if ("spool", s["id"]) in done:
            report["spools_already"] += 1
            continue
        v = f.get("vendor") or {}
        mat = (f.get("material") or "PLA").strip()
        name = (f.get("name") or "").strip()
        label = float(f.get("weight") or 1000)
        remaining = s.get("remaining_weight")
        body = {"material": mat[:64],
                "subtype": (name[len(mat):].strip() if name.upper().startswith(mat.upper()) else name)[:64] or None,
                "brand": (v.get("name") or "")[:128] or None,
                "rgba": (hexc(f.get("color_hex")) or "#808080")[1:] + "FF", "label_weight": int(label),
                "weight_used": max(0.0, min(label, label - float(remaining))) if remaining is not None else 0.0,
                "note": " ".join(x for x in [s.get("comment"), f"(hub Spoolman spool #{s['id']})"] if x)[:1000],
                "storage_location": (s.get("location") or None)}
        if f.get("price") and label:
            body["cost_per_kg"] = round(float(f["price"]) / label * 1000, 2)
        body = {k: v for k, v in body.items() if v is not None}
        if dry_run:
            report["spools_copied"] += 1
            continue
        try:
            made = c.lib_create_spools(body, 1)[0]
            if s.get("archived"):
                c.lib_archive(made["id"])
        except (ApiError, IndexError, KeyError) as e:
            report["spools_failed"].append({"id": s["id"], "error": str(getattr(e, "message", e))[:200]})
            continue
        db.add(Mig(kind="spool", source_id=int(s["id"]), farm_id=int(made["id"]),
                   note=f"hub_migrated_from={s['id']}"))
        db.flush()
        report["spools_copied"] += 1
    farm_fils = c.lib_filaments() if not dry_run else farm_fils
    fmap: dict[int, int] = {}
    for f in src_fils:
        m = done.get(("filament", f["id"]))
        if m and m.farm_id:
            fmap[f["id"]] = m.farm_id
            continue
        fid = farm_fid(f)
        if fid is None:
            if f["id"] not in used_fils:
                report["filaments_without_spools"].append({"id": f["id"], "name": f.get("name"),
                                                           "vendor": (f.get("vendor") or {}).get("name")})
            continue
        fmap[f["id"]] = fid
        if not dry_run:
            db.add(Mig(kind="filament", source_id=int(f["id"]), farm_id=int(fid)))
            v = f.get("vendor") or {}
            if v.get("id") and ("vendor", v["id"]) not in done:
                db.add(Mig(kind="vendor", source_id=int(v["id"]), farm_id=None, note=v.get("name")))
                done[("vendor", v["id"])] = True
    report["filaments_mapped"] = len(fmap)
    if not dry_run:
        db.flush()
        # Product filament references point at Spoolman filament ids: move them to the farm's, once per row.
        moved = 0
        new = {m.source_id: m.farm_id for m in db.execute(select(Mig).where(Mig.kind == "filament")).scalars()}
        remapped = set((_get_setting(db, "spool_migration_remapped") or []))
        for cls in (Variant, BomFilament):
            for row in db.execute(select(cls).where(cls.spoolman_filament_id.is_not(None))).scalars():
                key = f"{cls.__tablename__}:{row.id}"
                if key in remapped or row.spoolman_filament_id not in new:
                    continue
                row.spoolman_filament_id = new[row.spoolman_filament_id]
                remapped.add(key)
                moved += 1
        _set_setting(db, "spool_migration_remapped", sorted(remapped))
        report["references_moved"] = moved
        if disable and not report["spools_failed"]:
            row = db.execute(select(Connection).where(Connection.kind == "spoolman")).scalar()
            if row and row.enabled:
                row.enabled = False
                db.flush()
                forget(row.team_id, "spoolman")
            report["spoolman_disabled"] = True
        refresh_cache(db)
    return report


def _get_setting(db, key):
    from ..kv import setting
    return setting(db, key)


def _set_setting(db, key, value):
    from ..kv import set_setting
    set_setting(db, key, value)
