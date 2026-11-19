"""WooCommerce and the farm library: product push, live toggle, stock sync, order intake and part consumption."""
import hashlib
import hmac
import html
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients import woo, farm, inventree
from ..clients.base import ApiError
from ..config import settings
from ..jobs import PermanentError, event, enqueue
from ..models import Product, Variant, Image, ModelFile, Category, Setting, Order, OrderLine, PartCache
from . import availability, images as imgsvc
from ..kv import setting_row, setting, set_setting  # noqa: F401

log = logging.getLogger("hub.shop")


# ---------- helpers ----------
def image_token(img: Image) -> str:
    return hmac.new(settings().session_secret.encode(), f"img:{img.id}:{img.sha256}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def public_image_url(img: Image) -> str:
    return f"{settings().public_url}/public/img/{img.id}/{image_token(img)}.jpg"


def to_html(text: str | None) -> str:
    if not text:
        return ""
    if "<p" in text or "<br" in text or "<ul" in text:
        return text
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out = []
    for p in paras:
        lines = p.splitlines()
        if all(l.strip().startswith(("- ", "* ")) for l in lines):
            out.append("<ul>" + "".join(f"<li>{html.escape(l.strip()[2:])}</li>" for l in lines) + "</ul>")
        else:
            out.append("<p>" + "<br>".join(html.escape(l) for l in lines) + "</p>")
    return "\n".join(out)


def licensing_credit(model) -> str:
    from . import licensing
    return ("\n" + licensing.credit_html(model)) if licensing.credit_line(model) else ""


def woo_category_id(db: Session, cat: Category | None) -> int | None:
    if not cat:
        return None
    if cat.woo_category_id:
        return cat.woo_category_id
    existing = {c["name"].lower(): c["id"] for c in woo().list_categories()}
    cid = existing.get(cat.name.lower())
    if not cid:
        cid = woo().create_category(cat.name)["id"]
    cat.woo_category_id = cid
    return cid


def color_attribute_id(db: Session) -> int:
    aid = setting(db, "woo_color_attribute_id")
    if aid:
        return aid
    for a in woo().list_attributes():
        if a["slug"] in ("pa_color", "color") or a["name"].lower() == "color":
            aid = a["id"]
            break
    if not aid:
        aid = woo().create_attribute("Color", "color")["id"]
    set_setting(db, "woo_color_attribute_id", aid)
    return aid


def woo_images(product: Product) -> list[dict]:
    imgs = [i for i in sorted(product.model.images, key=lambda i: (i.role != "hero", i.sort))
            if i.for_web and i.role in ("hero", "gallery")][:12]
    out = []
    for i in imgs:
        out.append({"id": i.woo_media_id} if i.woo_media_id else {"src": public_image_url(i), "name": i.filename,
                                                                  "alt": product.title})
    return out, imgs


def validate_for_push(product: Product, going_live: bool, switching_live: bool = False):
    from . import licensing
    m = product.model
    problems = list(licensing.sale_problems(m))
    if going_live:
        if not product.price:
            problems.append("price is not set")
        if not [i for i in m.images if i.for_web]:
            problems.append("no web images")
        if product.color_mode == "options" and not [v for v in product.variants if v.active]:
            problems.append("color options enabled but no active colors")
        if switching_live:      # only when turning it on, so updates to products already live keep working
            problems += slice_problems(product)
    return problems


def slice_problems(product: Product) -> list[str]:
    """With printers set up and the team rule on, a product goes live only with an approved print file."""
    from sqlalchemy.orm import object_session
    from . import slicing
    db = object_session(product)
    if db is None or not slicing.cfg(db, "require_approved_slice"):
        return []
    d = slicing.default_profile(db)
    if not d:
        return []
    if any(f.is_print_file for f in product.model.files):
        return []
    return [f"no approved slice for {d.name} (the default printer); slice and approve it on the Print tab"]


def push_product(db: Session, product_id: int, actor: str | None = None) -> dict:
    p = db.get(Product, product_id)
    if not p:
        raise PermanentError("product not found")
    from .stores import sells_on
    if not sells_on(db, p, "woo"):
        return {"skipped": "not sold on WooCommerce"}
    problems = validate_for_push(p, p.live)
    if problems:
        p.woo_error = "; ".join(problems)
        raise PermanentError(p.woo_error)
    m = p.model
    snap = availability.load_snapshot(db)
    avail = availability.product_availability(db, p, snap)
    stock = availability.stock_for_woo(p, avail)
    variable = p.color_mode == "options" and any(v.active for v in p.variants)
    imgs_payload, imgs = woo_images(p)
    cat_id = woo_category_id(db, m.category)
    meta = [{"key": "_artisan_hub_product_id", "value": str(p.id)},
            {"key": "_artisan_hub_url", "value": f"{settings().public_url}/models/{m.id}"},
            {"key": "_artisan_hub_model_code", "value": m.code}]
    data = {
        "name": p.title, "type": "variable" if variable else "simple", "sku": p.sku,
        "description": to_html(p.description or m.description) + licensing_credit(m),
        "short_description": to_html(p.short_description or m.summary),
        "categories": [{"id": cat_id}] if cat_id else [],
        "images": imgs_payload,
        "status": "publish" if p.live else "draft",
        "catalog_visibility": "visible" if p.live else "hidden",
        "meta_data": meta,
        "tags": [{"name": t} for t in (m.tags or [])[:10]],
    }
    if p.weight_g:
        data["weight"] = f"{p.weight_g / 1000:.3f}"
    if p.dims_cm:
        parts = [x.strip() for x in re.split(r"[x×*]", p.dims_cm) if x.strip()]
        if len(parts) == 3:
            data["dimensions"] = {"length": parts[0], "width": parts[1], "height": parts[2]}
    if variable:
        aid = color_attribute_id(db)
        opts = [v.color_name for v in p.variants if v.active]
        data["attributes"] = [{"id": aid, "variation": True, "visible": True, "options": opts}]
        data["default_attributes"] = [{"id": aid, "option": opts[0]}]
        data["manage_stock"] = False
    else:
        data["regular_price"] = f"{p.price:.2f}" if p.price else ""
        data["sale_price"] = f"{p.sale_price:.2f}" if p.sale_price else ""
        data["manage_stock"] = True
        data["stock_quantity"] = stock.get("p", 0)
        data["backorders"] = "no"

    if p.woo_product_id:
        try:
            res = woo().update_product(p.woo_product_id, data)
        except ApiError as e:
            if e.status == 404:
                p.woo_product_id = None
                res = None
            else:
                raise
    else:
        res = None
    if res is None:
        found = woo().find_product_by_sku(p.sku)
        res = woo().update_product(found["id"], data) if found else woo().create_product(data)
    p.woo_product_id = res["id"]
    # remember media ids so we never re-download the same image
    for img, wimg in zip(imgs, res.get("images") or []):
        img.woo_media_id = wimg.get("id")

    if variable:
        existing = {v["sku"]: v for v in woo().list_variations(p.woo_product_id)}
        aid = color_attribute_id(db)
        create, update, keep = [], [], set()
        for v in p.variants:
            if not v.active:
                continue
            vd = {"sku": v.sku, "regular_price": f"{(v.price or p.price or 0):.2f}",
                  "attributes": [{"id": aid, "option": v.color_name}], "manage_stock": True,
                  "stock_quantity": stock.get(str(v.id), 0), "backorders": "no", "status": "publish"}
            if p.sale_price and not v.price:
                vd["sale_price"] = f"{p.sale_price:.2f}"
            if v.image_id:
                vi = db.get(Image, v.image_id)
                if vi and vi.woo_media_id:
                    vd["image"] = {"id": vi.woo_media_id}
            ex = existing.get(v.sku) or (next((e for e in existing.values() if e["id"] == v.woo_variation_id), None))
            if ex:
                vd["id"] = ex["id"]
                update.append(vd)
                keep.add(ex["id"])
            else:
                create.append(vd)
        stale = [e["id"] for e in existing.values() if e["id"] not in keep]
        r = woo().batch_variations(p.woo_product_id, {"create": create, "update": update, "delete": stale})
        by_sku = {x.get("sku"): x.get("id") for x in (r.get("create") or []) + (r.get("update") or []) if x.get("id")}
        for v in p.variants:
            if v.sku in by_sku:
                v.woo_variation_id = by_sku[v.sku]
        errs = [x.get("error") for x in (r.get("create") or []) + (r.get("update") or []) if x.get("error")]
        if errs:
            raise RuntimeError(f"variation errors: {errs[:3]}")
    p.woo_status = res.get("status")
    p.woo_stock_pushed = stock
    p.woo_synced_at = datetime.now(timezone.utc)
    p.woo_error = None
    event(db, f"model:{m.id}", f"Pushed to WooCommerce as {'live' if p.live else 'draft'} (product {p.woo_product_id})",
          actor=actor)
    enqueue(db, "farm.sync_product", ref=f"product:{p.id}", payload={"product_id": p.id}, delay=5)
    return {"woo_product_id": p.woo_product_id, "status": p.woo_status, "stock": stock}


def set_live(db: Session, product_id: int, live: bool, actor: str | None = None, this_store_only: bool = False) -> dict:
    """this_store_only: the product was taken off Woo; its live state on the other stores stays."""
    p = db.get(Product, product_id)
    problems = validate_for_push(p, live)
    if problems and live:
        raise PermanentError("; ".join(problems))
    if not this_store_only:
        p.live = live
    if not p.woo_product_id:
        return {"skipped": "not on the store"} if this_store_only else push_product(db, product_id, actor)
    res = woo().update_product(p.woo_product_id, {"status": "publish" if live else "draft",
                                                  "catalog_visibility": "visible" if live else "hidden"})
    p.woo_status = res.get("status")
    p.woo_synced_at = datetime.now(timezone.utc)
    event(db, f"model:{p.model_id}", f"{'Went live' if live else 'Hidden'} on WooCommerce", actor=actor)
    sync_stock(db, product_id)
    return {"status": p.woo_status}


def sync_stock(db: Session, product_id: int | None = None) -> dict:
    """Push buildable quantities to Woo for every product that has a Woo listing."""
    snap = availability.load_snapshot(db)
    q = select(Product).where(Product.woo_product_id.is_not(None))
    if product_id:
        q = q.where(Product.id == product_id)
    changed = 0
    from .stores import sells_on
    for p in db.execute(q).scalars():
        if not sells_on(db, p, "woo"):
            continue
        avail = availability.product_availability(db, p, snap)
        stock = availability.stock_for_woo(p, avail)
        if stock == (p.woo_stock_pushed or {}):
            continue
        if "p" in stock:
            woo().update_product(p.woo_product_id, {"manage_stock": True, "stock_quantity": stock["p"]})
        else:
            upd = []
            for v in p.variants:
                if v.woo_variation_id and str(v.id) in stock:
                    upd.append({"id": v.woo_variation_id, "manage_stock": True, "stock_quantity": stock[str(v.id)]})
            if upd:
                woo().batch_variations(p.woo_product_id, {"update": upd})
        old = p.woo_stock_pushed or {}
        p.woo_stock_pushed = stock
        changed += 1
        if p.live and any((old.get(k, 1) > 0) and v == 0 for k, v in stock.items()):
            event(db, f"model:{p.model_id}", "Out of supplies: Woo stock set to 0", level="warn")
            enqueue(db, "alert.out_of_supplies", ref=f"product:{p.id}", payload={"product_id": p.id})
    return {"changed": changed}


# ---------- orders ----------
def upsert_order(db: Session, o: dict) -> Order:
    row = db.execute(select(Order).where(Order.woo_order_id == o["id"])).scalar()
    if not row:
        row = Order(woo_order_id=o["id"], number=str(o.get("number") or o["id"]), status=o.get("status", "pending"))
        db.add(row)
    prev_status = row.status
    row.status = o.get("status", row.status)
    b = o.get("billing") or {}
    row.customer = " ".join(x for x in [b.get("first_name"), b.get("last_name")] if x) or b.get("email")
    try:
        row.total = float(o.get("total") or 0)
    except ValueError:
        row.total = None
    row.currency = o.get("currency")
    row.is_test = any(m.get("key") == "_artisan_test_order" for m in o.get("meta_data") or []) or \
        "test" in (o.get("payment_method") or "").lower()
    if o.get("date_created_gmt"):
        row.placed_at = datetime.fromisoformat(o["date_created_gmt"]).replace(tzinfo=timezone.utc)
    row.raw = {k: o.get(k) for k in ("status", "number", "payment_method", "payment_method_title", "date_created",
                                     "shipping_lines", "customer_note")}
    db.flush()
    by_line = {ln.woo_line_id: ln for ln in row.lines}
    seen = set()
    quote_lines = []
    for li in o.get("line_items") or []:
        ln = by_line.get(li["id"]) or OrderLine(order_id=row.id, woo_line_id=li["id"])
        ln.sku, ln.name, ln.qty = li.get("sku"), li.get("name"), int(li.get("quantity") or 1)
        if not ln.sku:   # a line the hub created for a model that isn't on the store carries its SKU as meta
            ln.sku = next((str(m.get("value")) for m in li.get("meta_data") or [] if m.get("key") == "_artisan_sku"), None)
        v = db.execute(select(Variant).where(Variant.sku == ln.sku)).scalar() if ln.sku else None
        if v:
            ln.variant_id, ln.product_id = v.id, v.product_id
        else:
            pr = db.execute(select(Product).where(Product.sku == ln.sku)).scalar() if ln.sku else None
            if not pr and li.get("product_id"):
                pr = db.execute(select(Product).where(Product.woo_product_id == li["product_id"])).scalar()
            if pr:
                ln.product_id = pr.id
                if li.get("variation_id"):
                    vv = db.execute(select(Variant).where(Variant.woo_variation_id == li["variation_id"])).scalar()
                    ln.variant_id = vv.id if vv else None
        if ln not in row.lines:
            row.lines.append(ln)
        seen.add(li["id"])
        qtok = next((m.get("value") for m in li.get("meta_data") or [] if m.get("key") == "_artisan_quote"), None)
        if qtok:
            quote_lines.append((ln, str(qtok)))
    for ln in list(row.lines):
        if ln.woo_line_id not in seen:
            row.lines.remove(ln)
    db.flush()
    if prev_status != row.status:
        event(db, f"order:{row.id}", f"Order #{row.number} is {row.status}")
    if quote_lines and row.status in ("processing", "completed", "on-hold"):
        from .quotes import attach_order
        for ln, tok in quote_lines:
            attach_order(db, row, ln, tok)
    from .farm import on_order
    on_order(db, row, prev_status)
    if row.status == "completed" and not row.consumed:
        enqueue(db, "order.consume", ref=f"order:{row.id}", payload={"order_id": row.id})
    # every product's stock right away (not behind background work): products sharing a part or filament with
    # what was just ordered must drop before the next customer sees them
    enqueue(db, "store.sync_stock", ref="stock:all", delay=1, priority=1)
    return row


def consume_order(db: Session, order_id: int) -> dict:
    """Deduct per-unit parts from InvenTree stock (FIFO across stock items) when an order completes."""
    o = db.get(Order, order_id)
    if not o or o.consumed:
        return {"skipped": True}
    if setting(db, "consume_parts_on_complete", True) is False:
        o.consumed = True
        return {"skipped": "disabled"}
    need: dict[int, float] = {}
    for ln in o.lines:
        if not ln.product_id:
            continue
        p = db.get(Product, ln.product_id)
        v = db.get(Variant, ln.variant_id) if ln.variant_id else None
        for kind, ref, qty, _ in availability.unit_requirements(p, v):
            if kind == "part":
                need[ref] = need.get(ref, 0) + qty * ln.qty
    removed = []
    for pid, qty in need.items():
        items = sorted(inventree().stock(part=pid, in_stock="true"), key=lambda s: s.get("updated") or "")
        left = qty
        batch = []
        for it in items:
            take = min(left, float(it.get("quantity") or 0))
            if take > 0:
                batch.append({"pk": it["pk"], "quantity": take})
                left -= take
            if left <= 0:
                break
        if batch:
            inventree().remove_stock(batch, notes=f"OpenPrintHQ: Woo order #{o.number}")
        removed.append({"part": pid, "qty": qty, "short": max(0, left)})
    o.consumed = True
    o.consumed_at = datetime.now(timezone.utc)
    event(db, f"order:{o.id}", f"Deducted parts for order #{o.number}: {removed}")
    enqueue(db, "inventory.refresh_parts", ref="refresh:parts", delay=1)
    return {"removed": removed}


def poll_orders(db: Session) -> dict:
    last = setting(db, "woo_orders_last_poll")
    n = 0
    page = 1
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    while True:
        batch = woo().list_orders(after=last, page=page)
        for o in batch:
            upsert_order(db, o)
            n += 1
        if len(batch) < 100:
            break
        page += 1
    set_setting(db, "woo_orders_last_poll", started)
    return {"orders": n}


# ---------- print files in the farm ----------
def print_files(product: Product) -> list[ModelFile]:
    files = [f for f in product.model.files if f.is_print_file]
    if not files:
        files = [f for f in product.model.files if f.kind in ("3mf_sliced", "gcode")][:1]
    return files


def sync_farm_product(db: Session, product_id: int) -> dict:
    """Put a store product's print-ready files in the farm's library, so its orders can print. The hub keeps which
    file prints for which SKU and sends file jobs itself."""
    p = db.get(Product, product_id)
    if not p or not p.woo_product_id:
        return {"skipped": "not on woo"}
    files = print_files(p)
    if not files:
        p.farm_error = "no print-ready file (sliced .3mf or .gcode) marked for production"
        return {"skipped": p.farm_error}
    c = farm()
    for f in files:
        ensure_farm_file(f, c, f"{p.sku}__{f.filename}")
    p.farm_error = None
    return {"files": [farm_ref(f) for f in files]}


FARM_FILE_KINDS = ("3mf_project", "3mf_sliced", "gcode")


# ---------- farm library refs ----------
# model_file.farm_file_ref is the file's id in the farm's library (farm_kind "openprinthq"). A ref of any
# other kind means nothing to the farm, so the file is uploaded again on first use instead of sent as a bad ref.
FARM = "openprinthq"


def farm_ref(f: ModelFile, kind: str | None = None) -> str | None:
    """The file's id in the farm's library, or None when it isn't there."""
    if not f.farm_file_ref:
        return None
    return f.farm_file_ref if f.farm_kind == FARM else None


def set_farm_ref(f: ModelFile, ref: str | None, kind: str | None = None) -> None:
    f.farm_file_ref = ref
    f.farm_kind = FARM if ref else None
    f.farm_file_error = None


def ensure_farm_file(f: ModelFile, c=None, name: str | None = None, kind: str | None = None) -> str:
    """Upload the file to the farm's library unless it is already there; return its ref there."""
    from pathlib import Path
    ref = farm_ref(f)
    if ref:
        return ref
    if not f.library_rel:
        raise ValueError(f"{f.filename} has no copy in the hub's library to upload")
    c = c or farm()
    res = c.upload_file(str(Path(settings().library_root) / f.library_rel), name or f.filename)
    ref = res.get("uuid") or (res.get("file") or {}).get("uuid")
    if not ref:
        raise RuntimeError(f"unexpected farm upload response: {res}")
    set_farm_ref(f, str(ref))
    return f.farm_file_ref


ROOT_FOLDER_KEY = "farm_root_folder_id:openprinthq"   # team setting (name kept from earlier versions)
MODEL_FOLDER_KEY = "farm_folder_id:openprinthq"        # model.extra (name kept from earlier versions)


def model_folder_id(m, kind: str | None = None):
    return (m.extra or {}).get(MODEL_FOLDER_KEY)


def _farm_folder(c, name: str, parent):
    r = c.library_folder(name, parent)
    return (r.get("folder") or r)["id"]


def mirror_model_to_farm(db: Session, model_id: int) -> dict:
    """Store the model's 3MF / G-code files in the farm's library under <Team name>/<Category>/<Code - Name>."""
    from pathlib import Path
    from ..models import Model
    m = db.get(Model, model_id)
    if not m:
        return {"skipped": "missing"}
    todo = [f for f in m.files if f.kind in FARM_FILE_KINDS and f.library_rel
            and (not farm_ref(f) or f.farm_file_error)]
    if not todo:
        return {"uploaded": 0}
    c = farm()
    extra = dict(m.extra or {})
    folder = extra.get(MODEL_FOLDER_KEY)
    if not folder:
        from .teams import current_team
        root = setting(db, ROOT_FOLDER_KEY) or _farm_folder(c, current_team(db).name, None)
        set_setting(db, ROOT_FOLDER_KEY, root)
        cat = _farm_folder(c, m.category.name if m.category else "Uncategorized", root)
        folder = _farm_folder(c, f"{m.code} - {m.name}", cat)
        extra[MODEL_FOLDER_KEY] = folder
        m.extra = extra
        db.commit()
    n = 0
    for f in todo:
        path = Path(settings().library_root) / f.library_rel
        if not path.exists():
            continue
        try:
            if not farm_ref(f):
                ensure_farm_file(f, c)
                f.farm_file_error = "move pending"
                db.commit()
            c.move_library_file(f.farm_file_ref, folder)
            f.farm_file_error = None
            n += 1
        except ApiError as e:
            f.farm_file_error = str(e)[:500]
            if e.status == 429:
                db.commit()
                raise
        db.commit()
    return {"uploaded": n, "folder": folder}
