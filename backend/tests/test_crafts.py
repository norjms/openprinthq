"""Crafts (docs/crafts.md): 3D Printing always on; Laser Crafting, Sublimation and Graphic Art chosen per
installation, at first-run setup or on Settings, and only the chosen ones in the sidebar."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from app.routers.nav import NAV_PATHS

TOKEN = "test-setup-token-crafts-0123"
needs_list = pytest.mark.skipif(not any(p.is_file() for p in NAV_PATHS), reason="needs frontend/static/nav.json")


def _set(**fields):
    from app.db import SessionLocal
    from app.models import Installation
    from app.services import installation
    s = SessionLocal()
    try:
        row = s.get(Installation, 1)
        for k, v in fields.items():
            setattr(row, k, v)
        s.commit()
    finally:
        s.close()
    installation.forget()


def _get(*names):
    from app.db import SessionLocal
    from app.models import Installation
    s = SessionLocal()
    try:
        row = s.get(Installation, 1)
        return {k: getattr(row, k) for k in names}
    finally:
        s.close()


@pytest.fixture(autouse=True)
def keep_installation():
    saved = _get("farm", "farm_source", "setup_completed_at", "setup_token_hash", "crafts")
    _set(crafts=None)
    yield
    _set(**saved)


@pytest.fixture
def client():
    from app.main import app
    from app.routers import setup
    setup._tries.clear()
    return TestClient(app)


def _key(scopes=("read", "write", "admin"), team_id=1):
    from app.auth import new_api_key
    from app.db import session_scope
    from app.tenant import use_team
    with session_scope() as db, use_team(team_id):
        return {"Authorization": f"Bearer {new_api_key(db, 'crafts test', list(scopes), team_id=team_id)}"}


def _groups(client, headers=None):
    return [g["label"] for g in client.get("/api/nav/v1", headers=headers or {}).json()["groups"]]


def test_craft_01_only_3d_printing_until_a_craft_is_chosen(client):
    """CRAFT-01: an installation with no crafts chosen has 3D printing on and nothing else."""
    from app.services import crafts
    assert crafts.chosen() == ["3d_printing"]
    assert client.get("/api/setup/v1/status").json()["crafts"] == ["3d_printing"]
    body = client.get("/api/crafts/v1", headers=_key()).json()
    assert body["enabled"] == ["3d_printing"]
    assert [(c["id"], c["always_on"], c["enabled"]) for c in body["crafts"]] == [
        ("3d_printing", True, True), ("laser", False, False), ("sublimation", False, False), ("graphic_art", False, False)]


def test_craft_02_names_are_checked_and_kept_in_order():
    """CRAFT-02: unknown crafts are refused, always-on ones need no choosing, the order is the list's."""
    from app.services import crafts
    assert crafts.normalise(["graphic_art", "3d_printing", "laser", "laser"]) == ["laser", "graphic_art"]
    assert crafts.normalise(None) == []
    with pytest.raises(ValueError, match="unknown craft 'woodwork'"):
        crafts.normalise(["laser", "woodwork"])
    assert crafts.enabled(["laser", "retired-craft"]) == ["3d_printing", "laser"]


def test_craft_03_first_run_setup_chooses_crafts(client):
    """CRAFT-03: the installer picks crafts in POST /api/setup/v1/initialize; an unknown craft fails setup."""
    _set(farm=None, farm_source=None, setup_completed_at=None, setup_token_hash=hashlib.sha256(TOKEN.encode()).hexdigest())
    auth = {"Authorization": f"Bearer {TOKEN}"}
    bad = client.post("/api/setup/v1/initialize", json={"team_name": "Lab", "crafts": ["laser", "pottery"]}, headers=auth)
    assert bad.status_code == 422 and "pottery" in bad.text
    assert client.get("/api/setup/v1/status").json()["setup_complete"] is False
    r = client.post("/api/setup/v1/initialize", json={"team_name": "Lab", "crafts": ["laser"]}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["crafts"] == ["3d_printing", "laser"]
    assert client.get("/api/setup/v1/status").json()["crafts"] == ["3d_printing", "laser"]


def test_craft_04_setup_without_crafts_keeps_3d_printing_only(client):
    """CRAFT-04: an installer that does not know about crafts still sets up a 3D printing hub."""
    _set(farm=None, farm_source=None, setup_completed_at=None, setup_token_hash=hashlib.sha256(TOKEN.encode()).hexdigest())
    r = client.post("/api/setup/v1/initialize", json={"farm": "openprinthq", "team_name": "Lab"},
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, r.text
    assert r.json()["crafts"] == ["3d_printing"]


def test_craft_05_an_admin_of_the_first_team_changes_them(client, monkeypatch):
    """CRAFT-05: PUT /api/crafts/v1 turns crafts on and off; it needs the settings permission in the first team."""
    admin = _key()
    on = client.put("/api/crafts/v1", json={"enabled": ["sublimation", "laser"]}, headers=admin)
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] == ["3d_printing", "laser", "sublimation"] and on.json()["can_change"] is True
    assert client.get("/api/crafts/v1", headers=admin).json()["enabled"] == ["3d_printing", "laser", "sublimation"]
    assert client.put("/api/crafts/v1", json={"enabled": ["weaving"]}, headers=admin).status_code == 422

    reader = _key(("read",))
    assert client.get("/api/crafts/v1", headers=reader).json()["can_change"] is False
    assert client.put("/api/crafts/v1", json={"enabled": []}, headers=reader).status_code == 403
    from app.config import settings
    monkeypatch.setattr(settings(), "dev_auth_bypass", False)
    assert client.put("/api/crafts/v1", json={"enabled": []}).status_code == 401
    assert client.get("/api/crafts/v1").status_code == 401
    off = client.put("/api/crafts/v1", json={"enabled": []}, headers=admin)
    assert off.json()["enabled"] == ["3d_printing"]


@needs_list
def test_craft_06_the_sidebar_shows_only_the_crafts_that_are_on(client):
    """CRAFT-06: a craft's group is in /api/nav/v1 only while the craft is on, for everybody, and the change shows
    at once."""
    base = ["Print", "Library", "Shop", "Admin"]
    assert _groups(client) == base
    client.put("/api/crafts/v1", json={"enabled": ["laser", "graphic_art"]}, headers=_key())
    assert _groups(client) == ["Print", "Laser Crafting", "Graphic Art", "Library", "Shop", "Admin"]
    client.put("/api/crafts/v1", json={"enabled": ["sublimation"]}, headers=_key())
    assert _groups(client) == ["Print", "Sublimation", "Library", "Shop", "Admin"]


@needs_list
def test_craft_07_laser_items_belong_to_the_laser_app():
    """CRAFT-07: Convert files is the OpenPrintHQ Laser app's page at /apps/laser; the overview is the hub's."""
    from app.routers.nav import load, resolve
    d = resolve(load(), {"app": None}, ["3d_printing", "laser"])
    laser = next(g for g in d["groups"] if g["label"] == "Laser Crafting")
    assert laser["craft"] == "laser"
    assert [(i["label"], i["href"], i["app"], i["icon"]) for i in laser["items"]] == [
        ("Laser overview", "/crafts/laser", "hub", "laser"), ("Convert files", "/apps/laser/", "laser", "swap")]
    all_on = resolve(load(), {"app": None}, ["3d_printing", "laser", "sublimation", "graphic_art"])
    icons = {i["id"]: i["icon"] for g in all_on["groups"] for i in g["items"]}
    assert icons["sublimation"] == "shirt" and icons["graphic-art"] == "palette"


@needs_list
def test_craft_08_every_sidebar_icon_is_in_the_ui_package():
    """CRAFT-08: each icon nav.json names exists in the pinned openprinthq-ui (a missing one draws nothing)."""
    import re
    from app.routers.nav import load
    pkg = NAV_PATHS[1].parents[1] / "node_modules" / "openprinthq-ui" / "src" / "lib" / "icons.js"
    if not pkg.is_file():
        pytest.skip("needs frontend/node_modules (npm install)")
    names = set(re.findall(r'^\s*"([a-z-]+)":', pkg.read_text(), re.M))
    used = {i["icon"] for g in load()["groups"] for i in g["items"]}
    assert used <= names, used - names
