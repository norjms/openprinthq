"""Desktop apps, /api/apps/v1 (docs/apps.md): which desktop apps this installation runs.

Anyone signed in may read them. The installer sets them, with its key: the hub owner, or the settings permission in
the installation's first team, as for the crafts.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current
from ..db import get_db
from ..models import Installation
from ..services import apps, crafts, installation
from .crafts import may_change

router = APIRouter(prefix="/api/apps", tags=["apps"])


class AppOut(BaseModel):
    id: str
    title: str
    craft: str
    path: str
    experimental: bool
    craft_on: bool


class AppsOut(BaseModel):
    apps: list[AppOut]
    can_change: bool


class AppIn(BaseModel):
    id: str = Field(max_length=31)
    title: str = Field(max_length=120)
    craft: str = Field(max_length=40)
    path: str = Field(default="", max_length=80)
    experimental: bool = False


class AppsIn(BaseModel):
    apps: list[AppIn] = Field(max_length=apps.MAX_APPS)


def _answer(p: dict) -> AppsOut:
    on = set(crafts.chosen())
    return AppsOut(apps=[AppOut(**a, craft_on=a["craft"] in on) for a in apps.registered()], can_change=may_change(p))


@router.get("/v1", response_model=AppsOut)
def get_apps(p=Depends(current)) -> AppsOut:
    return _answer(p)


@router.put("/v1", response_model=AppsOut)
def put_apps(body: AppsIn, p=Depends(current), db: Session = Depends(get_db)) -> AppsOut:
    if not may_change(p):
        raise HTTPException(403, "only the hub owner or an admin of the first team can change the apps")
    try:
        picked = apps.normalise([a.model_dump() for a in body.apps])
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    row = db.get(Installation, 1)
    if row is None:
        raise HTTPException(503, "the hub is still starting; try again in a moment")
    row.apps = picked
    db.commit()
    installation.forget()
    return _answer(p)
