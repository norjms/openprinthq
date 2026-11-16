"""Filament mapping: the screens' API, on the web (/api/farm/...) and for the phone app (/api/mobile/v1/farm/...).

See services/filament_mapping.py and docs/filament-mapping.md.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import Principal, need
from ..db import get_db
from ..services import farm, printer_access
from ..services import filament_mapping as fmap

router = APIRouter()
PREFIXES = ("/api/farm", "/api/mobile/v1/farm")
LEVELS = ("exact", "close", "family", "any")


class Slot(BaseModel):
    slot_id: int = Field(ge=1, le=64)
    global_tray_id: int = Field(ge=-1, le=255)


class MapIn(BaseModel):
    printer_id: int
    slots: list[Slot] = Field(min_length=1, max_length=64)
    remember: bool = True


class LevelIn(BaseModel):
    level: str | None = None


class SettingsIn(BaseModel):
    level: str | None = None
    material_groups: dict[str, str] | None = None


def _actor(p: Principal) -> str:
    return p.get("username") or p.get("email") or "api"


def _call(fn):
    try:
        return fn()
    except HTTPException:
        raise
    except fmap.MappingError as e:
        raise HTTPException(e.status, e.detail)
    except Exception as e:  # noqa: BLE001
        status = getattr(e, "status", None)
        if status == 404:
            raise HTTPException(404, "Job not found")
        if type(e).__name__ == "FarmUnsupported":
            raise HTTPException(400, "The farm doesn't support filament mapping yet; update ArtisanHub Farm")
        raise HTTPException(502, (getattr(e, "message", None) or str(e))[:300])


def _mine(db: Session, p: Principal, opts: dict) -> dict:
    """Only the printers this person may use."""
    opts["printers"] = printer_access.visible_items(db, p, opts.get("printers") or [], id_field="printer_id",
                                                    name_fields=("name",))
    return opts


def get_options(job_id: int, all_printers: bool = False, db: Session = Depends(get_db),
                p: Principal = Depends(need("view"))):
    """What the job needs, and for each printer it could go to: what is loaded and the best mapping."""
    opts = _call(lambda: fmap.options(db, farm._client(), job_id, all_printers=all_printers))
    return _mine(db, p, opts)


def post_mapping(job_id: int, body: MapIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """Print the job with the chosen mapping: it starts, or the hub reslices it first when the mapping changes the
    material or the printer model."""
    c = farm._client()
    opts = _mine(db, p, _call(lambda: fmap.options(db, c, job_id, all_printers=True)))
    if not any(r.get("printer_id") == body.printer_id for r in opts["printers"]):
        raise HTTPException(404, "Printer not found")
    out = _call(lambda: fmap.apply(db, c, job_id, body.printer_id, [s.model_dump() for s in body.slots],
                                   body.remember, _actor(p)))
    db.commit()
    return out


def put_level(job_id: int, body: LevelIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    if body.level is not None and body.level not in LEVELS:
        raise HTTPException(400, "level is exact, close, family or any")
    _call(lambda: farm._client().set_job_match_level(job_id, body.level))
    return {"ok": True, "level": body.level}


def unmatched(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """Waiting jobs that need their filament mapped, first in the queue first."""
    rows = _call(lambda: farm._client().filament_unmatched())
    out = []
    for r in rows:
        r = dict(r)
        r["printers"] = printer_access.visible_items(db, p, r.get("printers") or [], id_field="printer_id",
                                                     name_fields=("printer_name",))
        if r["printers"]:
            out.append(r)
    return {"jobs": out}


def get_settings(p: Principal = Depends(need("view"))):
    return _call(lambda: farm._client().filament_settings())


def put_settings(body: SettingsIn, db: Session = Depends(get_db), p: Principal = Depends(need("admin"))):
    if body.level is not None and body.level not in LEVELS:
        raise HTTPException(400, "level is exact, close, family or any")
    return _call(lambda: farm._client().set_filament_settings(body.level, body.material_groups))


def aliases(p: Principal = Depends(need("view"))):
    return {"aliases": _call(lambda: farm._client().filament_aliases())}


def delete_alias(alias_id: int, p: Principal = Depends(need("farm"))):
    return _call(lambda: farm._client().delete_filament_alias(alias_id))


for _pre in PREFIXES:
    router.add_api_route(f"{_pre}/jobs/{{job_id}}/filament", get_options, methods=["GET"])
    router.add_api_route(f"{_pre}/jobs/{{job_id}}/filament", post_mapping, methods=["POST"])
    router.add_api_route(f"{_pre}/jobs/{{job_id}}/match-level", put_level, methods=["PUT"])
    router.add_api_route(f"{_pre}/filament-matching/unmatched", unmatched, methods=["GET"])
    router.add_api_route(f"{_pre}/filament-matching/settings", get_settings, methods=["GET"])
    router.add_api_route(f"{_pre}/filament-matching/settings", put_settings, methods=["PUT"])
    router.add_api_route(f"{_pre}/filament-matching/aliases", aliases, methods=["GET"])
    router.add_api_route(f"{_pre}/filament-matching/aliases/{{alias_id}}", delete_alias, methods=["DELETE"])
