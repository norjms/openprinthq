"""Shared plumbing for model-site importers (Printables, Thingiverse, Creality Cloud, MakerWorld).

Every source turns a model page into the same normalized dict:

    {"platform", "id", "url", "title", "description_html", "tags": [], "category",
     "designer": {"name", "handle", "url"}, "license_raw", "license_text",
     "photos": [url, ...], "profiles": [{"id", "title", "printer", "time_s", "grams", "layer_height", "walls",
     "infill", "nozzle", "filaments": [{"type", "color", "grams"}], "default"}],
     "files": [{"id", "name", "size", "kind"}], "supplies": [{"title", "qty", "qty_known", "link", "sku", "supplier"}],
     "stats": {"downloads", "likes", "prints"}, "updated", "api_url", "raw": {...}}

and the generic importer below turns that into a hub model with photos, files, materials, designer,
license and a license record.
"""
import logging
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.base import ApiError
from ..models import Model, Designer, Setting, BomFilament, BomPart
from ..kv import setting_row, set_setting  # noqa: F401

log = logging.getLogger("hub.sources")

PLATFORM_LABEL = {"makerworld": "MakerWorld", "printables": "Printables", "thingiverse": "Thingiverse",
                  "creality": "Creality Cloud", "thangs": "Thangs", "myminifactory": "MyMiniFactory",
                  "cults3d": "Cults3D", "web": "Web link"}
CODE_PREFIX = {"makerworld": "MW", "printables": "PR", "thingiverse": "TV", "creality": "CR", "thangs": "TH",
               "myminifactory": "MMF", "cults3d": "CU", "web": "WEB"}
MODEL_EXT = (".3mf", ".stl", ".step", ".stp", ".obj", ".gcode", ".bgcode", ".zip", ".f3d", ".scad")


class Http:
    """One browser-like session per site, paced, with a short cache and a cool-down when the site pushes back."""

    def __init__(self, name: str, gap: float = 1.0):
        self.name = name
        self.gap = gap
        self.lock = threading.Lock()
        self.last = 0.0
        self.blocked_until = 0.0
        self.cache: dict[str, tuple[float, object]] = {}
        self.s = cffi.Session(impersonate="chrome", timeout=90)

    def _pace(self):
        with self.lock:
            wait = self.last + self.gap - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()

    def request(self, method: str, url: str, cache_s: int = 0, raw: bool = False, **kw):
        key = f"{method} {url} {kw.get('json')} {kw.get('params')}"
        if cache_s and key in self.cache and time.time() - self.cache[key][0] < cache_s:
            return self.cache[key][1]
        if time.time() < self.blocked_until:
            raise ApiError(self.name, 429, f"{self.name} asked us to slow down; try again in "
                                           f"{int(self.blocked_until - time.time()) + 1}s")
        for attempt in range(3):
            self._pace()
            r = self.s.request(method, url, **kw)
            if r.status_code == 429 or (r.status_code in (403, 503) and "Just a moment" in r.text[:3000]):
                if attempt < 2:
                    time.sleep(4 * (attempt + 1))
                    continue
                self.blocked_until = time.time() + 90
                log.warning("%s pushed back on %s: %s %s", self.name, url[:120], r.status_code, r.text[:200].replace("\n", " "))
                raise ApiError(self.name, 429, f"{self.name} is rate limiting or showing a bot check; "
                                               "the hub pauses requests to it for 90 seconds and retries")
            break
        if r.status_code >= 400:
            raise ApiError(self.name, r.status_code, r.text[:300])
        out = r if raw else r.json()
        if cache_s:
            self.cache[key] = (time.time(), out)
            if len(self.cache) > 400:
                for k in sorted(self.cache, key=lambda k: self.cache[k][0])[:100]:
                    self.cache.pop(k, None)
        return out

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def bytes(self, url: str, headers: dict | None = None) -> bytes:
        self._pace()
        r = self.s.get(url, headers=headers or {}, allow_redirects=True)
        if r.status_code >= 400:
            raise ApiError(self.name, r.status_code, f"download failed: {url[:120]}")
        return r.content


def account(db: Session, key: str) -> dict:
    row = setting_row(db, key)
    return row.value if row and isinstance(row.value, dict) else {}


def save_account(db: Session, key: str, value: dict):
    set_setting(db, key, value)
    db.commit()


def file_kind(name: str) -> str:
    n = name.lower()
    for ext, k in ((".3mf", "3mf"), (".stl", "stl"), (".step", "step"), (".stp", "step"), (".obj", "obj"),
                   (".gcode", "gcode"), (".bgcode", "gcode"), (".zip", "zip"), (".f3d", "f3d"), (".scad", "scad")):
        if n.endswith(ext):
            return k
    return "other"


# ---------- hardware from free text ----------
HARDWARE = re.compile(r"\b(screws?|bolts?|nuts?|washers?|magnets?|bearings?|heat[- ]?set|inserts?|springs?|"
                      r"rods?|dowels?|pins?|leds?|led strip|batter(?:y|ies)|switch(?:es)?|wires?|cables?|"
                      r"rubber feet|bumpers?|hinges?|zip ties?|velcro|glue|epoxy|neodymium|m[2-8]\b|"
                      r"usb|motor|servo|esp32|arduino|lights?|tea ?lights?|bulbs?|e27|e14|gu10)", re.I)
SECTION = re.compile(r"(hardware|bill of materials|bom|parts? (?:list|needed|required)|you(?:'ll| will) need|"
                     r"what you need|materials needed|additional parts|non[- ]printed|shopping list|supplies)", re.I)


def supplies_from_text(html_or_text: str | None) -> list[dict]:
    """Pull hardware lines (with counts) out of a description: '4x M3x8 screws', 'Magnets 6x3mm x 8'."""
    from ..services.makerworld import parse_qty_line
    if not html_or_text:
        return []
    soup = BeautifulSoup(html_or_text, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all(["li", "p", "div", "h1", "h2", "h3", "h4", "tr"]):
        li.append("\n")
    lines = [ln.strip(" \t-*•●–") for ln in soup.get_text().split("\n")]
    out, in_section, gap = [], False, 0
    for ln in lines:
        if not ln:
            gap += 1
            if gap > 1:
                in_section = False
            continue
        gap = 0
        if SECTION.search(ln) and len(ln) < 60:
            in_section = True
            continue
        if len(ln) > 90 or not HARDWARE.search(ln):
            continue
        q, title = parse_qty_line(ln)
        counted = q != 1 or bool(re.match(r"^\s*1\s*[x×]?\s", ln))
        if not (in_section or counted):
            continue          # a hardware word in ordinary prose is not a parts list
        title = re.sub(r"\s+", " ", title).strip(" :.;,")
        if 2 < len(title) <= 80 and not any(o["title"].lower() == title.lower() for o in out):
            out.append({"title": title, "qty": q, "qty_known": counted, "link": None, "sku": None, "supplier": None})
    return out[:25]


def html_text(html: str | None) -> str:
    from ..services.makerworld import html_to_text
    return html_to_text(html)


# ---------- designers ----------
def designer_for(db: Session, platform: str, name: str | None, handle: str | None, url: str | None) -> Designer | None:
    if not (name or handle):
        return None
    handle = handle or name
    name = name or handle
    d = db.execute(select(Designer).where(Designer.platform == platform, Designer.handle == handle)).scalar()
    if not d:
        same = db.execute(select(Designer).where(Designer.name == name)).scalar()
        if same and (same.platform in (None, platform)):
            d = same
    if not d:
        taken = db.execute(select(Designer).where(Designer.name == name)).scalar()
        label = PLATFORM_LABEL.get(platform, platform)
        d = Designer(name=f"{name} ({label})" if taken else name, slug=slugify(f"{name}-{CODE_PREFIX.get(platform, 'x')}"),
                     platform=platform, handle=handle, url=url, always_credit=False)
        db.add(d)
        db.flush()
    else:
        d.platform = d.platform or platform
        d.handle = d.handle or handle
        d.url = d.url or url
    return d


def find_existing(db: Session, platform: str, ext_id: str, url: str | None = None) -> Model | None:
    """The model this link was imported as before: same site and id, same code, or the same source link."""
    code = f"{CODE_PREFIX[platform]}-{ext_id}"[:40]
    m = db.execute(select(Model).where(Model.source_platform == platform, Model.source_ext_id == str(ext_id))).scalar()
    m = m or db.execute(select(Model).where(Model.code == code)).scalar()
    if not m and url and platform != "makerworld":
        m = db.execute(select(Model).where(Model.source_url == url)).scalars().first()
    return m


def duplicate_files(db: Session, model: Model, shas: list[str]) -> list[dict]:
    """Files that are also in other models of the team (same SHA-256): {sha256, filename, model_id, code}."""
    from ..models import ModelFile
    if not shas:
        return []
    rows = db.execute(select(ModelFile.sha256, ModelFile.filename, Model.id, Model.code).join(Model, Model.id == ModelFile.model_id)
                      .where(ModelFile.sha256.in_(shas), ModelFile.model_id != model.id)).all()
    return [{"sha256": r[0], "filename": r[1], "model_id": r[2], "code": r[3]} for r in rows]


def preview_dict(db: Session, n: dict, logged_in: bool, needs_login_for_files: bool, selected_profile=None) -> dict:
    from ..services import licensing
    code = licensing.normalize(n.get("license_raw"), n["platform"])
    li = licensing.info(code)
    existing = find_existing(db, n["platform"], n["id"], n.get("url"))
    supplies = n.get("supplies") or []
    return {"platform": n["platform"], "platform_label": PLATFORM_LABEL[n["platform"]], "id": n["id"], "url": n["url"],
            "title": n["title"], "cover": (n.get("pictures") or n.get("photos") or [None])[0],
            "creator": (n.get("designer") or {}).get("name"),
            # Pictures to show (resized where the platform offers it); photos stay the originals, for importing.
            "pictures": [x for x in (n.get("pictures") or n.get("photos") or []) if x][:12],
            "license": code, "license_label": li["label"], "license_raw": n.get("license_raw"), "sellable": li["sell"],
            "needs_credit": li["credit"], "license_text": n.get("license_text"), "photos": len(n.get("photos") or []),
            "supplies": supplies, "summary": html_text(n.get("description_html"))[:600],
            "description": html_text(n.get("description_html"))[:6000], "tags": [t for t in n.get("tags") or [] if t][:20],
            "source_category": n.get("category"),
            "profiles": [{"id": p["id"], "title": p.get("title"), "printer": p.get("printer"), "time_s": p.get("time_s"),
                          "grams": p.get("grams"), "default": p.get("default", False),
                          "filaments": [{"type": f.get("type"), "color": f.get("color"), "usedG": f.get("grams")}
                                        for f in p.get("filaments") or []]} for p in n.get("profiles") or []],
            "files": [{"name": f["name"], "size": f.get("size"), "kind": f.get("kind")} for f in n.get("files") or []],
            "selected_profile": selected_profile, "existing_model_id": existing.id if existing else None,
            "logged_in": logged_in, "files_need_login": needs_login_for_files,
            "ip_flags": licensing.ip_flags(n["title"], " ".join(n.get("tags") or []),
                                           html_text(n.get("description_html"))[:3000])}


# ---------- files ----------
ZIP_MAX_TOTAL = 2_000_000_000
ZIP_MAX_ENTRIES = 500


def store_files(db: Session, model: Model, paths: list[Path], td: str, say_existing: bool = False) -> tuple[list, list[str]]:
    """Add model files (zips unpacked, with limits) to a model and its Manyfold library folder.
    Answers (new ModelFile rows, notes). A file already in the model (same SHA-256) is not added twice."""
    import zipfile
    from ..services import importer
    have = {f.sha256: f for f in model.files}
    added, notes = [], []

    def one(p: Path):
        before = len(have)
        mf = importer.add_model_file(db, model, p, have)
        if mf is not None and len(have) > before:
            added.append(mf)
        elif mf is not None and say_existing:
            notes.append(f"{p.name[:60]} is already in this model")

    for fp in paths:
        if fp.suffix.lower() != ".zip":
            if fp.name.lower().endswith(MODEL_EXT):
                one(fp)
            else:
                notes.append(f"{fp.name[:60]} skipped: not a model file (STL, 3MF, STEP, OBJ, F3D, SCAD, G-code, ZIP)")
            continue
        try:
            with zipfile.ZipFile(fp) as z:
                infos = [zi for zi in z.infolist() if not zi.is_dir()]
                if len(infos) > ZIP_MAX_ENTRIES or sum(zi.file_size for zi in infos) > ZIP_MAX_TOTAL:
                    notes.append(f"{fp.name[:60]} skipped: more than {ZIP_MAX_ENTRIES} files or 2 GB unpacked")
                    continue
                for zi in infos:
                    low = zi.filename.lower()
                    if not low.endswith(MODEL_EXT) or low.endswith(".zip") or zi.file_size > 800_000_000 \
                            or "__macosx/" in low:
                        continue
                    out = Path(td) / "unz" / re.sub(r'[<>:"\\|?*]+', "_", Path(zi.filename.replace("\\", "/")).name)
                    out.parent.mkdir(exist_ok=True)
                    out.write_bytes(z.read(zi))
                    one(out)
        except zipfile.BadZipFile:
            notes.append(f"{fp.name[:60]} is not a readable ZIP")
    return added, notes


# ---------- generic import ----------
def import_normalized(db: Session, n: dict, profile_id=None, fetch_files=None, page_html: str | None = None,
                      fetch_bytes=None, actor: str | None = None) -> tuple[Model, dict]:
    """n: normalized dict. fetch_files(profile) -> [(filename, bytes)]. fetch_bytes(url) -> bytes (photos)."""
    from ..services import importer, licensing
    from ..services.makerworld import ensure_part
    platform = n["platform"]
    report = {"warnings": [], "platform": platform}
    model = find_existing(db, platform, n["id"], n.get("url"))
    created = model is None
    code = f"{CODE_PREFIX[platform]}-{n['id']}"[:40]
    if created:
        model = Model(code=code, name=n["title"][:300], slug=importer.unique_slug(db, f"{code}-{n['title']}"),
                      source=platform, status="draft")
        db.add(model)
        db.flush()
    model.source_platform, model.source_ext_id = platform, str(n["id"])
    model.name = n["title"][:300]
    model.source = platform
    model.source_url = n["url"]
    desc = html_text(n.get("description_html"))
    model.description = desc or model.description
    model.summary = (desc.split("\n\n")[0][:500] if desc else model.summary)
    dz = n.get("designer") or {}
    model.designer = designer_for(db, platform, dz.get("name"), dz.get("handle"), dz.get("url")) or model.designer
    # The category is chosen at import, or suggested from the source's (import_language.finish): never made here.
    report["source_category"] = n.get("category")
    model.tags = sorted(set((model.tags or []) + [t for t in (n.get("tags") or []) if t][:20]))
    new_code = licensing.normalize(n.get("license_raw"), platform)
    if model.license_code and model.license_code not in (new_code, "COMMERCIAL"):
        report["warnings"].append(f"license changed on {PLATFORM_LABEL[platform]} from {model.license_code} to {new_code}")
    if model.license_code != "COMMERCIAL":
        licensing.apply(model, new_code)
    model.license_checked_at = datetime.now(timezone.utc)

    profiles = n.get("profiles") or []
    prof = next((p for p in profiles if profile_id is not None and str(p["id"]) == str(profile_id)), None) \
        or next((p for p in profiles if p.get("default")), None) or (profiles[0] if profiles else None)
    if prof:
        ps = {"profile": prof.get("title"), "printer": prof.get("printer"), "nozzle": prof.get("nozzle"),
              "layer_height": prof.get("layer_height"), "walls": prof.get("walls"), "infill": prof.get("infill"),
              "supports": prof.get("supports"), "print_time_s": prof.get("time_s"), "filament_grams": prof.get("grams"),
              "filament": ", ".join(f"{f.get('type') or ''} {f.get('color') or ''}".strip() for f in prof.get("filaments") or [])}
        model.print_settings = {k: v for k, v in ps.items() if v not in (None, "", [])}
    model.extra = {**(model.extra or {}), platform: {
        "id": n["id"], "stats": n.get("stats"), "updated": n.get("updated"),
        "profiles": [{k: p.get(k) for k in ("id", "title", "printer", "time_s", "grams", "filaments")} for p in profiles],
        "files": n.get("files"), "files_in_browser": bool(n.get("files_in_browser")),
        "download_page": n.get("download_page"), "from_browser": bool(n.get("from_browser"))}}
    model.library_rel = importer.library_folder(model)
    db.flush()

    getb = fetch_bytes or (lambda u: Http("photos").bytes(u))
    existing = {i.sha256: i for i in model.images}
    sort = max([i.sort for i in model.images] or [0])
    cover_bytes = None
    with tempfile.TemporaryDirectory() as td:
        seen = set()
        for u in [u for u in n.get("photos") or [] if u]:
            if u in seen:
                continue
            seen.add(u)
            try:
                b = getb(u)
            except ApiError as e:
                report["warnings"].append(f"photo skipped: {e.message[:80]}")
                continue
            cover_bytes = cover_bytes or b
            name = Path(u.split("?")[0]).name or f"photo-{len(seen)}.jpg"
            p = Path(td) / f"{len(seen):02d}_{name[-80:]}"
            p.write_bytes(b)
            sort += 10
            importer.add_image(db, model, p, "gallery", sort, None, existing)
        if model.images and not any(i.role == "hero" for i in model.images):
            first = sorted(model.images, key=lambda i: i.sort)[0]
            first.role, first.sort = "hero", 0

        report["files"] = 0
        if fetch_files:
            try:
                for fname, data in fetch_files(prof):
                    safe = re.sub(r'[<>:"/\\|?*]+', "_", fname)[-150:] or "model.3mf"
                    fp = Path(td) / safe
                    fp.write_bytes(data)
                    added, notes = store_files(db, model, [fp], td)
                    report["files"] += len(added)
                    report["warnings"] += notes
            except ApiError as e:
                report["warnings"].append(f"files not downloaded: {e.message[:160]}")
        importer.copy_preview_images(model)
    report["warnings"] += [w for w in n.get("_warnings") or []]
    if n.get("files_in_browser") and not model.files:
        report["files_in_browser"] = True
        report["download_page"] = n.get("download_page") or n["url"]
        report["warnings"].append(f"{PLATFORM_LABEL[platform]} files need your signed-in browser: open the model page and "
                                  "use the OpenPrintHQ add-on's Send files, or drop the files on the model's Files tab")
    dups = duplicate_files(db, model, [f.sha256 for f in model.files])
    if dups:
        report["duplicates"] = dups
        for d in dups[:5]:
            report["warnings"].append(f"file {d['filename'][:60]} is also in {d['code']} (same contents)")

    prod = importer.ensure_product(db, model, None, prof.get("grams") if prof else None)
    fils = (prof or {}).get("filaments") or []
    if fils and (created or not any(f.grams for f in prod.filaments) or len(prod.filaments) <= 1):
        prod.filaments.clear()
        db.flush()
        per = None
        if prof and prof.get("grams") and all(not f.get("grams") for f in fils):
            per = float(prof["grams"]) / len(fils)
        for i, f in enumerate(fils, start=1):
            try:
                grams = float(f.get("grams") or per or 0)
            except (TypeError, ValueError):
                grams = 0
            color = (f.get("color") or "")[:7] if str(f.get("color") or "").startswith("#") else None
            prod.filaments.append(BomFilament(slot=i, label="Body" if i == 1 else f"Color {i}", material=(f.get("type") or "PLA")[:40],
                                              color_hex=color, color_name=None, grams=round(grams, 1), waste_pct=5,
                                              follows_variant=(i == 1 and prod.color_mode == "options")))
    report["filament"] = fils
    report["supplies"] = []
    have = {b.name.lower() for b in prod.parts}
    for s in n.get("supplies") or []:
        if s["title"].lower() in have:
            continue
        try:
            pid = ensure_part(db, s["title"], s.get("link"), s.get("sku"), s.get("supplier"), origin=PLATFORM_LABEL[platform])
            prod.parts.append(BomPart(inventree_part_id=pid, name=s["title"][:300], qty=float(s.get("qty") or 1),
                                      note=(f"From the {PLATFORM_LABEL[platform]} description" if s.get("qty_known", True)
                                            else f"Listed on {PLATFORM_LABEL[platform]} without a count: set the quantity")))
            report["supplies"].append(s["title"])
            if not s.get("qty_known", True):
                report["warnings"].append(f"set how many '{s['title'][:40]}' each print uses (Materials tab)")
        except Exception as e:  # noqa: BLE001
            report["warnings"].append(f"supply '{s['title'][:40]}' not added: {str(e)[:100]}")
    db.flush()

    licensing.capture_generic(db, model, platform, n.get("raw") or {}, page_html, cover_bytes, n.get("license_raw"),
                              n.get("license_text"), n.get("api_url"), n["url"], designer=dz.get("name"),
                              design_updated=n.get("updated"))
    report.update({"model_id": model.id, "code": model.code, "license": model.license_code, "created": created,
                   "images": len(model.images)})
    return model, report
