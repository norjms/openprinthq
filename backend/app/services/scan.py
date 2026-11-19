"""What a scanned code is (mobile control, 2026-10-23).

Codes are case-insensitive: S<spool id> (farm library spool), L<6 Crockford base32> (a location: storage, dry box,
printer, AMS unit, AMS slot, external holder), P<product id>, O<order id>. A full label link
(https://<hub>/l/<code>) is read the same way. Anything else that looks like a UPC/EAN goes to the barcode
lookup (kind "barcode").
"""
import re

from sqlalchemy.orm import Session

from . import spool_library as lib

LOC_KINDS = {"printer": "printer", "ams": "ams", "ams_slot": "ams_slot", "external": "external"}
CROCKFORD = re.compile(r"^L[0-9A-HJKMNP-TV-Z]{6}$")


def normalize(code: str) -> str:
    c = (code or "").strip()
    m = re.search(r"/l/([^/?#\s]+)", c)
    if m:
        c = m.group(1)
    return c.strip()


def kind_of(code: str) -> str:
    from .barcodes import looks_like_barcode
    c = normalize(code)
    u = c.upper()
    if looks_like_barcode(c):
        return "barcode"
    if re.fullmatch(r"S\d{1,9}", u):
        return "spool"
    if len(u) == 7 and u[0] == "L":
        return "location"
    if re.fullmatch(r"P\d{1,9}", u):
        return "product"
    if re.fullmatch(r"O\d{1,9}", u):
        return "order"
    return "unknown"


def _mini_spool(a: dict | None) -> dict | None:
    if not a or a.get("spool_id") is None:
        return None
    return {"id": a["spool_id"], "code": f"S{a['spool_id']}", "material": a.get("material"), "color_hex": a.get("color"),
            "vendor": a.get("brand"), "name": a.get("name"), "remaining_g": a.get("remaining_g"),
            "extruder_index": a.get("extruder_index"), "slicer_preset": a.get("slicer_preset_name")}


def resolve(db: Session, code: str) -> dict:
    c = normalize(code)
    kind = kind_of(c)
    u = c.upper()
    out = {"kind": kind, "code": u if kind != "barcode" else c, "title": None, "subtitle": None, "actions": []}
    if kind == "barcode":
        from .barcodes import lookup
        r = lookup(db, c)
        m = r.get("match")
        out.update({"title": (" ".join(x for x in [m.get("vendor"), m.get("material"), m.get("name")] if x) if m else
                              (r.get("product") or {}).get("title")) or f"Barcode {c}",
                    "subtitle": {"learned": "Known filament", "upc": "Product found online", "none": "Unknown barcode"}[r["source"]],
                    "lookup": r, "actions": ["new_spool", "learn"] if not m else ["new_spool"]})
        return out
    if kind == "spool":
        s = lib.get_spool(db, int(u[1:]))
        out.update({"title": " ".join(x for x in [s.get("material"), s.get("name")] if x) or f"Spool {u}",
                    "subtitle": " - ".join(x for x in [s.get("vendor"), f"{round(s.get('remaining_g') or 0)} g left",
                                                       (s.get("assigned") or {}).get("slot_label") and
                                                       f"{s['assigned']['printer']} {s['assigned']['slot_label']}"
                                                       or s.get("location")] if x),
                    "spool": s,
                    "actions": ["details", "use", "move", "assign"] + (["unassign"] if s.get("assigned") else [])
                    + ["label", "archive"]})
        return out
    if kind == "location":
        loc = lib.location_by_code(u)
        ids = list(loc.get("spools") or [])
        if ids:
            try:
                byid = {s["id"]: lib.spool_summary(s) for s in lib.list_spools(db)}
                loc["spools"] = [byid.get(i) or {"id": i, "code": f"S{i}"} for i in ids]
            except lib.LibraryError:
                loc["spools"] = [{"id": i, "code": f"S{i}"} for i in ids]
        lk = loc.get("kind") or "storage"
        out["kind"] = LOC_KINDS.get(lk, "location")
        out.update({"title": loc.get("name"), "location": loc,
                    "subtitle": {"printer": "Printer", "ams": "AMS unit", "ams_slot": "AMS slot",
                                 "external": "External holder", "drybox": "Dry box", "storage": "Storage"}.get(lk, lk.title())})
        if lk in ("ams_slot", "external") and loc.get("printer_id") is not None:
            slot = {"printer": loc.get("printer_name"), "printer_id": loc.get("printer_id"), "ams_id": loc.get("ams_id"),
                    "tray_id": loc.get("tray_id"), "label": None, "extruders": [], "fixed": None, "loaded": None}
            try:
                ld = lib._call(lib._ophq().loaded, int(loc["printer_id"]))
                hit = next((x for x in ld.get("slots") or [] if x.get("ams_id") == loc.get("ams_id")
                            and x.get("tray_id") == (loc.get("tray_id") or 0)), None)
                if hit:
                    slot.update({"label": hit.get("label"), "extruders": hit.get("extruders") or [], "fixed": hit.get("fixed"),
                                 "loaded": _mini_spool(hit.get("assignment")), "live": hit.get("live")})
            except lib.LibraryError:
                pass
            if not slot["label"] or slot["loaded"] is None:
                # The printer is offline or its paths have no row for this slot: name it the engine's way and find
                # what is installed from the slot assignments.
                try:
                    c = lib._ophq()
                    nc = next((int(x.get("nozzle_count") or 1) for x in c._printers_raw()
                               if x.get("id") == loc.get("printer_id")), 1)
                    slot["label"] = slot["label"] or lib.slot_label(loc.get("ams_id"), loc.get("tray_id"), nc)
                    if slot["loaded"] is None:
                        a = next((x for x in c.lib_assignments() if x.get("printer_id") == loc.get("printer_id")
                                  and x.get("ams_id") == loc.get("ams_id")
                                  and x.get("tray_id") == (loc.get("tray_id") or 0)), None)
                        if a:
                            s = lib.get_spool(db, int(a["spoolman_spool_id"]))
                            slot["loaded"] = {**lib.spool_summary(s), "extruder_index": a.get("extruder_index")}
                except Exception:  # noqa: BLE001
                    slot["label"] = slot["label"] or lib.slot_label(loc.get("ams_id"), loc.get("tray_id"))
            out["slot"] = slot
            out["subtitle"] = f"{loc.get('printer_name') or 'Printer'} - {slot.get('label') or out['subtitle']}"
            out["actions"] = ["assign"] + (["unassign"] if slot.get("loaded") else []) + ["details", "label"]
        elif lk == "printer":
            out["printer"] = {"id": loc.get("printer_id"), "name": loc.get("printer_name") or loc.get("name")}
            out["actions"] = ["details", "label"]
        else:
            out["actions"] = ["details", "move_here", "label"]
        return out
    if kind == "product":
        from ..models import Product
        pr = db.get(Product, int(u[1:]))
        if not pr:
            out.update({"kind": "unknown", "title": f"No product {u}"})
            return out
        out.update({"title": pr.title, "subtitle": f"Product {pr.sku}" + (" - live" if pr.live else ""),
                    "product": {"id": pr.id, "sku": pr.sku, "title": pr.title, "live": pr.live, "price": pr.price,
                                "model_id": pr.model_id},
                    "actions": ["details", "live", "push"]})
        return out
    if kind == "order":
        from ..models import Order
        o = db.get(Order, int(u[1:]))
        if not o:
            out.update({"kind": "unknown", "title": f"No order {u}"})
            return out
        out.update({"title": f"Order {o.number}", "subtitle": " - ".join(x for x in [o.customer, o.status] if x),
                    "order": {"id": o.id, "number": o.number, "status": o.status, "customer": o.customer,
                              "lines": [{"sku": ln.sku, "name": ln.name, "qty": ln.qty} for ln in o.lines]},
                    "actions": ["details", "status"]})
        return out
    out["title"] = "Unknown code"
    return out


def public_summary(code: str) -> dict:
    """For the public /l/ page when nobody is signed in: just what kind of thing it is and a short name."""
    c = normalize(code)
    k = kind_of(c)
    names = {"spool": "Filament spool", "location": "Storage or printer location", "product": "Product",
             "order": "Order", "barcode": "Product barcode", "unknown": "OpenPrintHQ code"}
    return {"kind": k, "code": c.upper() if k != "barcode" else c, "title": f"{names[k]} {c.upper()}"
            if k in ("spool", "product", "order", "location") else names[k]}
