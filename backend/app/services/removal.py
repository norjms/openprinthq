"""Archive, restore and permanently delete models, cleaning up every connected system."""
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..clients.base import ApiError
from ..config import settings
from ..models import Model, Image, OrderLine, LicenseEvidence, Setting
from . import images as imgsvc
from ..kv import setting_row, set_setting  # noqa: F401

log = logging.getLogger("hub.removal")
BLOCK_KEY = "import_blocklist"


# ---------- import block list (so scanners don't bring deleted models back) ----------
def blocklist(db: Session) -> dict:
    row = setting_row(db, BLOCK_KEY)
    v = row.value if row and isinstance(row.value, dict) else {}
    return {"codes": list(v.get("codes") or []), "makerworld": list(v.get("makerworld") or []),
            "sources": list(v.get("sources") or [])}


def is_blocked(db: Session, code: str | None = None, makerworld_id: int | None = None, source_key: str | None = None) -> bool:
    b = blocklist(db)
    return bool((code and code in b["codes"]) or (makerworld_id and makerworld_id in b["makerworld"])
                or (source_key and source_key in b["sources"]))


def _block(db: Session, m: Model):
    b = blocklist(db)
    if m.code and m.code not in b["codes"]:
        b["codes"].append(m.code)
    if m.makerworld_id and m.makerworld_id not in b["makerworld"]:
        b["makerworld"].append(m.makerworld_id)
    if m.source_url and m.source_url not in b["sources"]:
        b["sources"].append(m.source_url)
    set_setting(db, BLOCK_KEY, b)


def unblock(db: Session, code: str | None = None, makerworld_id: int | None = None):
    b = blocklist(db)
    if code in b["codes"]:
        b["codes"].remove(code)
    if makerworld_id in b["makerworld"]:
        b["makerworld"].remove(makerworld_id)
    set_setting(db, BLOCK_KEY, b)


# ---------- archive / restore ----------
def archive(db: Session, m: Model, actor: str | None = None):
    if m.product and m.product.live:
        raise ValueError("hide it on the store first (turn Live off)")
    extra = dict(m.extra or {})
    extra["status_before_archive"] = m.status
    m.extra = extra
    m.status = "archived"


def restore(db: Session, m: Model):
    m.status = (m.extra or {}).get("status_before_archive") or "draft"
    if m.status == "archived":
        m.status = "draft"


def delete_blockers(db: Session, m: Model) -> list[str]:
    out = []
    if m.product and m.product.live:
        out.append("it is live on the store (turn Live off first)")
    if m.product:
        n = db.execute(select(func.count(OrderLine.id)).where(OrderLine.product_id == m.product.id)).scalar()
        if n:
            out.append(f"it has {n} order line(s); archive it instead so order history keeps its product")
    return out


# ---------- permanent delete ----------
def purge(db: Session, model_id: int) -> dict:
    from ..clients import woo, dam, farm, manyfold
    m = db.get(Model, model_id)
    if not m:
        return {"skipped": "already gone"}
    blockers = delete_blockers(db, m)
    if blockers:
        raise ValueError("; ".join(blockers))
    report = {"code": m.code, "name": m.name, "warnings": []}
    p = m.product

    # WooCommerce draft (variations go with it)
    if p and p.woo_product_id:
        try:
            woo().delete(f"/products/{p.woo_product_id}", params={"force": "true"})
            report["woo"] = p.woo_product_id
        except ApiError as e:
            if e.status != 404:
                report["warnings"].append(f"Woo product {p.woo_product_id}: {e.message[:120]}")

    # Shopify and Etsy drafts
    if p:
        from . import stores
        for ch in stores.CHANNELS:
            try:
                gone = stores.remove_listing(db, p, ch)
                if gone:
                    report[ch] = gone
            except ApiError as e:
                report["warnings"].append(f"{stores.LABEL[ch]} listing: {e.message[:120]}")

    # the farm: the model's files in the farm's library
    try:
        c = farm()
        from .shop import farm_ref, model_folder_id
        for f in m.files:
            if farm_ref(f):
                try:
                    c.delete_library_file(farm_ref(f))
                except ApiError as e:
                    if e.status != 404:
                        report["warnings"].append(f"Farm file {f.filename}: {e.message[:100]}")
        fid = model_folder_id(m)
        if fid:
            try:
                c.delete_library_folder(fid)
            except ApiError:
                pass
    except Exception as e:  # noqa: BLE001
        report["warnings"].append(f"Farm: {str(e)[:120]}")

    # DAM: the model's photos and its collection
    try:
        rs = dam()
        n = 0
        for img in m.images:
            if img.dam_ref:
                others = db.execute(select(func.count(Image.id)).where(Image.dam_ref == img.dam_ref, Image.model_id != m.id)).scalar()
                if not others:
                    try:
                        rs.delete_resource(img.dam_ref)
                        n += 1
                    except ApiError as e:
                        report["warnings"].append(f"DAM resource {img.dam_ref}: {e.message[:80]}")
        if m.dam_collection_ref:
            try:
                rs.call("delete_collection", m.dam_collection_ref)
            except ApiError as e:
                report["warnings"].append(f"DAM collection: {e.message[:80]}")
        report["dam_resources"] = n
    except Exception as e:  # noqa: BLE001
        report["warnings"].append(f"DAM: {str(e)[:120]}")

    # License records are kept (they protect past sales): moved aside with an index
    ev = db.execute(select(LicenseEvidence).where(LicenseEvidence.model_id == m.id)).scalars().all()
    data = Path(settings().data_dir)
    if ev:
        keep = data / "evidence-deleted" / f"{m.code}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        keep.mkdir(parents=True, exist_ok=True)
        src = data / "evidence" / str(m.id)
        if src.exists():
            for f in src.iterdir():
                shutil.move(str(f), keep / f.name)
            shutil.rmtree(src, ignore_errors=True)
        (keep / "records.json").write_text(json.dumps(
            {"model": {"code": m.code, "name": m.name, "source_url": m.source_url, "designer": m.designer.name if m.designer else None},
             "records": [{"kind": r.kind, "license": r.license_code, "raw": r.license_raw, "note": r.note,
                          "wayback": r.wayback_url, "sha256": r.sha256, "captured_at": r.captured_at.isoformat(),
                          "files": [Path(x).name for x in (r.pdf_rel, r.json_rel, r.html_rel) if x]} for r in ev]},
            indent=1))
        report["license_records_kept"] = str(keep.relative_to(data))

    # Hub image files (content addressed: only when no other model uses the same photo)
    removed = 0
    for img in m.images:
        others = db.execute(select(func.count(Image.id)).where(Image.sha256 == img.sha256, Image.model_id != m.id)).scalar()
        if others:
            continue
        paths = [data / img.store_rel] if img.store_rel else []
        paths += [imgsvc.variant_path(img.sha256, s) for s in imgsvc.SIZES]
        paths += [data / "img" / "web" / img.sha256[:2] / f"{img.sha256}.jpg"]
        for pth in paths:
            try:
                pth.unlink()
                removed += 1
            except FileNotFoundError:
                pass
    report["image_files"] = removed

    # Library folder (Manyfold drops it on the next scan). Source folders on the share are left alone.
    lib = Path(settings().library_root)
    if m.library_rel:
        target = (lib / m.library_rel.strip("/")).resolve()
        if lib.resolve() in target.parents and target.exists():
            shutil.rmtree(target, ignore_errors=True)
            report["library_folder"] = m.library_rel

    _block(db, m)
    db.delete(m)
    db.flush()
    try:
        from ..jobs import enqueue
        enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=30)
        if p:
            enqueue(db, "store.sync_stock", ref="stock:all", delay=5)
    except Exception:  # noqa: BLE001
        pass
    _ = manyfold  # imported for symmetry; rescan runs via the job
    return report
