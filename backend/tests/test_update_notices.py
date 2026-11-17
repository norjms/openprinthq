"""The installer's update notices (Q7): an alert in the inbox while a new version waits on the installer's page."""

import pytest
from fastapi.testclient import TestClient


def _key(name, scopes=("read", "write", "admin")):
    from sqlalchemy import select

    from app.auth import new_api_key
    from app.db import session_scope
    from app.models import ApiKey
    from app.tenant import unscoped, use_team
    with session_scope() as db, use_team(1):
        raw = new_api_key(db, name, list(scopes), team_id=1)
    with session_scope() as db, unscoped():
        kid = db.execute(select(ApiKey.id).where(ApiKey.name == name).order_by(ApiKey.id.desc())).scalar()
    return raw, kid


@pytest.fixture
def installer():
    from app.db import session_scope
    from app.models import Installation
    from app.services import installation
    raw, kid = _key("update notices installer")
    with session_scope() as db:
        row = db.get(Installation, 1)
        saved, row.installer_key_id = row.installer_key_id, kid
    installation.forget()
    yield {"Authorization": f"Bearer {raw}"}
    with session_scope() as db:
        db.get(Installation, 1).installer_key_id = saved
    installation.forget()


def _alerts(ref):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Alert
    from app.tenant import use_team
    with session_scope() as db, use_team(1):
        return [(a.id, a.kind, a.state, a.title, (a.subject or {}).get("version"), a.count)
                for a in db.execute(select(Alert).where(Alert.ref == ref).order_by(Alert.id)).scalars()]


def _notice(**kw):
    body = {"part": "orcaslicer", "version": "2.5.2-ls52", "title": "OrcaSlicer 2.5.2 is ready for the desktop",
            "message": "Install it from the installer's update page."}
    body.update(kw)
    return body


def test_a_ready_update_is_one_open_alert_until_it_is_installed(installer):
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    ref = "alert:update:orcaslicer"
    r = client.post("/api/setup/v1/updates", json=_notice(), headers=installer)
    assert r.status_code == 200, r.text
    assert r.json()["channels"] == ["push"]      # an update, not a problem: the phone app only
    first = _alerts(ref)
    assert [(k, s, v) for _, k, s, _, v, _ in first] == [("update_ready", "open", "2.5.2-ls52")]
    # A newer version updates the same alert.
    client.post("/api/setup/v1/updates", json=_notice(version="2.5.3-ls53", title="OrcaSlicer 2.5.3"), headers=installer)
    again = _alerts(ref)
    assert len(again) == 1 and again[0][3] == "OrcaSlicer 2.5.3" and again[0][4] == "2.5.3-ls53"
    # Installed: closed.
    assert client.post("/api/setup/v1/updates", json=_notice(version="2.5.3-ls53", ready=False),
                       headers=installer).status_code == 200
    assert _alerts(ref)[0][2] == "resolved"


def test_only_the_installers_key_may_announce(installer, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), "dev_auth_bypass", False)
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    other, _ = _key("another admin key")
    r = client.post("/api/setup/v1/updates", json=_notice(part="other-part"), headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 403
    assert client.post("/api/setup/v1/updates", json=_notice(part="Bad Part!"), headers=installer).status_code == 422
    assert _alerts("alert:update:other-part") == []


def test_the_guided_fix_points_at_the_installer_and_can_be_put_away(installer):
    from app.db import session_scope
    from app.models import Alert
    from app.services import alert_fixes
    from app.tenant import use_team
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    client.post("/api/setup/v1/updates", json=_notice(part="desk"), headers=installer)
    aid = [a for a in _alerts("alert:update:desk") if a[2] == "open"][0][0]
    with session_scope() as db, use_team(1):
        a = db.get(Alert, aid)
        d = alert_fixes.describe(db, a)
        paths = {p["id"]: p for p in d["paths"]}
        assert paths["install"]["open"]["web"] == "/manage"
        assert ["Version", "2.5.2-ls52"] in d["summary"]
        out = alert_fixes.run(db, a, "later", {}, "tester")
        assert out["closed"] is True and a.state == "fixed"
