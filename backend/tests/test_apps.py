"""Desktop apps (docs/apps.md): the installer tells the hub which desktop apps it runs; each is in its craft's
sidebar group, and People gives each to anyone through its group in the user directory, as for OrcaSlicer."""

import pytest
from fastapi.testclient import TestClient

from tests.test_crafts import _get, _key, _set, needs_list
from tests.test_orcaslicer_access import FakeLldap, _events, orca  # noqa: F401  (fixtures)
from tests.test_teams import as_user, people, real_auth  # noqa: F401  (fixtures)

BAMBU = {"id": "bambustudio", "title": "Bambu Studio", "craft": "3d_printing", "path": "/apps/bambustudio"}
XTOOL = {"id": "xtoolstudio", "title": "xTool Studio", "craft": "laser", "path": "/apps/xtoolstudio",
         "experimental": True}


@pytest.fixture(autouse=True)
def keep_installation():
    saved = _get("crafts", "apps")
    _set(crafts=None, apps=None)
    yield
    _set(**saved)


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_apps_01_names_are_checked():
    """APPS-01: each app once, a known craft, its own path; anything else refused whole."""
    from app.services import apps
    got = apps.normalise([BAMBU, {**XTOOL, "path": "/apps/xtoolstudio/"}])
    assert [a["id"] for a in got] == ["bambustudio", "xtoolstudio"] and got[1]["experimental"] is True
    assert got[0]["path"] == "/apps/bambustudio" and got[0]["experimental"] is False
    for bad in ([BAMBU, BAMBU], [{**BAMBU, "craft": "pottery"}], [{**BAMBU, "path": "/apps/other"}],
                [{**BAMBU, "id": "../x"}], [{**BAMBU, "title": " "}]):
        with pytest.raises(ValueError):
            apps.normalise(bad)
    assert apps.group("bambustudio") == "bambustudio-users"


def test_apps_02_the_installer_sets_them_and_anyone_signed_in_reads_them(client):
    """APPS-02: PUT /api/apps/v1 needs the owner or the first team's settings permission; GET anyone signed in."""
    assert client.get("/api/apps/v1", headers=_key()).json()["apps"] == []
    r = client.put("/api/apps/v1", headers=_key(), json={"apps": [BAMBU, XTOOL]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(a["id"], a["craft_on"]) for a in body["apps"]] == [("bambustudio", True), ("xtoolstudio", False)]
    assert client.put("/api/apps/v1", headers=_key(), json={"apps": [{**BAMBU, "craft": "x"}]}).status_code == 422
    assert client.put("/api/apps/v1", headers=_key(scopes=("read",)), json={"apps": []}).status_code == 403


@needs_list
def test_apps_03_each_app_is_in_its_crafts_sidebar_group(client):
    """APPS-03: a 3D printing app after the Print group's own items; another craft's only while that craft is on."""
    _set(apps=[BAMBU, XTOOL])
    groups = {g["label"]: g["items"] for g in client.get("/api/nav/v1", headers=_key()).json()["groups"]}
    item = groups["Print"][-1]
    # It opens the hub's page for the app, which shows its desktop inside the hub.
    assert item == {"id": "app-bambustudio", "label": "Bambu Studio", "href": "/open/bambustudio", "icon": "tv",
                    "app": "hub", "frame": "/apps/bambustudio/"}
    assert "Laser Crafting" not in groups
    _set(crafts=["laser"])
    groups = {g["label"]: g["items"] for g in client.get("/api/nav/v1", headers=_key()).json()["groups"]}
    xtool = groups["Laser Crafting"][-1]
    assert xtool["href"] == "/open/xtoolstudio" and xtool["frame"] == "/apps/xtoolstudio/"
    assert xtool["experimental"] is True


def test_apps_04_the_owner_gives_an_app_on_people(orca, real_auth):  # noqa: F811
    """APPS-04: who may open each app: the hub's owners always, others through the app's group, logged."""
    _set(apps=[BAMBU])
    hub = as_user(orca["boss"])
    seen = hub.get("/api/platform/apps").json()["apps"]
    assert [(a["id"], a["group"]) for a in seen] == [("bambustudio", "bambustudio-users")]
    assert orca["boss"] in seen[0]["allowed"] and orca["member"] not in seen[0]["allowed"]
    path = f"/api/platform/users/{orca['member']}/apps/bambustudio"
    r = hub.put(path, json={"allowed": True})
    assert r.status_code == 200 and r.json()["changed"] is True
    assert "bambustudio-users" in orca["lldap"].logins["mo"]["groups"]
    assert hub.put(path, json={"allowed": True}).json()["changed"] is False
    assert hub.put(path, json={"allowed": False}).json()["changed"] is True
    assert orca["lldap"].logins["mo"]["groups"] == {"hub-users"}
    assert f"{orca['member_name']}: Bambu Studio on" in _events(orca["member_name"])
    # An app the hub doesn't run, or OrcaSlicer (its own switch), is not given here.
    assert hub.put(f"/api/platform/users/{orca['member']}/apps/nope", json={"allowed": True}).status_code == 404
    assert hub.put(f"/api/platform/users/{orca['member']}/apps/orcaslicer", json={"allowed": True}).status_code == 404
    # Only the hub's owner.
    assert as_user(orca["member"]).get("/api/platform/apps").status_code == 403
