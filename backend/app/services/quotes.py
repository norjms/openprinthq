"""Instant quotes: a customer uploads a model on the store, the hub slices it and prices it, the customer pays, and
the order waits for review before anything goes to the farm.

Price per order = max(minimum, (filament + machine time) x markup x quantity + setup fee). Filament is the sliced
grams (scaled to the chosen material's density) at that material's price per kg; machine time is the quote
printer's hourly rate (or the team default). The model is sliced once, laid flat automatically, on the quote
printer, falling back to the other active printers when it doesn't fit. At approval it is sliced again in the
chosen material and colour, uploaded to the farm and queued, quantity as ordered.

Files live under data_dir/quotes/<token>/ and are deleted when a quote expires unordered (valid_days)."""
import hashlib
import logging
import re
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..config import settings
from ..jobs import enqueue, event
from ..kv import setting, set_setting
from ..models import Quote, PrinterProfile, SpoolCache

log = logging.getLogger("hub.quotes")
EXTS = (".stl", ".3mf", ".obj")
DENSITY = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21, "PC": 1.20}
DEFAULTS = {
    "enabled": False, "markup": 2.5, "setup_fee": 3.0, "minimum": 10.0, "max_mb": 50, "valid_days": 7,
    "max_per_hour": 10, "printer_profile_id": None, "max_quantity": 50, "currency": "USD",
    "materials": [
        {"key": "pla", "label": "PLA", "material": "PLA", "price_per_kg": 20.0, "enabled": True,
         "note": "Everyday plastic: crisp detail, indoor use", "colours": [{"name": "Black", "hex": "#000000"}, {"name": "White", "hex": "#FFFFFF"}]},
        {"key": "petg", "label": "PETG", "material": "PETG", "price_per_kg": 22.0, "enabled": True,
         "note": "Tougher and heat resistant: functional parts", "colours": [{"name": "Black", "hex": "#000000"}]},
    ],
}


def now():
    return datetime.now(timezone.utc)


def config(db: Session) -> dict:
    c = {**DEFAULTS, **(setting(db, "quote_settings") or {})}
    return c


def save_config(db: Session, body: dict) -> dict:
    c = config(db)
    for k in ("enabled", "markup", "setup_fee", "minimum", "max_mb", "valid_days", "max_per_hour", "printer_profile_id",
              "max_quantity", "currency", "woo_product_id"):
        if k in body:
            c[k] = body[k]
    if "materials" in body:
        mats = []
        for m in body["materials"] or []:
            key = re.sub(r"[^a-z0-9-]", "", (m.get("key") or m.get("label") or "").lower())[:30]
            if not key or not m.get("material"):
                raise ValueError("each material needs a name and a material type")
            mats.append({"key": key, "label": (m.get("label") or key)[:60], "material": m["material"].upper()[:20],
                         "price_per_kg": float(m.get("price_per_kg") or 0), "enabled": bool(m.get("enabled", True)),
                         "note": (m.get("note") or "")[:200],
                         "colours": [{"name": (x.get("name") or "")[:60], "hex": (x.get("hex") or "")[:9],
                                      "spoolman_filament_id": x.get("spoolman_filament_id")}
                                     for x in (m.get("colours") or []) if x.get("name")]})
        if len({m["key"] for m in mats}) != len(mats):
            raise ValueError("two materials share a name")
        c["materials"] = mats
    for k in ("markup", "setup_fee", "minimum"):
        c[k] = float(c[k])
        if c[k] < 0 or (k == "markup" and c[k] < 1):
            raise ValueError("markup is at least 1; fees can't be negative")
    set_setting(db, "quote_settings", c)
    return c


def qdir(token: str) -> Path:
    return Path(settings().data_dir) / "quotes" / token


def _profile(db: Session, c: dict) -> PrinterProfile | None:
    from . import slicing
    if c.get("printer_profile_id"):
        p = db.get(PrinterProfile, c["printer_profile_id"])
        if p:
            return p
    return slicing.default_profile(db)


def _fallbacks(db: Session, first: PrinterProfile) -> list[PrinterProfile]:
    others = [p for p in db.execute(select(PrinterProfile)).scalars() if p.id != first.id and getattr(p, "active", True)]
    return [first] + others


# ---------- create, slice, price ----------

def create(db: Session, filename: str, data: bytes, ip: str | None) -> Quote:
    c = config(db)
    if not c.get("enabled"):
        raise PermissionError("instant quotes are switched off")
    name = Path(filename or "model").name[:200]
    if not name.lower().endswith(EXTS):
        raise ValueError("upload an STL, 3MF or OBJ file")
    if len(data) > float(c["max_mb"]) * 1024 * 1024:
        raise ValueError(f"the file is larger than {c['max_mb']:g} MB")
    if len(data) < 84:
        raise ValueError("the file is empty or not a 3D model")
    if name.lower().endswith(".3mf") and data[:2] != b"PK":
        raise ValueError("that isn't a 3MF file")
    if ip:
        hour_ago = now() - timedelta(hours=1)
        n = db.execute(select(func.count(Quote.id)).where(Quote.ip == ip, Quote.created_at >= hour_ago)).scalar() or 0
        if n >= int(c["max_per_hour"]):
            raise PermissionError("too many quotes from here in the last hour; try again later")
    token = secrets.token_urlsafe(18)
    d = qdir(token)
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    (d / safe).write_bytes(data)
    q = Quote(token=token, filename=name, stored=f"{token}/{safe}", bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
              ip=(ip or "")[:64] or None, status="slicing", expires_at=now() + timedelta(days=int(c["valid_days"])))
    db.add(q)
    db.flush()
    enqueue(db, "quote.slice", ref=f"quote:{q.id}", payload={"quote_id": q.id}, priority=9, max_attempts=1)
    return q


def _fake_model(material: str, colour_hex: str | None):
    slot = SimpleNamespace(slot=1, material=material, color_hex=colour_hex, spoolman_filament_id=None, waste_pct=0, label="Body")
    prod = SimpleNamespace(filaments=[slot], sku=None)
    return SimpleNamespace(product=prod, code="QUOTE", name="Quote", print_settings={}, files=[], id=None)


def _slice(db: Session, prof: PrinterProfile, path: Path, material: str, colour_hex: str | None, copies: int = 1,
           keep_files: Path | None = None) -> dict:
    """Slice one customer file. Returns stats; raises ValueError with a customer-readable reason."""
    from . import slicing
    from ..clients.base import ApiError
    spec = slicing.build_spec(db, _fake_model(material, colour_hex), prof,
                              {"orientation": {"mode": "auto"}, "ignore_designer": True, "copies": copies}, sources=[])
    spec.pop("_warnings", None)
    spec["output_name"] = "quote"
    spec.pop("metadata", None)
    c = slicing.client()
    res = c.slice([path], spec)
    try:
        if not res.get("ok"):
            err = str(res.get("error") or res.get("return_code"))
            if "nothing to slice" in err or "inside the print volume" in err or "over the boundary" in err:
                raise ValueError("too big for this printer")
            raise ValueError("the model couldn't be sliced: check it is a closed, printable mesh")
        plates = res.get("plates") or []
        grams = sum((f.get("grams") or 0) for p in plates for f in (p.get("filaments") or []))
        out = {"time_s": int(sum(p.get("time_s") or 0 for p in plates)), "grams": round(grams, 2), "previews": [],
               "files": []}
        if keep_files:
            keep_files.mkdir(parents=True, exist_ok=True)
            for name in res.get("previews") or []:
                if re.fullmatch(r"preview_plate_\d+\.png", name):
                    try:
                        c.download(res["id"], name, keep_files / name)
                        out["previews"].append(name)
                    except ApiError:
                        pass
            files = res.get("files") or []
            if res.get("sliced_3mf") and str(res["sliced_3mf"]).endswith(".gcode.3mf"):
                wanted = [res["sliced_3mf"]]
            elif res.get("sliced_file"):
                wanted = [res["sliced_file"]]
            else:
                wanted = sorted(f for f in files if re.match(r"plate_\d+\.(b?gcode)$", f))
            for w in wanted:
                c.download(res["id"], w, keep_files / Path(w).name)
                out["files"].append(Path(w).name)
        return out
    finally:
        try:
            c.forget(res["id"])
        except Exception:  # noqa: BLE001
            pass


def run_slice(db: Session, quote_id: int) -> dict:
    from . import slicing
    q = db.get(Quote, quote_id)
    if not q or q.status != "slicing":
        return {"skipped": True}
    c = config(db)
    mats = [m for m in c["materials"] if m.get("enabled")]
    if not mats:
        q.status, q.error = "failed", "no materials are offered for quotes"
        return {"failed": q.error}
    first = mats[0]
    path = Path(settings().data_dir) / "quotes" / q.stored
    try:
        mesh = slicing.client().inspect([path], "mesh")
        objs = mesh.get("objects") or []
        if objs:
            q.size_mm = [round(v, 1) for v in ((objs[0].get("bbox_object") or {}).get("size") or [])][:3] or None
    except Exception:  # noqa: BLE001
        pass
    prof0 = _profile(db, c)
    if not prof0:
        q.status, q.error = "failed", "no printer is set up for quotes"
        return {"failed": q.error}
    last = None
    for prof in _fallbacks(db, prof0):
        try:
            out = _slice(db, prof, path, first["material"], None, keep_files=qdir(q.token) / "preview")
        except ValueError as e:
            last = str(e)
            if "too big" in last:
                continue
            break
        except Exception as e:  # noqa: BLE001
            last = "the quote service is busy; try again in a few minutes"
            log.warning("quote %s slice failed: %r", q.id, e)
            q.breakdown = {"internal_error": repr(e)[:500]}
            break
        q.printer_profile_id, q.grams, q.time_s = prof.id, out["grams"], out["time_s"]
        q.sliced_material, q.previews, q.status, q.error = first["material"], out["previews"], "ready", None
        price(db, q, first["key"], None, 1)
        return {"ready": True, "printer": prof.name, "grams": q.grams}
    q.status = "failed"
    q.error = ("too big for any of our printers" if last and "too big" in last else last) or "the model couldn't be sliced"
    return {"failed": q.error}


def _density(material: str) -> float:
    m = (material or "PLA").upper()
    return next((v for k, v in DENSITY.items() if m.startswith(k)), 1.24)


def _rate(db: Session, prof_id: int | None) -> float:
    from . import slicing
    p = db.get(PrinterProfile, prof_id) if prof_id else None
    if p and p.hourly_rate is not None:
        return float(p.hourly_rate)
    return float(slicing.cfg(db, "printer_hourly_rate") or 0)


def options(db: Session, q: Quote) -> list[dict]:
    """Every offered material with its price for one piece and whether each colour is in stock."""
    c = config(db)
    out = []
    if q.grams is None:
        return out
    stock = {}
    for s in db.execute(select(SpoolCache).where(SpoolCache.archived.is_(False))).scalars():
        if s.filament_id:
            stock[s.filament_id] = stock.get(s.filament_id, 0) + (s.remaining_g or 0)
    for m in c["materials"]:
        if not m.get("enabled"):
            continue
        b = breakdown(db, q, m, 1)
        need = b["grams"] * 1.1
        cols = [{"name": x["name"], "hex": x.get("hex"),
                 "in_stock": (stock.get(x["spoolman_filament_id"], 0) >= need) if x.get("spoolman_filament_id") else True}
                for x in m.get("colours") or []]
        out.append({"key": m["key"], "label": m["label"], "note": m.get("note"), "unit_price": b["unit"],
                    "colours": cols, "grams": b["grams"]})
    return out


def breakdown(db: Session, q: Quote, m: dict, qty: int) -> dict:
    c = config(db)
    grams = round((q.grams or 0) * _density(m["material"]) / _density(q.sliced_material or "PLA"), 1)
    hours = (q.time_s or 0) / 3600
    material = grams * float(m["price_per_kg"]) / 1000
    machine = hours * _rate(db, q.printer_profile_id)
    unit = round((material + machine) * float(c["markup"]), 2)
    total = round(max(float(c["minimum"]), unit * qty + float(c["setup_fee"])), 2)
    return {"grams": grams, "hours": round(hours, 2), "material_cost": round(material, 2), "machine_cost": round(machine, 2),
            "markup": float(c["markup"]), "setup_fee": float(c["setup_fee"]), "minimum": float(c["minimum"]),
            "unit": unit, "quantity": qty, "total": total, "minimum_applied": total == float(c["minimum"])}


def price(db: Session, q: Quote, material_key: str, colour: str | None, qty: int) -> Quote:
    c = config(db)
    m = next((x for x in c["materials"] if x["key"] == material_key and x.get("enabled")), None)
    if not m:
        raise ValueError("that material isn't offered")
    if colour and colour not in [x["name"] for x in m.get("colours") or []]:
        raise ValueError("that colour isn't offered in this material")
    qty = int(qty or 1)
    if not 1 <= qty <= int(c["max_quantity"]):
        raise ValueError(f"order between 1 and {c['max_quantity']} pieces")
    b = breakdown(db, q, m, qty)
    q.material_key, q.colour, q.quantity, q.unit_price, q.total = m["key"], colour, qty, b["unit"], b["total"]
    q.breakdown = {**b, "material": m["label"], "colour": colour}
    return q


def public(db: Session, q: Quote) -> dict:
    """What the store shows the customer (no costs, no internal ids)."""
    c = config(db)
    expired = q.expires_at and q.expires_at.replace(tzinfo=q.expires_at.tzinfo or timezone.utc) < now() and q.status in ("slicing", "ready", "failed")
    return {"token": q.token, "status": "expired" if expired else q.status, "filename": q.filename, "error": q.error,
            "size_mm": q.size_mm, "print_hours": round((q.time_s or 0) / 3600, 1) if q.time_s else None,
            "options": options(db, q) if q.status == "ready" else [], "currency": c.get("currency", "USD"),
            "setup_fee": float(c["setup_fee"]), "minimum": float(c["minimum"]), "max_quantity": int(c["max_quantity"]),
            "choice": {"material": q.material_key, "colour": q.colour, "quantity": q.quantity, "total": q.total,
                       "unit_price": q.unit_price},
            "previews": [f"preview/{p}" for p in q.previews or []],
            "expires_at": q.expires_at.isoformat() if q.expires_at else None}


# ---------- orders and review ----------

def attach_order(db: Session, order, line, token: str):
    q = db.execute(select(Quote).where(Quote.token == token)).scalar()
    if not q or q.order_id:
        return
    q.order_id, q.order_line_id, q.status = order.id, line.id, "ordered"
    q.expires_at = None
    event(db, f"order:{order.id}", f"Order #{order.number} includes custom print quote {q.id} ({q.filename}); review it on the Quotes page")
    from .alerts import notify
    notify(db, "quote_ordered", f"Custom print ordered: {q.filename}",
           f"Order #{order.number}: {q.quantity} x {(q.breakdown or {}).get('material', '')} {q.colour or ''}. "
           "Review it before it prints.", link=settings().public_url.rstrip("/") + "/quotes",
           ref=f"alert:quote:{q.id}", subject={"quote": q.id, "order": order.id})


def approve(db: Session, q: Quote, actor: str) -> Quote:
    if q.status != "ordered":
        raise ValueError(f"this quote is {q.status}, not waiting for review")
    q.status, q.reviewed_by, q.reviewed_at = "approved", actor, now()
    from .alert_inbox import resolve
    resolve(db, f"alert:quote:{q.id}", "approved")
    enqueue(db, "quote.produce", ref=f"quote:{q.id}", payload={"quote_id": q.id}, priority=9, max_attempts=2)
    return q


def refuse(db: Session, q: Quote, actor: str, reason: str) -> Quote:
    from ..models import Order
    if q.status != "ordered":
        raise ValueError(f"this quote is {q.status}, not waiting for review")
    if not (reason or "").strip():
        raise ValueError("give the customer a reason")
    q.status, q.review_note, q.reviewed_by, q.reviewed_at = "refused", reason.strip()[:1000], actor, now()
    from .alert_inbox import resolve
    resolve(db, f"alert:quote:{q.id}", "refused")
    o = db.get(Order, q.order_id) if q.order_id else None
    if o:
        enqueue(db, "quote.refuse_note", ref=f"quote:{q.id}", payload={"quote_id": q.id, "woo_order_id": o.woo_order_id})
        event(db, f"order:{o.id}", f"Custom print refused by {actor}: {reason[:200]}. Refund the order in WooCommerce.", level="warn")
    return q


def refuse_note(db: Session, quote_id: int, woo_order_id: int) -> dict:
    from ..clients import woo
    q = db.get(Quote, quote_id)
    woo().add_order_note(woo_order_id, f"We can't print your custom model {q.filename}: {q.review_note} "
                                       "We'll refund this part of your order.", customer_note=True)
    woo().update_order(woo_order_id, {"status": "on-hold"})
    return {"noted": True}


def produce(db: Session, quote_id: int) -> dict:
    """Approved: slice in the chosen material and colour, upload to the farm, queue the pieces."""
    from ..models import OrderLine
    from .farm import _client, upsert
    q = db.get(Quote, quote_id)
    if not q or q.status != "approved":
        return {"skipped": True}
    c = config(db)
    m = next((x for x in c["materials"] if x["key"] == q.material_key), None) or {"material": q.sliced_material or "PLA"}
    col = next((x for x in (m.get("colours") or []) if x["name"] == q.colour), None)
    prof = db.get(PrinterProfile, q.printer_profile_id) if q.printer_profile_id else _profile(db, c)
    out_dir = qdir(q.token) / "print"
    shutil.rmtree(out_dir, ignore_errors=True)
    out = _slice(db, prof, Path(settings().data_dir) / "quotes" / q.stored, m["material"], (col or {}).get("hex"),
                 keep_files=out_dir)
    if not out["files"]:
        raise ValueError("the slicer produced no printable file")
    w = _client()
    ids = []
    for f in out["files"]:
        up = w.upload_file(str(out_dir / f), f"Q{q.id}-{re.sub(r'[^A-Za-z0-9._-]+', '_', Path(q.filename).stem)[:40]}-{f}")
        q.farm_file_ref = up.get("uuid") or (up.get("file") or {}).get("uuid")
        name = f"Quote {q.id} {q.filename} {m.get('label', m['material'])} {q.colour or ''}".strip()
        r = w.submit_job(file_ref=q.farm_file_ref, quantity=q.quantity, job_name=name)
        ids += [i for i in (r or {}).get("job_ids") or []]
    q.farm_job_ids = ids
    if q.order_line_id:
        ln = db.get(OrderLine, q.order_line_id)
        if ln:
            ln.farm_job_ids, ln.farm_error = ids, None
            for i in ids:
                upsert(db, i, {"name": f"Quote {q.id} {q.filename}", "status": "queued", "order_refs": [ln.order.number]})
            o = ln.order
            if all(x.farm_job_ids for x in o.lines) and not o.routed_at:
                o.routed_at = now()
    event(db, "farm", f"Custom print quote {q.id} sent to the farm ({len(ids)} job(s))")
    return {"jobs": ids}


def expire(db: Session) -> dict:
    """Delete files of quotes that were never ordered and have run out."""
    n = 0
    for q in db.execute(select(Quote).where(Quote.status.in_(("slicing", "ready", "failed")), Quote.expires_at < now())).scalars():
        shutil.rmtree(qdir(q.token), ignore_errors=True)
        q.status = "expired"
        n += 1
    return {"expired": n}


def admin_dict(db: Session, q: Quote) -> dict:
    from ..models import Order
    o = db.get(Order, q.order_id) if q.order_id else None
    prof = db.get(PrinterProfile, q.printer_profile_id) if q.printer_profile_id else None
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {"id": q.id, "token": q.token, "status": q.status, "filename": q.filename, "bytes": q.bytes,
            "size_mm": q.size_mm, "grams": q.grams, "time_s": q.time_s, "printer": prof.name if prof else None,
            "material": (q.breakdown or {}).get("material"), "colour": q.colour, "quantity": q.quantity,
            "unit_price": q.unit_price, "total": q.total, "breakdown": q.breakdown or {}, "error": q.error,
            "previews": [f"/api/quotes/{q.id}/preview/{p}" for p in q.previews or []],
            "order": {"id": o.id, "number": o.number, "status": o.status, "customer": o.customer} if o else None,
            "farm_job_ids": q.farm_job_ids or [], "review_note": q.review_note, "reviewed_by": q.reviewed_by,
            "reviewed_at": iso(q.reviewed_at), "created_at": iso(q.created_at), "expires_at": iso(q.expires_at)}
