"""Add a model by URL from the OpenPrintHQ browser add-on (Firefox, Chrome, Edge).

The add-on signs in with a clip key: a person's own API key with only the `clip` scope. It can look at a link,
import it, follow the import and send files to a model that was imported by link - nothing else (no reading the
catalogue, no store, no settings). The person makes and revokes their own clip keys in Settings > Browser add-on;
a clip key stops working when its person leaves the team or loses the "add models" permission.
Signed-in people (and full keys) can call the same routes.

Routes (all under /api/clip/v1):
  GET  /hello                     who the key belongs to, and the supported sites (Test connection)
  GET  /sites                     supported sites, their hosts and whether the hub or the browser fetches files
  POST /preview  {url, capture?}  what would be imported
  POST /import   {url, capture?, download_files?, category?}  -> {job_id, model_id?}
  GET  /jobs/{id}                 the import's progress and result
  POST /files    multipart: model_id or source_url, files[]  -> files added to that model
  GET|POST /keys, DELETE /keys/{id}   your own clip keys (signed in only)
"""
import shutil
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, current, new_api_key
from ..clients.base import ApiError
from ..config import settings
from ..db import get_db
from ..jobs import enqueue, event
from ..models import ApiKey, Job, Model
from .. import sources as srcs
from .. import tenant

router = APIRouter(prefix="/api/clip/v1", tags=["clipper"])

MAX_UPLOAD = 500 * 1024 * 1024
MAX_UPLOAD_TOTAL = 2 * 1024 * 1024 * 1024
MAX_UPLOAD_FILES = 50
SITES = [
    {"platform": "printables", "label": "Printables", "hosts": ["printables.com"], "files": "hub",
     "needs": None, "notes": "Public GraphQL API; free models download on the hub. Paid or club models: Send files."},
    {"platform": "thingiverse", "label": "Thingiverse", "hosts": ["thingiverse.com"], "files": "hub",
     "needs": "Thingiverse App Token in Settings", "notes": "Official REST API."},
    {"platform": "makerworld", "label": "MakerWorld", "hosts": ["makerworld.com"], "files": "hub",
     "needs": "MakerWorld account in Settings for files", "notes": "Print profiles, filament and hardware too."},
    {"platform": "creality", "label": "Creality Cloud", "hosts": ["crealitycloud.com"], "files": "hub",
     "needs": "Creality Cloud account in Settings for files", "notes": ""},
    {"platform": "thangs", "label": "Thangs", "hosts": ["thangs.com"], "files": "browser", "needs": None,
     "notes": "Details from Thangs' public JSON; downloads need your signed-in browser."},
    {"platform": "myminifactory", "label": "MyMiniFactory", "hosts": ["myminifactory.com"], "files": "browser",
     "needs": None, "notes": "Details from the object page; downloads need your signed-in browser."},
    {"platform": "cults3d", "label": "Cults3D", "hosts": ["cults3d.com"], "files": "browser",
     "needs": "Cults3D API key in Settings (otherwise the add-on reads the page)",
     "notes": "Cults3D blocks servers; the add-on sends what the page shows and the files you download."},
    {"platform": "web", "label": "Any other link", "hosts": [], "files": "hub", "needs": None,
     "notes": "A direct STL/3MF/STEP/OBJ/ZIP link, or a page's own title, photos and license (public sites only)."},
]


def need_clip(p: Principal = Depends(current)) -> Principal:
    perms = p.get("perms") or []
    if "clip" not in perms and "models" not in perms:
        raise HTTPException(403, "this key can't add models")
    tenant.set_team(p["team_id"])
    return p


def _clip_only(p: Principal) -> bool:
    return "models" not in (p.get("perms") or [])


class UrlIn(BaseModel):
    url: str = Field(max_length=4000)
    capture: dict | None = None            # what the add-on read off the page (sites that refuse the hub)


class ImportIn(UrlIn):
    download_files: bool = True
    category: str | None = None
    profile_id: str | None = None


def _capture(c: dict | None) -> dict | None:
    if not c:
        return None
    import json
    if len(json.dumps(c, default=str)) > 60_000:
        raise HTTPException(413, "the page capture is too large")
    return c


@router.get("/hello")
def hello(db: Session = Depends(get_db), p: Principal = Depends(need_clip)):
    return {"ok": True, "team": p.get("team_name"), "who": p.actor, "clip_only": _clip_only(p),
            "hub": settings().public_url, "sites": SITES}


@router.get("/sites")
def sites(p: Principal = Depends(need_clip)):
    return {"sites": SITES}


def _err(platform: str, e: ApiError):
    raise HTTPException(502 if e.status not in (400, 401, 403, 404, 413) else 400,
                        f"{srcs.PLATFORM_LABEL.get(platform, platform)}: {e.message}")


@router.post("/preview")
def preview(body: UrlIn, db: Session = Depends(get_db), p: Principal = Depends(need_clip)):
    from . import sourcing
    try:
        platform, ext_id = srcs.detect(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if platform == "makerworld":
        d = sourcing.mw_preview(body.url, db, p)
        out = {**d, "platform": "makerworld", "platform_label": "MakerWorld", "id": str(d["id"]),
               "files_in_browser": False}
    else:
        try:
            out = srcs.preview(db, body.url, _capture(body.capture))
        except ApiError as e:
            _err(platform, e)
    out["supported"] = platform != "web"
    if _clip_only(p) and out.get("existing_model_id"):
        out["existing_model_url"] = f"{settings().public_url}/models/{out['existing_model_id']}"
    return out


@router.post("/import")
def do_import(body: ImportIn, db: Session = Depends(get_db), p: Principal = Depends(need_clip)):
    from . import sourcing
    try:
        platform, ext_id = srcs.detect(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    have = srcs.base.find_existing(db, platform, ext_id, body.url.strip()) if platform != "makerworld" else \
        db.execute(select(Model).where(Model.makerworld_id == int(ext_id))).scalar()
    if platform == "makerworld":
        r = sourcing.mw_import(sourcing.ImportIn(url=body.url, download_files=body.download_files, category=body.category), db, p)
        j = db.get(Job, r["job"])
        if j:
            j.payload = {**(j.payload or {}), "by_user": p.get("user_id"), "via": "browser add-on"}
            db.commit()
        return {"job_id": r["job"], "platform": platform, "model_id": have.id if have else None, "files_in_browser": False}
    j = enqueue(db, "source.import", ref=f"{platform}:{ext_id}",
                payload={"platform": platform, "id": ext_id, "url": body.url.strip(), "profile_id": body.profile_id,
                         "download_files": body.download_files, "category": body.category,
                         "capture": _capture(body.capture), "via": "browser add-on", "by_user": p.get("user_id")},
                actor=p.actor, max_attempts=4)
    db.commit()
    return {"job_id": j.id, "platform": platform, "model_id": have.id if have else None,
            "files_in_browser": platform in srcs.BROWSER_FILES}


@router.get("/jobs/{job_id}")
def job(job_id: int, db: Session = Depends(get_db), p: Principal = Depends(need_clip)):
    j = db.get(Job, job_id)
    if not j or j.kind not in ("source.import", "makerworld.import") or \
            (_clip_only(p) and (j.payload or {}).get("by_user") != p.get("user_id")):
        raise HTTPException(404)
    res = j.result or {}
    keep = ("model_id", "code", "files", "images", "license", "created", "warnings", "files_in_browser",
            "download_page", "duplicates", "platform")
    out = {"id": j.id, "status": j.state, "attempts": j.attempts, "max_attempts": j.max_attempts,
           "error": (j.error or "")[:500] or None,
           "result": {k: res.get(k) for k in keep if k in res} if j.state == "done" else None}
    if out["result"] and res.get("model_id"):
        out["result"]["model_url"] = f"{settings().public_url}/models/{res['model_id']}"
    return out


@router.post("/files")
async def send_files(request: Request, files: list[UploadFile] = File(...), model_id: int | None = Form(None),
                     source_url: str | None = Form(None), db: Session = Depends(get_db),
                     p: Principal = Depends(need_clip)):
    """Files the browser downloaded (signed-in sites), added to the model imported from that link.
    A clip key may only add files to models that were imported by link."""
    from starlette.concurrency import run_in_threadpool
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(413, f"at most {MAX_UPLOAD_FILES} files at a time")
    m = None
    if model_id:
        m = db.get(Model, model_id)
    elif source_url:
        try:
            platform, ext_id = srcs.detect(source_url)
        except ValueError as e:
            raise HTTPException(400, str(e))
        m = srcs.base.find_existing(db, platform, ext_id, source_url.strip()) if platform != "makerworld" else \
            db.execute(select(Model).where(Model.makerworld_id == int(ext_id))).scalar()
    if not m or m.team_id != p.team_id:
        raise HTTPException(404, "no model was imported from that link yet: add it first")
    if _clip_only(p) and not (m.source_platform or m.makerworld_id):
        raise HTTPException(403, "the add-on can only send files to models that were added by link")
    work = Path(settings().data_dir) / "clip" / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    total = 0
    paths = []
    try:
        for f in files:
            name = Path((f.filename or "file").replace("\\", "/")).name[-150:] or "file"
            dst = work / name
            n = 0
            with dst.open("wb") as out:
                while True:
                    chunk = await f.read(1024 * 1024)
                    if not chunk:
                        break
                    n += len(chunk)
                    total += len(chunk)
                    if n > MAX_UPLOAD:
                        raise HTTPException(413, f"{name} is over {MAX_UPLOAD // 1048576} MB")
                    if total > MAX_UPLOAD_TOTAL:
                        raise HTTPException(413, "over 2 GB in one send")
                    out.write(chunk)
            paths.append(dst)
        added, notes = await run_in_threadpool(_store, db, m, paths, str(work), p)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"model_id": m.id, "code": m.code, "added": [a["filename"] for a in added], "notes": notes,
            "files": len(m.files), "model_url": f"{settings().public_url}/models/{m.id}"}


def _store(db: Session, m: Model, paths: list[Path], td: str, p: Principal):
    from ..tasks import after_new_files
    tenant.set_team(p["team_id"])
    added, notes = srcs.base.store_files(db, m, paths, td, say_existing=True)
    dups = srcs.base.duplicate_files(db, m, [a.sha256 for a in added])
    for d in dups[:5]:
        notes.append(f"{d['filename'][:60]} is also in {d['code']} (same contents)")
    if added:
        plat = m.source_platform or "makerworld"
        ex = dict(m.extra or {})
        if isinstance(ex.get(plat), dict):
            ex[plat] = {**ex[plat], "files_in_browser": False}
            m.extra = ex
        event(db, f"model:{m.id}", f"{len(added)} file(s) sent from the browser add-on: "
                                   + ", ".join(a.filename for a in added[:6]), actor=p.actor)
        after_new_files(db, m)
    db.commit()
    return [{"filename": a.filename, "sha256": a.sha256} for a in added], notes


# ---------- your own clip keys ----------
class KeyIn(BaseModel):
    name: str = Field(default="Browser add-on", max_length=80)
    expires_in_days: int | None = 365


def _signed_in(p: Principal):
    if p.get("kind") != "user" or not p.get("user_id"):
        if p.get("kind") == "user" and settings().dev_auth_bypass:
            return
        raise HTTPException(403, "sign in to the hub to manage add-on keys")
    if "models" not in (p.get("perms") or []):
        raise HTTPException(403, "you can't add models in this team")


@router.get("/keys")
def keys(db: Session = Depends(get_db), p: Principal = Depends(current)):
    _signed_in(p)
    rows = db.execute(select(ApiKey).where(ApiKey.team_id == p.team_id, ApiKey.user_id == p.get("user_id"),
                                           ApiKey.revoked.is_(False)).order_by(ApiKey.id)).scalars()
    return {"keys": [{"id": k.id, "name": k.name, "prefix": k.prefix,
                      "created_at": k.created_at.isoformat() if k.created_at else None,
                      "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                      "expires_at": k.expires_at.isoformat() if k.expires_at else None}
                     for k in rows if list(k.scopes or []) == ["clip"]]}


@router.post("/keys")
def make_key(body: KeyIn, db: Session = Depends(get_db), p: Principal = Depends(current)):
    from ..models import now
    _signed_in(p)
    if body.expires_in_days is not None and not 1 <= body.expires_in_days <= 3650:
        raise HTTPException(400, "expires_in_days is 1 to 3650")
    raw = new_api_key(db, (body.name.strip() or "Browser add-on")[:120], ["clip"], team_id=p.team_id, user_id=p.get("user_id"))
    k = db.execute(select(ApiKey).where(ApiKey.prefix == raw.split("_")[1])).scalar()
    k.expires_at = now() + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    k.note = "OpenPrintHQ browser add-on (clip scope: add by link and send files only)"
    event(db, "api", f"Browser add-on key '{k.name}' made", actor=p.actor)
    db.commit()
    return {"key": raw, "id": k.id, "hub": settings().public_url,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None}


@router.delete("/keys/{key_id}")
def revoke_key(key_id: int, db: Session = Depends(get_db), p: Principal = Depends(current)):
    _signed_in(p)
    k = db.get(ApiKey, key_id)
    if not k or k.team_id != p.team_id or k.user_id != p.get("user_id") or list(k.scopes or []) != ["clip"]:
        raise HTTPException(404)
    k.revoked = True
    event(db, "api", f"Browser add-on key '{k.name}' revoked", actor=p.actor)
    db.commit()
    return {"ok": True}
