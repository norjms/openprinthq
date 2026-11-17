"""The sidebar as data (/api/nav/v1), shared with ArtisanHub Farm, and the redirects from pages that moved."""
import pytest
from fastapi.testclient import TestClient

from app.routers.nav import NAV_PATHS

# The list is frontend/static/nav.json; a run that has only the backend folder cannot check it.
needs_list = pytest.mark.skipif(not any(p.is_file() for p in NAV_PATHS), reason="needs frontend/static/nav.json")

APPROVED = {
    "Print": ["Farm overview", "Printers", "Cameras", "Print queue", "Filament", "Slicing"],
    "Library": ["Models", "Add model", "Find models", "Licenses"],
    "Shop": ["Orders", "Quotes", "Inventory", "Shopping list", "Reports"],
    "Admin": ["Sync health", "People", "Connections", "Devices", "Settings"],
}


def _client():
    from app.main import app
    return TestClient(app, follow_redirects=False)


@needs_list
def test_nav_is_the_approved_list():
    r = _client().get("/api/nav/v1")
    assert r.status_code == 200
    d = r.json()
    assert d["version"] == 1 and d["product"] == "ArtisanHub"
    assert {g["label"]: [i["label"] for i in g["items"]] for g in d["groups"]} == APPROVED
    for g in d["groups"]:
        for it in g["items"]:
            assert it["href"].startswith("/") and it["icon"] and it["app"] in ("hub", "farm")


@needs_list
def test_farm_items_link_to_an_installed_farm():
    from app.routers.nav import load, resolve
    nav = load()
    hub = resolve(nav, {"app": None})
    farm = resolve(nav, {"app": "https://hub.example/apps/farm"})
    by_id = lambda d: {i["id"]: i for g in d["groups"] for i in g["items"]}  # noqa: E731
    assert by_id(hub)["printers"] == {**by_id(hub)["printers"], "href": "/farm?view=printers", "app": "hub"}
    assert by_id(farm)["printers"]["href"] == "https://hub.example/apps/farm/app/printers"
    assert by_id(farm)["printers"]["app"] == "farm"
    assert by_id(farm)["models"]["href"] == "/" and by_id(farm)["models"]["app"] == "hub"


FARM_GROUP = ["Files", "Slicer", "Projects", "Statistics", "Print reports", "Timelapses", "Farm settings"]


@needs_list
def test_the_farm_group_shows_only_with_the_farms_own_pages():
    from app.routers.nav import load, resolve
    nav = load()
    labels = lambda d: [g["label"] for g in d["groups"]]  # noqa: E731
    hub = resolve(nav, {"app": None})
    assert labels(hub) == list(APPROVED)
    assert not any(i.get("app") == "farm" for g in hub["groups"] for i in g["items"])
    farm = resolve(nav, {"app": "https://hub.example/apps/farm"})
    assert labels(farm) == ["Print", "Farm", "Library", "Shop", "Admin"]
    group = next(g for g in farm["groups"] if g["label"] == "Farm")
    assert [i["label"] for i in group["items"]] == FARM_GROUP
    paths = {i["label"]: i["farm_path"] for i in group["items"]}
    assert paths == {"Files": "/app/files", "Slicer": "/app/slicer", "Projects": "/app/projects",
                     "Statistics": "/app/statistics", "Print reports": "/app/reports",
                     "Timelapses": "/app/timelapses", "Farm settings": "/app/settings"}
    for it in group["items"]:
        assert it["app"] == "farm" and it["href"] == "https://hub.example/apps/farm" + it["farm_path"]
    # Timelapses lives in the Farm group only; the list in the file is not changed by resolving it.
    assert sum(i["id"] == "timelapses" for g in farm["groups"] for i in g["items"]) == 1
    assert "href" not in next(i for g in nav["groups"] for i in g["items"] if i["id"] == "timelapses")
    # One Cameras page, the hub's, with or without the farm's app.
    cams = next(i for g in farm["groups"] for i in g["items"] if i["id"] == "cameras")
    assert cams["href"] == "/farm?view=cameras" and cams.get("app") != "farm"


@needs_list
def test_the_api_shows_the_farm_group_to_people_signed_in_with_an_installed_farm(monkeypatch):
    from app.config import settings
    from app.routers import nav as nav_router
    monkeypatch.setattr(nav_router, "farm_info", lambda: {"kind": "openprinthq", "label": "ArtisanHub Farm",
                                                          "app": "https://hub.example/apps/farm"})
    signed_in = _client().get("/api/nav/v1").json()       # the tests' development sign-in
    assert [g["label"] for g in signed_in["groups"]][:2] == ["Print", "Farm"]
    monkeypatch.setattr(nav_router, "farm_info", lambda: {"kind": None, "label": "the farm", "app": None})
    assert "Farm" not in [g["label"] for g in _client().get("/api/nav/v1").json()["groups"]]
    monkeypatch.setattr(settings(), "dev_auth_bypass", False)
    anon = _client().get("/api/nav/v1").json()
    assert "Farm" not in [g["label"] for g in anon["groups"]]


def test_moved_pages_redirect():
    c = _client()
    for old, new in (("/team", "/people"), ("/platform", "/people?view=hub"), ("/add-on", "/devices"),
                     ("/tv?code=ABCD-EFGH", "/devices?code=ABCD-EFGH"), ("/printers", "/slicing?tab=printers")):
        r = c.get(old)
        assert r.status_code == 301 and r.headers["location"] == new, old
