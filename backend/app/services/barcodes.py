"""Filament barcodes and identification (mobile control, 2026-10-23).

The phone identifies a filament with a ladder: barcode -> this hub's barcode library (learned when someone confirms
what a barcode is) -> an online UPC lookup (UPCitemdb's free trial API, cached here) -> on-device AI and OCR ->
this hub's vision model (Ollama). Every step answers candidates; the person always confirms, and a confirmed barcode
is learned.

The rules parser reads brand, material, colour, weight and diameter from a product title or OCR text; candidates
are then matched against the team's filament library and the SpoolmanDB catalog.
"""
import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger("hub.barcodes")

UPC_RE = re.compile(r"^\d{8}$|^\d{12,14}$")
MATERIALS = ["PLA-CF", "PETG-CF", "PA6-CF", "PA12-CF", "PAHT-CF", "PET-CF", "ABS-GF", "PA-CF", "ASA-CF", "PC-ABS",
             "PLA+", "PLA", "PETG", "PCTG", "PET", "ABS", "ASA", "TPU", "TPE", "PA12", "PA6", "PAHT", "PA", "PC",
             "PVA", "HIPS", "PP", "PPS", "PEEK", "BVOH", "PVB"]
FINISH_WORDS = ["silk", "matte", "metal", "metallic", "galaxy", "marble", "wood", "sparkle", "glow", "translucent",
                "dual", "tri", "rainbow", "pearl", "high speed", "hs", "basic", "pro", "tough", "carbon fiber"]
UPC_CACHE_DAYS = 30


def valid_upc(code: str) -> bool:
    """UPC-A / EAN-8 / EAN-13 / GTIN-14 with a correct check digit."""
    code = (code or "").strip()
    if not UPC_RE.match(code):
        return False
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def looks_like_barcode(code: str) -> bool:
    return bool(UPC_RE.match((code or "").strip()))


# ---------------------------------------------------------------- the rules parser

def _brands() -> dict:
    from .filaments import BRANDS
    out = {k: v for k, v in BRANDS.items() if v}
    out.update({"bambu lab": "Bambu Lab", "bambu": "Bambu Lab", "elegoo": "ELEGOO", "jayo": "JAYO", "kingroon": "Kingroon",
                "sunlu": "Sunlu", "eryone": "Eryone", "overture": "Overture", "polymaker": "Polymaker",
                "prusament": "Prusament", "esun": "eSun", "creality": "Creality", "anycubic": "ANYCUBIC",
                "hatchbox": "Hatchbox", "inland": "Inland", "geeetech": "GEEETECH", "amolen": "AMOLEN",
                "tecbears": "TECBEARS", "duramic": "Duramic", "iiid max": "IIID MAX", "flashforge": "Flashforge",
                "3d fuel": "3D-Fuel", "fiberlogy": "Fiberlogy", "extrudr": "Extrudr", "azurefilm": "AzureFilm",
                "snapmaker": "Snapmaker", "kexcelled": "Kexcelled", "r3d": "R3D", "voxelpla": "VoxelPLA"})
    return out


def parse_text(text: str | None, brand_hint: str | None = None) -> dict:
    """{vendor, name, material, color_name, color_hex, weight_g, diameter_mm} from a product title or OCR text."""
    from .filaments import NAMED, PLAIN
    t = " ".join((text or "").replace("\n", " ").split())
    low = t.lower()
    out: dict = {"vendor": None, "name": None, "material": None, "color_name": None, "color_hex": None,
                 "weight_g": None, "diameter_mm": None}
    brands = _brands()
    if brand_hint:
        out["vendor"] = brands.get(brand_hint.strip().lower(), brand_hint.strip())
    else:
        for k in sorted(brands, key=len, reverse=True):
            if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", low):
                out["vendor"] = brands[k]
                break
    up = t.upper().replace("PLA PLUS", "PLA+").replace("PLA PRO", "PLA+")
    for m in MATERIALS:
        if re.search(r"(?<![A-Z0-9])" + re.escape(m) + r"(?![A-Z0-9])" if not m.endswith("+") else
                     r"(?<![A-Z0-9])" + re.escape(m), up):
            out["material"] = m
            break
    if not out["material"] and re.search(r"carbon fib(er|re)", low) and "PETG" in up:
        out["material"] = "PETG-CF"
    w = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|kilo|g|gram|grams)\b", low)
    if w:
        v = float(w.group(1).replace(",", "."))
        g = v * 1000 if w.group(2).startswith("k") else v
        if 100 <= g <= 10000:
            out["weight_g"] = g
    d = re.search(r"(1\.75|2\.85|3\.00?|3)\s*mm", low)
    if d:
        out["diameter_mm"] = float(d.group(1))
    elif "1.75" in low:
        out["diameter_mm"] = 1.75
    colours = {**{k.lower(): v for k, v in NAMED.items()}, **{k.lower(): v for k, v in PLAIN.items()}}
    for k in sorted(colours, key=len, reverse=True):
        if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", low):
            out["color_name"] = k.title()
            out["color_hex"] = colours[k].upper()
            break
    finish = next((f for f in FINISH_WORDS if re.search(r"(?<![a-z])" + re.escape(f) + r"(?![a-z])", low)), None)
    parts = [p for p in [finish.title() if finish and finish not in ("hs", "basic") else None, out["color_name"]] if p]
    out["name"] = " ".join(parts) or None
    return out


# ---------------------------------------------------------------- candidates

def candidates(db: Session, guess: dict, limit: int = 10) -> list[dict]:
    """Library filaments, then SpoolmanDB catalog entries, ranked by how well they fit the guess."""
    from . import spool_library as lib
    from .filaments import delta_e
    pool = []
    try:
        pool += lib.library_filaments(db)
    except Exception as e:  # noqa: BLE001
        log.warning("library filaments: %s", e)
    words = " ".join(x for x in [guess.get("vendor"), guess.get("material")] if x)
    try:
        pool += lib.catalog_hits(words or None, None, limit=400) if words else []
    except Exception:  # noqa: BLE001
        pass

    def score(f):
        s = 0.0
        if guess.get("vendor") and (f.get("vendor") or "").lower() == guess["vendor"].lower():
            s += 3
        fm, gm = (f.get("material") or "").upper(), (guess.get("material") or "").upper()
        if gm and fm == gm:
            s += 3
        elif gm and fm.startswith(gm.rstrip("+")):
            s += 1.5
        if guess.get("color_hex") and f.get("color_hex"):
            try:
                s += max(0.0, 3 - delta_e(guess["color_hex"], f["color_hex"]) / 10)
            except Exception:  # noqa: BLE001
                pass
        if guess.get("color_name") and guess["color_name"].lower() in (f.get("name") or "").lower():
            s += 2
        if guess.get("weight_g") and f.get("weight_g") and abs(float(f["weight_g"]) - float(guess["weight_g"])) < 1:
            s += 0.5
        if f.get("source") != "catalog":
            s += 0.5
        return s
    ranked = sorted(((score(f), i, f) for i, f in enumerate(pool)), key=lambda t: (-t[0], t[1]))
    return [{**f, "score": round(sc, 2)} for sc, _, f in ranked if sc > 0][:limit]


# ---------------------------------------------------------------- the barcode library

def _team_barcode(db: Session, code: str):
    from ..models import FilamentBarcode
    return db.execute(select(FilamentBarcode).where(FilamentBarcode.barcode == code)).scalar()


def learn(db: Session, barcode: str, filament_id: int, actor: str | None = None, filament: dict | None = None,
          source: str = "learned"):
    """Remember that a barcode is this library filament (replaces what was learned before)."""
    from ..models import FilamentBarcode
    code = (barcode or "").strip()
    if not code or len(code) > 32:
        raise ValueError("barcode is 1 to 32 characters")
    if filament is None:
        from . import spool_library as lib
        filament = next((f for f in lib.library_filaments(db) if f.get("id") == int(filament_id)), None)
        if filament is None:
            raise LookupError(f"no filament {filament_id} in the library")
    row = _team_barcode(db, code) or FilamentBarcode(barcode=code)
    row.filament_id = int(filament_id)
    row.vendor, row.name, row.material = filament.get("vendor"), filament.get("name"), filament.get("material")
    row.color_hex, row.weight_g = filament.get("color_hex"), filament.get("weight_g")
    row.diameter_mm = filament.get("diameter_mm")
    row.source, row.created_by = source, actor
    db.add(row)
    db.flush()
    return row


def upc_lookup(db: Session, code: str) -> dict | None:
    """The product behind a UPC/EAN from UPCitemdb, cached for UPC_CACHE_DAYS (misses too). Never raises: an
    outage or the daily limit just answers None."""
    from ..config import settings
    from ..models import UpcLookup
    from ..tenant import unscoped
    with unscoped():
        row = db.execute(select(UpcLookup).where(UpcLookup.code == code)).scalar()
    fresh = row and row.fetched_at and (datetime.now(timezone.utc) - (row.fetched_at if row.fetched_at.tzinfo else
                                        row.fetched_at.replace(tzinfo=timezone.utc))) < timedelta(days=UPC_CACHE_DAYS)
    if fresh:
        return {"title": row.title, "brand": row.brand, **(row.data or {})} if row.found else None
    url = settings().upc_lookup_url
    if not url:
        return None
    try:
        r = httpx.get(url, params={"upc": code}, timeout=6.0, headers={"Accept": "application/json",
                                                                       "User-Agent": "OpenPrintHQ/1.0"})
        if r.status_code == 429 or r.status_code >= 500:
            return None
        j = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.info("UPC lookup %s: %s", code, e)
        return None
    items = j.get("items") or []
    it = items[0] if items else None
    data = {}
    if it:
        data = {"description": (it.get("description") or "")[:1000], "model": it.get("model"),
                "weight": it.get("weight"), "images": (it.get("images") or [])[:3], "ean": it.get("ean"),
                "upc": it.get("upc"), "category": it.get("category")}
    with unscoped():
        row = row or UpcLookup(code=code)
        row.found = bool(it)
        row.title = (it.get("title") or "")[:500] if it else None
        row.brand = (it.get("brand") or "")[:200] if it else None
        row.data = data
        row.fetched_at = datetime.now(timezone.utc)
        db.add(row)
        db.flush()
    return {"title": row.title, "brand": row.brand, **data} if it else None


def lookup(db: Session, barcode: str) -> dict:
    """{barcode, match, source: learned | upc | none, candidates, product}."""
    code = (barcode or "").strip()
    out = {"barcode": code, "match": None, "source": "none", "candidates": [], "product": None}
    row = _team_barcode(db, code) if code else None
    if row:
        from . import spool_library as lib
        f = None
        try:
            f = next((x for x in lib.library_filaments(db) if x.get("id") == row.filament_id), None)
        except Exception as e:  # noqa: BLE001
            log.warning("library filaments: %s", e)
        out["match"] = f or {"id": row.filament_id, "vendor": row.vendor, "name": row.name, "material": row.material,
                             "color_hex": row.color_hex, "weight_g": row.weight_g, "source": "learned"}
        out["source"] = "learned"
        out["candidates"] = [out["match"]]
        return out
    if not looks_like_barcode(code):
        return out
    prod = upc_lookup(db, code)
    if prod:
        g = parse_text(" ".join(x for x in [prod.get("title"), prod.get("description")] if x)[:600],
                       brand_hint=prod.get("brand") or None)
        out["product"] = {"title": prod.get("title"), "brand": prod.get("brand") or g.get("vendor"),
                          "weight_g": g.get("weight_g"), "material": g.get("material"), "color_name": g.get("color_name"),
                          "images": prod.get("images") or []}
        out["source"] = "upc"
        out["candidates"] = candidates(db, g)
    return out


# ---------------------------------------------------------------- identify (vision model, then rules)

PROMPT = """You read the label of a 3D printer filament spool or its box.
Answer with ONE JSON object and nothing else, using exactly these keys:
{"vendor": string or null, "name": string or null, "material": string or null, "color_name": string or null,
 "color_hex": string like "#RRGGBB" or null, "weight_g": number or null, "diameter_mm": number or null,
 "barcode": string or null}
material is one of PLA, PLA+, PETG, ABS, ASA, TPU, PA, PC, PLA-CF, PETG-CF or what the label says.
weight_g is the net filament weight in grams (1 kg = 1000). color_hex only when the label prints the code;
never estimate it from the colour name. Use null for anything you cannot read.
Do not guess a vendor that is not printed on the label."""


def _vision_cfg(db: Session) -> tuple[str, str, float]:
    from ..config import settings
    from ..kv import setting
    s = settings()
    url = (setting(db, "vision_url") or s.vision_url or "").rstrip("/")
    model = setting(db, "vision_model") or s.vision_model
    return url, model, float(s.vision_timeout)


def _clean_guess(d: dict) -> dict:
    keys = ("vendor", "name", "material", "color_name", "color_hex", "weight_g", "diameter_mm", "barcode")
    out = {k: d.get(k) for k in keys}
    for k in ("weight_g", "diameter_mm"):
        try:
            out[k] = float(out[k]) if out[k] not in (None, "") else None
        except (TypeError, ValueError):
            out[k] = None
    if out["weight_g"] is not None and 0 < out["weight_g"] <= 10:
        out["weight_g"] = out["weight_g"] * 1000          # "1 kg" read as 1: no spool weighs 10 g
    from .spool_library import hexc
    out["color_hex"] = hexc(out.get("color_hex")) if out.get("color_hex") else None
    for k in ("vendor", "name", "material", "color_name", "barcode"):
        v = out.get(k)
        out[k] = str(v).strip()[:120] if v not in (None, "", "null") else None
    if out["material"]:
        out["material"] = out["material"].upper()
    return out


def ask_vision(db: Session, photo: bytes | None, text: str | None) -> tuple[dict | None, str | None]:
    """(guess, engine) from the team's AI connections, tried in the saved order (services/vision.py), or
    (None, why) when none is set up, all are down or none could read the label."""
    from . import vision
    prompt = PROMPT + (f"\nText read from the label by the phone:\n{text[:2000]}" if text else "")

    def usable(doc):
        g = _clean_guess(doc)
        return bool(g.get("material") or g.get("vendor") or g.get("name"))

    if not vision.providers(db):
        return None, "no AI connection set up (Settings, Connected services)"
    doc, engine, notes = vision.read_label(db, prompt, photo, usable)
    if doc is None:
        return None, "; ".join(notes)[:300] or "no AI connection could read it"
    return _clean_guess(doc), engine


def _ask_vision_legacy(db: Session, photo: bytes | None, text: str | None) -> tuple[dict | None, str | None]:
    """The single-Ollama call used before AI connections (kept for reference; vision.py covers it)."""
    url, model, timeout = _vision_cfg(db)
    if not url or not model:
        return None, "no vision model set up"
    msg = {"role": "user", "content": PROMPT + (f"\nText read from the label by the phone:\n{text[:2000]}" if text else "")}
    if photo:
        msg["images"] = [base64.b64encode(photo).decode()]
    try:
        r = httpx.post(url + "/api/chat", json={"model": model, "messages": [msg], "stream": False, "format": "json",
                                                "think": False, "options": {"temperature": 0}}, timeout=timeout)
        r.raise_for_status()
        content = ((r.json().get("message") or {}).get("content") or "").strip()
        m = re.search(r"\{.*\}", content, re.S)
        doc = json.loads(m.group(0) if m else content)
        if not isinstance(doc, dict):
            raise ValueError("not an object")
    except (httpx.HTTPError, ValueError, TypeError) as e:
        log.info("vision model: %s", e)
        return None, f"vision model unavailable: {type(e).__name__}"
    return _clean_guess(doc), f"ollama:{model}"


def identify(db: Session, photo: bytes | None = None, text: str | None = None, hints: dict | None = None) -> dict:
    """{guess, candidates, engine}. Never fails because the model is down: the rules parser answers instead."""
    hints = {k: v for k, v in (hints or {}).items() if v not in (None, "")}
    guess, engine, note = None, None, None
    if photo or text:
        guess, engine = ask_vision(db, photo, text)
        if guess is None:
            note, engine = engine, None
    rules = parse_text(text, brand_hint=hints.get("vendor")) if text else {}
    if guess is None:
        guess = {**{k: None for k in ("vendor", "name", "material", "color_name", "color_hex", "weight_g",
                                       "diameter_mm", "barcode")}, **rules}
        engine = "rules"
    else:
        for k, v in rules.items():           # the parser fills what the model left out
            if guess.get(k) in (None, "") and v:
                guess[k] = v
    for k, v in hints.items():
        if k in guess and v:
            guess[k] = v
    out = {"guess": guess, "candidates": candidates(db, guess), "engine": engine}
    if note:
        out["note"] = note
    return out
