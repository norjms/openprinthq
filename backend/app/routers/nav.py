"""The hub's sidebar as data, so the farm (and any other app that shares the sidebar) draws the same one.

GET /api/nav/v1 answers anyone: the groups and items are the same for everybody. Signed in, it also carries the
installation's name and, when the installation's farm is OpenPrintHQ with a public address (an installed
The farm at /apps/farm), each farm item's link to the farm's own page. Groups of an optional craft
(docs/crafts.md) are there only when the installation has that craft on. The list itself is
frontend/static/nav.json, the file the hub's own sidebar is built from.
"""
import copy
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db

router = APIRouter(prefix="/api/nav", tags=["nav"])

_HERE = Path(__file__).resolve()
NAV_PATHS = (
    _HERE.parents[2] / "static" / "nav.json",                                  # in the container: /app/static
    _HERE.parents[3] / "frontend" / "static" / "nav.json",                     # a source checkout
)
FARM_LABEL = {"openprinthq": "Farm"}


def load() -> dict:
    for p in NAV_PATHS:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise HTTPException(503, "the hub's navigation list is missing; rebuild the hub")


def farm_info() -> dict:
    """{"kind", "label", "app"}: the installation's farm, and the address of its own pages when the hub can link
    to them (OpenPrintHQ with a public address, which is what an installed farm has)."""
    from ..clients import public_url
    from ..services import installation
    kind = installation.chosen_farm()
    app = None
    if kind == "openprinthq":
        try:
            app = (public_url("openprinthq") or "").rstrip("/") or None
        except Exception:  # noqa: BLE001  (no team picked or no connection: the hub's own pages it is)
            app = None
    label = FARM_LABEL.get(kind or "", "The farm")
    if app and "/apps/farm" in app:
        label = "Farm"
    return {"kind": kind, "label": label, "app": app}


def _app_items(apps: list[dict], craft: str) -> list[dict]:
    """The sidebar's items for the desktop apps of one craft (docs/apps.md), each opening its desktop."""
    return [{"id": f"app-{a['id']}", "label": a["title"], "href": f"{a['path']}/", "icon": "tv", "app": a["id"],
             "desktop": True, **({"experimental": True} if a.get("experimental") else {})}
            for a in apps if a["craft"] == craft]


def resolve(nav: dict, farm: dict, crafts: list[str] | tuple[str, ...] = ("3d_printing",),
            apps: list[dict] | tuple[dict, ...] = ()) -> dict:
    """Every item gets its "app": "hub", "farm", or the app the list names (laser), and farm items the farm's own
    link when there is one. Items the list marks "app": "farm" are pages only the farm has (the Farm group): without
    a link to the farm's own pages they are left out, and so is a group left with no items. A group with "craft"
    is left out unless that craft is in crafts. Each desktop app in apps is added to its craft's group (the group
    with that craft, or for 3D printing the first group with none), after the group's own items."""
    out = copy.deepcopy(nav)
    out.pop("notes", None)
    groups = []
    placed_3d = False
    for g in out["groups"]:
        if g.get("craft") and g["craft"] not in crafts:
            continue
        craft = g.get("craft")
        if not craft and not placed_3d:
            craft, placed_3d = "3d_printing", True
        g["items"] = list(g["items"]) + (_app_items(list(apps), craft) if craft else [])
        items = []
        for it in g["items"]:
            farm_only = it.get("app") == "farm"
            if it.get("farm_path") and farm.get("app"):
                it["href"] = farm["app"] + it["farm_path"]
                it["app"] = "farm"
            elif farm_only:
                continue
            elif it.get("app") in (None, "hub"):
                it["app"] = "hub"
            elif it.get("desktop"):
                it.pop("desktop")
            items.append(it)
        if items:
            groups.append({**g, "items": items})
    out["groups"] = groups
    return out


@router.get("/v1")
def nav(request: Request, db: Session = Depends(get_db)):
    from .. import tenant
    from ..auth import current
    try:
        p = current(request, db)
    except HTTPException:
        p = None
    if p:
        tenant.set_team(p["team_id"])
    farm = farm_info() if p else {"kind": None, "label": "The farm", "app": None}
    from ..services import apps, crafts
    doc = resolve(load(), farm, crafts.chosen(), apps.registered())
    doc["installation"] = (p.get("team_name") if p else None)
    doc["farm"] = farm
    return doc
