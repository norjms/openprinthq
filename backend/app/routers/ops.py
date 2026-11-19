"""Inventory, planner, orders, jobs, settings, API keys, ingest, media and webhooks."""
import base64
import csv
import hashlib
import hmac
import io
import json
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..auth import need, Principal, new_api_key, current
from ..clients import inventree, woo
from ..config import settings
from ..db import get_db
from ..jobs import enqueue, event
from ..models import (Model, Image, Product, PartCache, SpoolCache, FilamentCatalog, Order, Job, ApiKey, Setting,
                      Category)
from ..serialize import model_detail
from ..services import availability, importer, images as imgsvc, themes
from ..services.shop import image_token, set_setting, setting

router = APIRouter()


def _pub(kind, field="public_url"):
    from ..clients import public_url
    return public_url(kind, field)


# ---------- me / stats ----------
@router.get("/api/me")
def me(db: Session = Depends(get_db), p: Principal = Depends(need("read"))):
    from ..clients import public_url
    from ..models import User
    from ..services.teams import user_teams
    woo = public_url("woo", "url")
    dam = public_url("dam", "host")
    teams, prefs = [], {}
    if p.get("user_id"):
        u = db.get(User, p["user_id"])
        teams = user_teams(db, u) if u else []
        prefs = themes.normalize(u.prefs) if u else {}
    return {"user": {k: p.get(k) for k in ("username", "email", "name", "kind", "user_id", "platform_admin")},
            "prefs": prefs,
            "team": {"id": p["team_id"], "slug": p.get("team_slug"), "name": p.get("team_name"), "role": p.get("role")},
            "scopes": p.get("perms") or [], "perms": p.get("perms") or [], "teams": teams,
            "links": {"woo": woo + "/wp-admin/" if woo else None, "dam": f"https://{dam}" if dam and "://" not in dam else dam,
                      "models": settings().models_url or None,
                      "manyfold": public_url("manyfold"), "farm": public_url("openprinthq"),
                      "inventree": public_url("inventree"), "spoolman": public_url("spoolman")}}


class PrefsIn(BaseModel):
    theme: str | None = None
    reduce_transparency: str | None = None
    # Sent by apps from before themes (a skin and a light/dark mode). Accepted and ignored: every look is a theme now.
    skin: str | None = None
    mode: str | None = None


@router.put("/api/me/prefs")
def set_prefs(body: PrefsIn, db: Session = Depends(get_db), p: Principal = Depends(need("read"))):
    """The person's own look: a theme and whether to reduce transparency. API keys have none to keep."""
    from ..models import User
    if body.theme is not None and body.theme not in themes.THEMES:
        raise HTTPException(400, "theme is one of: " + ", ".join(themes.THEMES))
    if body.reduce_transparency is not None and body.reduce_transparency not in themes.TRANSPARENCY:
        raise HTTPException(400, "reduce_transparency is auto, on or off")
    u = db.get(User, p["user_id"]) if p.get("user_id") else None
    if not u:
        return {"saved": False}
    prefs = themes.normalize(u.prefs)
    prefs.update({k: v for k, v in (("theme", body.theme), ("reduce_transparency", body.reduce_transparency))
                  if v is not None})
    u.prefs = prefs
    db.commit()
    return {"saved": True, "prefs": prefs}


def unresolved_failures(db: Session) -> int:
    """Failed jobs that still need a look, for the Sync health badge, which shows a number only when there is a real
    problem. Left out: a failure the same job (kind and ref) has since run successfully, and a slice
    that failed for good (the model doesn't fit and the like), which the Slicing page lists instead."""
    from sqlalchemy import and_, exists, or_
    from sqlalchemy.orm import aliased
    later = aliased(Job)
    resolved = exists().where(and_(later.kind == Job.kind,
                                   or_(later.ref == Job.ref, and_(later.ref.is_(None), Job.ref.is_(None))),
                                   later.state == "done", later.id > Job.id))
    permanent_slice = and_(Job.kind == "slice.model", Job.error.like("PermanentError%"))
    return db.execute(select(func.count(Job.id)).where(Job.state == "failed", ~resolved, ~permanent_slice)).scalar() or 0


@router.get("/api/stats")
def stats(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    models = db.execute(select(func.count(Model.id)).where(Model.status != "archived")).scalar()
    live = db.execute(select(func.count(Product.id)).where(Product.live.is_(True))).scalar()
    on_woo = db.execute(select(func.count(Product.id)).where(Product.woo_product_id.is_not(None))).scalar()
    open_orders = db.execute(select(func.count(Order.id)).where(Order.status.in_(availability.OPEN_STATUSES))).scalar()
    failed = unresolved_failures(db)
    queued = db.execute(select(func.count(Job.id)).where(Job.state.in_(("queued", "running")))).scalar()
    snap = availability.load_snapshot(db)
    blocked = []
    for p in availability.all_products(db, live_only=True):
        a = availability.product_availability(db, p, snap)
        if (a.get("units") or 0) == 0 and a.get("has_bom", True):
            blocked.append({"model_id": p.model_id, "sku": p.sku, "title": p.title})
    return {"models": models, "live": live, "on_woo": on_woo, "open_orders": open_orders, "jobs_failed": failed,
            "jobs_pending": queued, "live_out_of_supplies": blocked,
            "licenses_attention": _license_attention(db),
            "plate_checks": _plate_checks(db),
            "alerts_open": _alerts_open(db),
            "quotes_waiting": _quotes_waiting(db),
            "images": db.execute(select(func.count(Image.id))).scalar()}


def _quotes_waiting(db: Session) -> int:
    from ..models import Quote
    return db.execute(select(func.count(Quote.id)).where(Quote.status == "ordered")).scalar() or 0


def _alerts_open(db: Session) -> int:
    from ..services.alert_inbox import counts
    return counts(db)["open"]


def _plate_checks(db: Session) -> int:
    from .farm import waiting_count
    return waiting_count(db)


def _license_attention(db: Session) -> int:
    from ..models import LicenseGrant
    from ..services import licensing
    return sum(1 for g in db.execute(select(LicenseGrant)).scalars()
               if g.sales_need_active and licensing.grant_status(g)["state"] in ("expiring", "expired"))


# ---------- inventory ----------
@router.get("/api/inventory/parts")
def parts(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    snap = availability.load_snapshot(db)
    used: dict[int, list] = {}
    for p in availability.all_products(db):
        for b in p.parts:
            used.setdefault(b.inventree_part_id, []).append({"model_id": p.model_id, "sku": p.sku, "live": p.live,
                                                             "qty": b.qty})
    out = []
    for pc in db.execute(select(PartCache).order_by(PartCache.name)).scalars():
        out.append({"id": pc.inventree_part_id, "name": pc.name, "ipn": pc.ipn, "category": pc.category,
                    "units": pc.units, "in_stock": pc.in_stock, "allocated": pc.allocated, "on_order": pc.on_order,
                    "minimum_stock": pc.minimum_stock, "open_order_demand": snap.part_demand.get(pc.inventree_part_id, 0),
                    "available": snap.part_available(pc.inventree_part_id), "suppliers": pc.suppliers,
                    "used_by": used.get(pc.inventree_part_id, []),
                    "url": f"{_pub('inventree')}/web/part/{pc.inventree_part_id}/details" if _pub('inventree') else None})
    return out


class NewPart(BaseModel):
    name: str
    category: str = "Hardware"
    ipn: str | None = None
    description: str | None = None
    units: str | None = None
    initial_stock: float = 0
    minimum_stock: float = 0
    supplier: str | None = None
    supplier_sku: str | None = None
    link: str | None = None
    pack_qty: float = 1


@router.post("/api/inventory/parts")
def create_part(body: NewPart, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    inv = inventree()
    cats = {c["pathstring"]: c["pk"] for c in inv.categories()}
    cid = cats.get(body.category)
    if not cid:
        parent = None
        path = ""
        for seg in body.category.split("/"):
            path = f"{path}/{seg}" if path else seg
            if path not in cats:
                cats[path] = inv.create_category(seg, parent)["pk"]
            parent = cats[path]
        cid = parent
    part = inv.create_part({"name": body.name, "category": cid, "IPN": body.ipn or "", "description": body.description
                            or body.name, "units": body.units or "", "minimum_stock": body.minimum_stock,
                            "component": True, "purchaseable": True, "active": True})
    if body.supplier:
        comps = {c["name"].lower(): c["pk"] for c in inv.companies(is_supplier="true")}
        sid = comps.get(body.supplier.lower()) or inv.create_company(body.supplier)["pk"]
        inv.create_supplier_part({"part": part["pk"], "supplier": sid, "SKU": body.supplier_sku or body.name[:90],
                                  "link": body.link or "", "pack_quantity": str(body.pack_qty or 1)})
    if body.initial_stock:
        locs = inv.locations()
        inv.add_stock(part["pk"], body.initial_stock, locs[0]["pk"] if locs else None, "Initial stock via OpenPrintHQ")
    from ..services.inventory import refresh_parts
    refresh_parts(db)
    db.commit()
    return {"id": part["pk"], "name": part["name"]}


class Adjust(BaseModel):
    delta: float
    note: str | None = None


@router.post("/api/inventory/parts/{part_id}/adjust")
def adjust_part(part_id: int, body: Adjust, db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    inv = inventree()
    note = body.note or f"Adjusted in OpenPrintHQ by {p.actor}"
    if body.delta > 0:
        items = inv.stock(part=part_id, in_stock="true")
        locs = inv.locations()
        loc = (items[0].get("location") if items else None) or (locs[0]["pk"] if locs else None)
        inv.add_stock(part_id, body.delta, loc, note)
    elif body.delta < 0:
        left = -body.delta
        batch = []
        for it in inv.stock(part=part_id, in_stock="true"):
            take = min(left, float(it["quantity"]))
            if take > 0:
                batch.append({"pk": it["pk"], "quantity": take})
                left -= take
            if left <= 0:
                break
        if batch:
            inv.remove_stock(batch, note)
    from ..services.inventory import refresh_parts
    refresh_parts(db)
    enqueue(db, "store.sync_stock", ref="stock:all", delay=1)
    db.commit()
    return {"ok": True}


@router.get("/api/inventory/filament")
def filament(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    spools = [{"id": s.spool_id, "filament_id": s.filament_id, "vendor": s.vendor, "name": s.name,
               "material": s.material, "color_hex": s.color_hex, "remaining_g": round(s.remaining_g),
               "location": s.location} for s in db.execute(select(SpoolCache).order_by(SpoolCache.material,
                                                                                      SpoolCache.name)).scalars()]
    catalog = [{"id": f.filament_id, "vendor": f.vendor, "name": f.name, "material": f.material,
                "color_hex": f.color_hex, "weight_g": f.weight_g, "price": f.price}
               for f in db.execute(select(FilamentCatalog).order_by(FilamentCatalog.vendor, FilamentCatalog.name)).scalars()]
    plan = availability.planner(db, include="live")
    return {"spools": spools, "catalog": catalog, "live_needs": plan["filament"],
            "spoolman_url": _pub("spoolman")}


@router.get("/api/planner")
def planner(include: str = "live", db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    return availability.planner(db, include)


@router.get("/api/planner.csv")
def planner_csv(include: str = "live", db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    plan = availability.planner(db, include)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["type", "item", "required", "on_hand", "on_order", "short", "buy", "supplier", "link", "used_by"])
    for f in plan["filament"]:
        if f["short_g"] > 0:
            c = f["catalog"] or {}
            w.writerow(["filament", f["label"], f"{f['required_g']} g", f"{f['on_hand_g']} g", "", f"{f['short_g']} g",
                        f"{f['buy_spools']} spool(s)", c.get("vendor") or "", c.get("url") or "", " ".join(f["used_by"])])
    for p in plan["parts"]:
        if p["short"] > 0:
            s = p["supplier"] or {}
            w.writerow(["part", p["name"], p["required"], p["on_hand"], p["on_order"], p["short"],
                        f"{p['buy_packs']} pack(s)", s.get("name") or "", s.get("link") or "", " ".join(p["used_by"])])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=artisan-shopping-list.csv"})


@router.post("/api/planner/purchase-orders")
def make_pos(db: Session = Depends(get_db), p: Principal = Depends(need("inventory"))):
    plan = availability.planner(db, "live")
    by_supplier: dict = {}
    for item in plan["parts"]:
        s = item["supplier"]
        if item["buy_packs"] > 0 and s and s.get("supplier_id"):
            by_supplier.setdefault(s["supplier_id"], []).append((s["supplier_part_id"], item["buy_packs"] * (s.get("pack_qty") or 1)))
    created = []
    inv = inventree()
    for sid, lines in by_supplier.items():
        po = inv.create_purchase_order(sid, description="OpenPrintHQ shopping list")
        for spid, qty in lines:
            inv.add_po_line(po["pk"], spid, qty)
        created.append(po.get("reference") or po["pk"])
    return {"purchase_orders": created}


# ---------- orders ----------
@router.get("/api/orders")
def orders(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    from ..services.farm import order_print_state, routing
    rows = db.execute(select(Order).order_by(Order.placed_at.desc().nullslast(), Order.id.desc()).limit(200)).scalars()
    hub = routing(db) == "hub"
    from ..services import stores
    return [{"id": o.id, "print": order_print_state(db, o) if hub or o.routed_at else None, "woo_order_id": o.woo_order_id,
             "channel": o.channel or "woo", "store": stores.LABEL.get(o.channel or "woo"), "ext_id": o.ext_id,
             "number": o.number, "status": o.status, "customer": o.customer,
             "total": o.total, "currency": o.currency, "is_test": o.is_test, "consumed": o.consumed,
             "placed_at": o.placed_at.isoformat() if o.placed_at else None,
             **stores.order_links(o),
             "woo_url": f"{_pub('woo', 'url')}/wp-admin/post.php?post={o.woo_order_id}&action=edit"
                        if _pub('woo', 'url') and (o.channel or "woo") == "woo" else None,
             "lines": [{"sku": ln.sku, "name": ln.name, "qty": ln.qty, "product_id": ln.product_id,
                        "variant_id": ln.variant_id, "farm_job_ids": ln.farm_job_ids or [], "farm_error": ln.farm_error,
                        "model_id": db.get(Product, ln.product_id).model_id if ln.product_id else None}
                       for ln in o.lines]} for o in rows]


# ---------- jobs ----------
@router.get("/api/jobs")
def jobs(state: str | None = None, limit: int = 200, db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    q = select(Job).order_by(Job.id.desc()).limit(min(limit, 1000))
    if state:
        q = q.where(Job.state == state)
    else:
        q = q.where(~Job.ref.like("periodic:%") | (Job.state != "done"))
    return [{"id": j.id, "kind": j.kind, "ref": j.ref, "state": j.state, "attempts": j.attempts,
             "error": (j.error or "")[:2000], "result": j.result, "created_by": j.created_by,
             "created_at": j.created_at.isoformat(), "finished_at": j.finished_at.isoformat() if j.finished_at else None,
             "run_after": j.run_after.isoformat()} for j in db.execute(q).scalars()]


@router.get("/api/jobs/{job_id}")
def job_one(job_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    j = db.get(Job, job_id)
    if not j:
        raise HTTPException(404)
    return {"id": j.id, "kind": j.kind, "state": j.state, "attempts": j.attempts, "max_attempts": j.max_attempts,
            "error": (j.error or "")[:2000], "result": j.result}


@router.post("/api/jobs/{job_id}/retry")
def retry(job_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("write"))):
    j = db.get(Job, job_id)
    if not j:
        raise HTTPException(404)
    from ..jobs import utcnow
    j.state, j.attempts, j.run_after, j.error = "queued", 0, utcnow(), None
    db.commit()
    return {"ok": True}


ACTIONS = {"scan-library": ("library.scan_deltaprints", "manual:scan"), "scan-inbox": ("library.scan_inbox", "manual:inbox"),
           "refresh-parts": ("inventory.refresh_parts", "refresh:parts"),
           "refresh-spools": ("inventory.refresh_spools", "refresh:spools"),
           "sync-stock": ("store.sync_stock", "stock:all"), "poll-orders": ("woo.poll_orders", "manual:orders"),
           "poll-shopify-orders": ("shopify.poll_orders", "manual:shopify-orders"),
           "poll-etsy-orders": ("etsy.poll_orders", "manual:etsy-orders"),
           "manyfold-rescan": ("manyfold.rescan", "manyfold:rescan"), "manyfold-link": ("manyfold.link", "manyfold:link"),
           "setup": ("setup.run", "setup"),
           "license-recheck": ("license.recheck", "manual:license-recheck"),
           "spools-migrate-to-farm": ("spools.migrate_to_farm", "spools:migrate")}


@router.post("/api/actions/{name}")
def action(name: str, force: bool = False, db: Session = Depends(get_db), p: Principal = Depends(need("models"))):
    if name not in ACTIONS:
        raise HTTPException(404)
    if name in ("setup", "spools-migrate-to-farm") and "settings" not in (p.get("perms") or []):
        raise HTTPException(403, "you don't have permission to change team settings")
    kind, ref = ACTIONS[name]
    j = enqueue(db, kind, ref=ref, payload={"force": force} if force else {}, actor=p.actor)
    db.commit()
    return {"job": j.id}


# ---------- settings / keys ----------
EDITABLE = {"farm_mirror_library": True, "consume_parts_on_complete": True, "default_target_buffer": 2, "default_stock_cap": 10, "hide_on_license_expiry": False}


def _editable():
    from ..services.slicing import SETTINGS
    return {**EDITABLE, **SETTINGS}


def _check_setting(k, v):
    from zoneinfo import ZoneInfo
    if k == "timezone":
        try:
            ZoneInfo(str(v))
        except Exception:  # noqa: BLE001
            raise HTTPException(400, f"unknown time zone {v!r} (use a name like America/Chicago)")
    if k == "filament_brand" and v not in (None, "", "Bambu Lab"):
        raise HTTPException(400, "filament_brand is Bambu Lab or empty")
    if k == "slice_auto_new" and v not in ("off", "default", "all"):
        raise HTTPException(400, "slice_auto_new is off, default or all")
    if k == "slice_night_hour" and not (isinstance(v, int) and 0 <= v <= 23):
        raise HTTPException(400, "slice_night_hour is an hour from 0 to 23")
    if k == "material_prices":
        if not isinstance(v, dict) or not all(isinstance(x, (int, float)) and x >= 0 for x in v.values()):
            raise HTTPException(400, "material_prices is {material: price per kg}")
        v = {str(a).upper()[:20]: float(b) for a, b in v.items()}
    if k in ("slice_auto_approve_pct", "default_material_price", "printer_hourly_rate"):
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{k} must be a number")
        if v < 0:
            raise HTTPException(400, f"{k} can't be negative")
    return v


@router.get("/api/settings")
def get_settings(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    return {k: setting(db, k, v) for k, v in _editable().items()}


@router.put("/api/settings")
def put_settings(body: dict, db: Session = Depends(get_db), _: Principal = Depends(need("admin"))):
    ed = _editable()
    for k, v in body.items():
        if k in ed:
            set_setting(db, k, _check_setting(k, v))
    db.commit()
    return {k: setting(db, k, v) for k, v in ed.items()}


@router.get("/api/keys")
def keys(db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    return [{"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": k.scopes, "revoked": k.revoked,
             "created_at": k.created_at.isoformat(), "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None}
            for k in db.execute(select(ApiKey).where(ApiKey.team_id == p.team_id).order_by(ApiKey.id)).scalars()]


class KeyIn(BaseModel):
    name: str
    scopes: list[str] = ["read", "write"]


@router.post("/api/keys")
def make_key(body: KeyIn, db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    from ..models import SCOPE_PERMS
    have = set(p.get("perms") or [])
    scopes = [x for x in body.scopes if x in SCOPE_PERMS and set(SCOPE_PERMS[x]) <= have] or ["read"]
    raw = new_api_key(db, body.name, scopes, team_id=p.team_id, user_id=p.get("user_id"))
    db.commit()
    return {"key": raw, "note": "Shown once. Use as: Authorization: Bearer <key>"}


@router.delete("/api/keys/{key_id}")
def revoke_key(key_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    k = db.get(ApiKey, key_id)
    if k and k.team_id == p.team_id:
        k.revoked = True
        db.commit()
    return {"ok": True}


# ---------- machine ingest ----------
@router.post("/api/v1/ingest")
async def ingest(request: Request, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    """Create or update a model in one call.

    JSON body:   {"name", "code"?, "category"?, "designer"?, "summary"?, "description"?, "source_url"?, "price"?,
                  "sku"?, "filament"?, "grams"?, "license_ok"?, "tags"?, "print_settings"?,
                  "image_urls": [...], "file_urls": [...], "images_b64": [{"filename", "data"}]}
    Multipart:   field "meta" (the JSON above) + any number of "files" parts (images and model files).
    """
    ctype = request.headers.get("content-type", "")
    uploads = []
    if ctype.startswith("multipart/"):
        form = await request.form()
        meta = json.loads(form.get("meta") or "{}")
        uploads = [f for k, f in form.multi_items() if k == "files" and hasattr(f, "filename")]
    else:
        meta = await request.json()
    if not meta.get("name") and not meta.get("code"):
        raise HTTPException(400, "name (or an existing code) is required")
    work = Path(settings().data_dir) / "ingest" / uuid.uuid4().hex
    work.mkdir(parents=True)
    try:
        for f in uploads:
            with open(work / Path(f.filename).name, "wb") as out:
                shutil.copyfileobj(f.file, out)
        for item in meta.pop("images_b64", []) or []:
            (work / Path(item["filename"]).name).write_bytes(base64.b64decode(item["data"]))
        with httpx.Client(timeout=120, follow_redirects=True) as hc:
            for url in (meta.pop("image_urls", []) or []) + (meta.pop("file_urls", []) or []):
                r = hc.get(url)
                r.raise_for_status()
                name = Path(httpx.URL(url).path).name or f"{uuid.uuid4().hex}.jpg"
                (work / name).write_bytes(r.content)
        m = importer.import_generic_folder(db, work, meta)
        prod = m.product
        if meta.get("sku"):
            prod.sku = meta["sku"]
        if meta.get("price") is not None:
            prod.price = meta["price"]
        event(db, f"model:{m.id}", f"Created/updated via API by {p.actor}")
        enqueue(db, "dam.sync_model", ref=f"model:{m.id}:dam", payload={"model_id": m.id}, delay=2)
        enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=30)
        db.commit()
        d = model_detail(m)
        return {"id": m.id, "code": m.code, "url": f"{settings().public_url}/models/{m.id}", "model": d}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------- media ----------
@router.get("/media/{image_id}/{size}")
def media(image_id: int, size: str, db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    img = db.get(Image, image_id)
    if not img:
        raise HTTPException(404)
    if size == "original":
        path = Path(settings().data_dir) / img.store_rel
        return FileResponse(path, filename=img.filename, headers={"Cache-Control": "private, max-age=86400"})
    if size not in imgsvc.SIZES:
        raise HTTPException(404)
    path = imgsvc.variant_path(img.sha256, size)
    if not path.exists():
        imgsvc.render(img.sha256, Path(settings().data_dir) / img.store_rel)
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/public/img/{image_id}/{token}.jpg")
def public_image(image_id: int, token: str, db: Session = Depends(get_db)):
    from ..tenant import bind_session
    bind_session(db, None, True)       # signed links work for any team; the token proves the link was issued
    img = db.get(Image, image_id)
    if not img or not hmac.compare_digest(token, image_token(img)):
        raise HTTPException(404)
    path = imgsvc.web_jpeg(img.sha256, Path(settings().data_dir) / img.store_rel)
    return FileResponse(path, media_type="image/jpeg", filename=f"{Path(img.filename).stem}.jpg",
                        headers={"Cache-Control": "public, max-age=86400"})


# ---------- webhooks ----------
def _hook_team(db: Session, slug: str | None) -> int:
    """Webhooks are per team: /hooks/<service>/<team slug>. The bare path belongs to the first team."""
    from ..models import Team
    from ..tenant import bind_session, set_team
    bind_session(db, None, True)
    t = db.get(Team, 1) if not slug else db.execute(select(Team).where(Team.slug == slug)).scalar()
    if not t:
        raise HTTPException(404)
    bind_session(db, t.id)
    set_team(t.id)
    return t.id


@router.post("/hooks/woo")
@router.post("/hooks/woo/{team_slug}")
async def woo_hook(request: Request, team_slug: str | None = None, db: Session = Depends(get_db)):
    from ..services.connections import get as conn
    raw = await request.body()
    topic = request.headers.get("x-wc-webhook-topic", "")
    sig = request.headers.get("x-wc-webhook-signature", "")
    tid = _hook_team(db, team_slug)
    if not topic:            # Woo sends a ping with webhook_id=... when the hook is created
        return {"ok": True}
    secret = ((conn("woo", tid, required=False) or {}).get("webhook_secret") or "").encode()
    if not secret:
        raise HTTPException(401, "store not connected")
    expect = base64.b64encode(hmac.new(secret, raw, hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(sig, expect):
        raise HTTPException(401, "bad signature")
    body = json.loads(raw or b"{}")
    if topic.startswith("order."):
        enqueue(db, "order.ingest", ref=f"woo-order:{body.get('id')}", payload={"order": body})
    elif topic.startswith("product."):
        pid = body.get("id")
        prod = db.execute(select(Product).where(Product.woo_product_id == pid)).scalar() if pid else None
        if prod and body.get("status") and body.get("status") != prod.woo_status:
            prod.woo_status = body["status"]
            prod.live = body["status"] == "publish" and body.get("catalog_visibility", "visible") != "hidden"
            event(db, f"model:{prod.model_id}", f"Status changed in WooCommerce to {body['status']}")
    db.commit()
    return {"ok": True}


@router.post("/hooks/shopify")
@router.post("/hooks/shopify/{team_slug}")
async def shopify_hook(request: Request, team_slug: str | None = None, db: Session = Depends(get_db)):
    """Shopify's order webhooks (X-Shopify-Hmac-Sha256: base64 HMAC of the body with the app's client secret). The
    order is read back from Shopify by a job, so every order goes through the same path as the poll."""
    from ..services import stores
    raw = await request.body()
    tid = _hook_team(db, team_slug)
    if not stores.shopify_signature_ok(raw, request.headers.get("x-shopify-hmac-sha256", ""), tid):
        raise HTTPException(401, "bad signature")
    topic = request.headers.get("x-shopify-topic", "")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "not JSON")
    if topic.startswith("orders/") and body.get("id"):
        ext = body.get("admin_graphql_api_id") or f"gid://shopify/Order/{body['id']}"
        enqueue(db, "stores.ingest_order", ref=f"shopify-order:{body['id']}", payload={"channel": "shopify", "ext_id": ext},
                delay=2)
    db.commit()
    return {"ok": True}


@router.post("/hooks/etsy")
@router.post("/hooks/etsy/{team_slug}")
async def etsy_hook(request: Request, team_slug: str | None = None, db: Session = Depends(get_db)):
    """Etsy's order webhooks (Standard Webhooks signature with the endpoint's signing secret). The body only points
    at the receipt; a job reads it from Etsy."""
    from ..services import stores
    raw = await request.body()
    tid = _hook_team(db, team_slug)
    if not stores.etsy_signature_ok(raw, request.headers, tid):
        raise HTTPException(401, "bad signature")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "not JSON")
    kind = str(body.get("event_type") or "")
    url = str(body.get("resource_url") or "")
    if kind.startswith("order.") and "/receipts/" in url:
        rid = url.split("/receipts/", 1)[1].split("?")[0].split("/")[0]
        if rid.isdigit():
            enqueue(db, "stores.ingest_order", ref=f"etsy-order:{rid}", payload={"channel": "etsy", "ext_id": rid}, delay=2)
    db.commit()
    return {"ok": True}


# ---------- Etsy sign-in ----------
@router.post("/api/stores/etsy/connect")
def etsy_connect(db: Session = Depends(get_db), _: Principal = Depends(need("settings"))):
    """Where to send the shop owner to approve the hub on Etsy (OAuth with PKCE)."""
    from ..services import stores
    from ..services.connections import NotConnected
    try:
        url = stores.etsy_connect(db)
    except NotConnected:
        raise HTTPException(400, "Save the Etsy app's keystring and shared secret first")
    db.commit()
    return {"url": url, "redirect_uri": stores.etsy_redirect_uri()}


@router.get("/oauth/etsy/callback")
def etsy_callback(state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db)):
    from fastapi.responses import RedirectResponse
    from urllib.parse import quote
    from ..jobs import PermanentError
    from ..clients.base import ApiError
    from ..services import stores
    team = state.split(".", 1)[0]
    if error or not code or not team.isdigit():
        return RedirectResponse(f"/connections?etsy=error&msg={quote(error or 'Etsy did not send a sign-in code')}")
    from ..tenant import bind_session, set_team
    bind_session(db, int(team))
    set_team(int(team))
    try:
        info = stores.etsy_finish(db, state, code)
    except (PermanentError, ApiError) as e:
        db.rollback()
        return RedirectResponse(f"/connections?etsy=error&msg={quote(str(getattr(e, 'message', e))[:200])}")
    db.commit()
    return RedirectResponse(f"/connections?etsy=connected&shop={quote(str(info.get('shop_name') or ''))}")


@router.get("/api/stores/etsy")
def etsy_status(db: Session = Depends(get_db), _: Principal = Depends(need("settings"))):
    """The Etsy sign-in state, the callback address to register, and the shop's listing defaults to pick from."""
    from ..services import stores
    from ..services.connections import get
    out = {"configured": bool(get("etsy", required=False)), "connected": stores.connected("etsy"),
           "shop": setting(db, stores.ETSY_SHOP), "redirect_uri": stores.etsy_redirect_uri(),
           "webhook_url": f"{settings().public_url.rstrip('/')}/hooks/etsy"}
    if out["connected"]:
        try:
            out["options"] = stores.etsy_options()
        except Exception as e:  # noqa: BLE001
            out["options_error"] = str(getattr(e, "message", e))[:300]
    return out


@router.delete("/api/stores/etsy")
def etsy_signout(db: Session = Depends(get_db), _: Principal = Depends(need("settings"))):
    from ..services import stores
    stores.etsy_disconnect(db)
    db.commit()
    return {"ok": True}


@router.get("/api/stores")
def stores_connected(_: Principal = Depends(need("read"))):
    """The stores this team sells on (woo, shopify, etsy)."""
    from ..services import stores
    return {"connected": stores.connected_channels(), "labels": stores.LABEL}


class ShipIn(BaseModel):
    company: str | None = None
    number: str | None = None
    url: str | None = None


@router.post("/api/orders/{order_id}/ship")
def ship_order(order_id: int, body: ShipIn, db: Session = Depends(get_db), p: Principal = Depends(need("store"))):
    """Mark an order shipped in its store (Woo completed, Shopify fulfilled, Etsy shipped), with tracking if given."""
    from ..clients.base import ApiError
    from ..services import stores
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404)
    if o.status in ("cancelled", "refunded"):
        raise HTTPException(400, f"order #{o.number} is {o.status}")
    try:
        out = stores.mark_shipped(db, o, body.model_dump(), actor=p.actor)
    except ApiError as e:
        raise HTTPException(502, f"{stores.LABEL.get(o.channel, o.channel)}: {e.message[:300]}")
    db.commit()
    return out


OPHQ_PRINTS = {"print_complete": "queue.job_completed", "print_failed": "queue.job_failed",
               "print_stopped": "queue.job_cancelled"}
OPHQ_ALERTS = {"printer_offline": "printer.offline", "printer_error": "alarm.raised", "ai_failure_detection": "vision.failure_detected"}


def ophq_event_text(name: str, body: dict) -> str:
    """One readable line for the event log from an OpenPrintHQ webhook (never the raw JSON)."""
    label = (name or "event").replace("_", " ").strip().capitalize()
    if (name or "").lower() in ("test", "webhook_test", "notification_test"):
        label = "Test message"
    parts = []
    for k in ("title", "message", "printer_name", "printer", "filename", "reason", "status"):
        v = body.get(k) if isinstance(body, dict) else None
        if isinstance(v, (str, int, float)) and str(v).strip() and str(v) not in parts:
            parts.append(str(v).strip())
    text = f"Farm {label.lower() if label != 'Test message' else 'test message'}"
    if parts:
        text += ": " + " - ".join(parts)
    return text[:500]


@router.post("/hooks/openprinthq")
@router.post("/hooks/openprinthq/{team_slug}")
async def ophq_hook(request: Request, team_slug: str | None = None, db: Session = Depends(get_db)):
    """OpenPrintHQ's signed webhook provider (X-OPHQ-Signature: sha256=HMAC of the body). Job events pull the farm
    queue shortly after (the queue is the source of truth); printer alarms, going offline and failure detection
    raise farm alerts."""
    raw = await request.body()
    _hook_team(db, team_slug)
    secret = (setting(db, "ophq_webhook_secret") or "").encode()
    sig = request.headers.get("x-ophq-signature", "")
    want = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest() if secret else ""
    if not secret or not hmac.compare_digest(sig, want):
        raise HTTPException(401, "bad signature")
    set_setting(db, "farm_webhook_at", time.time())    # the periodic farm sync slows down while hooks arrive
    name = request.headers.get("x-ophq-event", "") or ""
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "not JSON")
    name = name or str(body.get("event") or "")
    event(db, "openprinthq", ophq_event_text(name, body))
    if name in OPHQ_PRINTS:
        # The print webhook carries no queue id: the task finds the queue item that just ended on that printer.
        reason = body.get("reason")
        pdata = {"printer_name": body.get("printer"), "job_name": body.get("filename"),
                 "message": None if reason in (None, "", "Unknown") else reason, "timestamp": body.get("timestamp"),
                 "source": "openprinthq"}
        enqueue(db, "slicing.farm_job", ref=f"ophqprint:{pdata['printer_name']}:{pdata['timestamp']}",
                payload={"event": OPHQ_PRINTS[name], "data": pdata}, delay=5, max_attempts=3)
    if name in OPHQ_ALERTS:
        data = {"printer_name": body.get("printer_name") or body.get("printer"), "message": body.get("message"),
                "code": body.get("error_code") or body.get("code"), "confidence": body.get("confidence")}
        enqueue(db, "farm.event", payload={"event": OPHQ_ALERTS[name], "data": data}, max_attempts=3)
    else:
        enqueue(db, "farm.sync", ref="farm:sync", delay=3)
    db.commit()
    return {"ok": True}


@router.get("/api/v1/availability")
def sku_availability(sku: str, qty: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    """Units buildable right now per SKU (product or color variant). Used by the Woo checkout guard.
    With qty (counts in the same order as sku) the cart is checked as a whole: each SKU's answer is what is left
    after the cart's earlier lines, so two products sharing a scarce part can't both take it."""
    from ..models import Variant
    snap = availability.load_snapshot(db)
    items, names = {}, {}
    skus = [x.strip() for x in sku.split(",") if x.strip()][:50]
    counts = []
    for x in (qty or "").split(","):
        try:
            counts.append(max(0, int(x)))
        except ValueError:
            counts.append(0)
    for i, s in enumerate(skus):
        v = db.execute(select(Variant).where(Variant.sku == s)).scalar()
        prod = v.product if v else db.execute(select(Product).where(Product.sku == s)).scalar()
        if not prod:
            items[s] = None
            continue
        a = availability.product_availability(db, prod, snap)
        if v and a.get("mode") == "options":
            r = a["variants"].get(v.id)
            items[s] = None if (not r or not r["has_bom"]) else r["units"]
        else:
            items[s] = None if not a.get("has_bom", True) else a.get("units")
        names[s] = prod.title + (f" ({v.color_name})" if v else "")
        if qty is not None and i < len(counts) and counts[i]:
            snap.reserve(prod, v, min(counts[i], items[s]) if items[s] is not None else counts[i])
    return {"items": items, "names": names}


@router.get("/healthz")
def health(response: Response):
    """Ready when the database answers and every schema migration has run. 503 otherwise, so an installer or
    orchestrator can wait on it. "ok" stays for existing healthchecks."""
    from sqlalchemy import text
    from ..db import MIGRATIONS, SessionLocal
    from ..models import Installation
    s = SessionLocal()
    try:
        s.execute(select(1))
        done = {r[0] for r in s.execute(text("SELECT v FROM schema_version"))}
        row = s.get(Installation, 1)
    except Exception:  # noqa: BLE001  any database failure means not ready
        response.status_code = 503
        return {"ok": False, "database": "unavailable", "migrations": "unknown"}
    finally:
        s.close()
    pending = [v for v, _ in MIGRATIONS if v not in done]
    ready = not pending and row is not None
    if not ready:
        response.status_code = 503
    return {"ok": ready, "database": "ok", "migrations": "pending" if pending else "done",
            "schema_version": max(done) if done else 0, "setup_complete": bool(row and row.setup_completed_at)}


@router.get("/api/theme/look", include_in_schema=False)
def theme_look(request: Request, db: Session = Depends(get_db)):
    """The person's look for the supporting apps (Spoolman, Manyfold and others). npmplus calls this on their behalf
    with the shared proxy key and the email Authentik signed them in with; anyone else gets nothing."""
    from ..models import User
    key = settings().theme_proxy_key
    sent = request.headers.get("x-theme-proxy-key") or ""
    email = (request.headers.get("x-theme-user") or "").strip().lower()
    if not key or not hmac.compare_digest(sent, key) or not email:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    u = db.scalars(select(User).where(func.lower(User.email) == email).order_by(User.last_login_at.desc().nullslast())).first()
    prefs = themes.normalize(u.prefs if u else None)
    look = {"theme": prefs["theme"], "reduce_transparency": prefs["reduce_transparency"]}
    return Response(content=json.dumps(look), media_type="application/json",
                    headers={"Cache-Control": "private, max-age=60"})
