"""Crafts, /api/crafts/v1 (docs/crafts.md): which crafts this installation shows tools for.

Anyone signed in may read them. Changing them changes the sidebar for everybody, so it needs the hub owner, or the
settings permission in the installation's first team (which is what the installer's key has).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current
from ..db import get_db
from ..models import Installation
from ..services import crafts, installation

router = APIRouter(prefix="/api/crafts", tags=["crafts"])


class CraftOut(BaseModel):
    id: str
    label: str
    summary: str
    always_on: bool
    enabled: bool


class CraftsOut(BaseModel):
    crafts: list[CraftOut]
    enabled: list[str]
    can_change: bool


class CraftsIn(BaseModel):
    enabled: list[str] = Field(max_length=20)


def may_change(p: dict) -> bool:
    return bool(p.get("platform_admin")) or (p.get("team_id") == 1 and "settings" in (p.get("perms") or []))


def _answer(p: dict) -> CraftsOut:
    on = crafts.chosen()
    return CraftsOut(crafts=[CraftOut(**c) for c in crafts.describe(on)], enabled=on, can_change=may_change(p))


@router.get("/v1", response_model=CraftsOut)
def get_crafts(p=Depends(current)) -> CraftsOut:
    return _answer(p)


@router.put("/v1", response_model=CraftsOut)
def put_crafts(body: CraftsIn, p=Depends(current), db: Session = Depends(get_db)) -> CraftsOut:
    if not may_change(p):
        raise HTTPException(403, "only the hub owner or an admin of the first team can change the crafts")
    try:
        picked = crafts.normalise(body.enabled)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    row = db.get(Installation, 1)
    if row is None:
        raise HTTPException(503, "the hub is still starting; try again in a moment")
    row.crafts = picked
    db.commit()
    installation.forget()
    return _answer(p)
