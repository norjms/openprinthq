"""First-run setup and the explicit print farm (invariants 1, 3 and 4).

The shared test database models an installation that predates the explicit choice ("legacy"). Each test here
puts the installation row into the state it needs and the fixture puts it back.
"""

import hashlib
import re

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-setup-token-0123456789"


def _row():
    from app.db import SessionLocal
    from app.models import Installation
    s = SessionLocal()
    return s, s.get(Installation, 1)


def _set(**fields):
    from app.services import installation
    s, row = _row()
    try:
        for k, v in fields.items():
            setattr(row, k, v)
        s.commit()
    finally:
        s.close()
    installation.forget()


@pytest.fixture
def new_install():
    """The installation as a brand-new hub sees it: no farm, setup not done, a generated setup token."""
    s, row = _row()
    saved = {k: getattr(row, k) for k in ("farm", "farm_source", "setup_completed_at", "setup_token_hash", "crafts")}
    s.close()
    _set(farm=None, farm_source=None, setup_completed_at=None, setup_token_hash=hashlib.sha256(TOKEN.encode()).hexdigest(), crafts=None)
    yield
    _set(**saved)


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_status_before_setup(new_install, client):
    body = client.get("/api/setup/v1/status").json()
    assert body == {"api_version": 1, "setup_complete": False, "farm": None, "crafts": ["3d_printing"]}


def test_a_new_install_has_no_farm_until_setup_chooses_one(new_install):
    from app.clients import farm_connected, farm_kind
    from app.services.installation import FarmNotChosen, connect_allowed
    with pytest.raises(FarmNotChosen):
        farm_kind()
    assert farm_connected() is False
    with pytest.raises(ValueError, match="Finish first-run setup at /setup first"):
        connect_allowed("openprinthq", existing=False)


def test_setup_refuses_a_missing_or_wrong_token(new_install, client):
    body = {"farm": "openprinthq", "team_name": "Lab workshop"}
    assert client.post("/api/setup/v1/initialize", json=body).status_code == 401
    wrong = client.post("/api/setup/v1/initialize", json=body, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    assert client.get("/api/setup/v1/status").json()["setup_complete"] is False


def test_setup_rejects_an_unknown_farm(new_install, client):
    r = client.post("/api/setup/v1/initialize", json={"farm": "both", "team_name": "Lab"},
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 422


def test_setup_chooses_the_farm_names_the_team_and_mints_a_key_once(new_install, client):
    from app.clients import farm_kind
    from app.db import SessionLocal
    from app.models import ApiKey, Team
    from app.tenant import unscoped
    r = client.post("/api/setup/v1/initialize", json={"farm": "openprinthq", "team_name": "Lab workshop"},
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["farm"] == "openprinthq"
    assert body["team"] == {"id": 1, "name": "Lab workshop", "slug": "lab-workshop"}
    assert re.fullmatch(r"ahk_[0-9a-f]{8}_\S+", body["api_key"])

    assert farm_kind() == "openprinthq"
    assert client.get("/api/setup/v1/status").json() == {"api_version": 1, "setup_complete": True, "farm": "openprinthq",
                                                         "crafts": ["3d_printing"]}
    s = SessionLocal()
    try:
        with unscoped():
            assert s.get(Team, 1).name == "Lab workshop"
            key = [k for k in s.query(ApiKey).all() if body["api_key"].split("_")[1] == k.prefix][0]
        assert key.team_id == 1 and set(key.scopes) == {"read", "write", "admin"}
    finally:
        s.close()

    again = client.post("/api/setup/v1/initialize", json={"farm": "openprinthq", "team_name": "Other"},
                        headers={"Authorization": f"Bearer {TOKEN}"})
    assert again.status_code == 409
    assert farm_kind() == "openprinthq"


def test_setup_refuses_a_farm_the_hub_no_longer_supports(new_install, client):
    r = client.post("/api/setup/v1/initialize", json={"farm": "legacy_farm", "team_name": "Lab"},
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 422
    assert client.get("/api/setup/v1/status").json()["setup_complete"] is False


def test_after_setup_a_second_farm_connection_is_refused(new_install):
    from app.clients import farm_kind
    from app.services.installation import connect_allowed
    _set(farm="openprinthq", farm_source="setup")
    assert farm_kind() == "openprinthq"
    connect_allowed("openprinthq", existing=True)      # the existing row can still be edited
    connect_allowed("spoolman", existing=False)


def test_an_installation_on_an_unsupported_farm_refuses_to_start(new_install):
    from app.db import SessionLocal
    from app.services.installation import UnsupportedFarm, bootstrap
    _set(farm="legacy_farm", farm_source="setup")
    s = SessionLocal()
    try:
        with pytest.raises(UnsupportedFarm, match="no longer supports"):
            bootstrap(s, fresh=False)
    finally:
        s.rollback()
        s.close()


@pytest.mark.parametrize(
    ("connected", "fresh", "expected"),
    [
        (True, False, ("openprinthq", "migrated")),
        (True, True, ("openprinthq", "migrated")),
        (False, False, (None, "legacy")),
        (False, True, (None, None)),
    ],
)
def test_an_existing_installation_is_migrated_only_when_connected(connected, fresh, expected):
    from app.services.installation import decide
    assert decide(connected, fresh) == expected


def test_healthz_reports_database_and_migrations(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["database"] == "ok" and body["migrations"] == "done"
    assert body["schema_version"] >= 20


# Defaults may name only this machine, a compose service (a single-label host) or a public API the hub
# calls. Anything else is one deployment's address leaking into every new install.
PUBLIC_API_HOSTS = {"api.upcitemdb.com"}
PRIVATE_IP = re.compile(r"\b(10|127|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d+\.\d+(\.\d+)?\b")


def _host_ok(host: str) -> bool:
    return host in ("localhost", "127.0.0.1") or "." not in host or host in PUBLIC_API_HOSTS


def test_no_setting_defaults_to_an_address_of_the_current_deployment():
    from app.config import Settings
    leaks = {}
    for name, f in Settings.model_fields.items():
        v = f.default
        if not isinstance(v, str):
            continue
        hosts = re.findall(r"[a-z][a-z0-9+.-]*://(?:[^@/\s]*@)?([^:/\s]+)", v)
        mail = re.findall(r"<[^@>\s]+@([^>\s]+)>", v)
        bad_ip = PRIVATE_IP.search(v) and not v.startswith("http://127.0.0.1")
        if bad_ip or any(not _host_ok(h) for h in hosts) or any(d != "localhost" for d in mail):
            leaks[name] = v
    assert leaks == {}


def test_a_fresh_hub_seeds_no_connections_from_defaults(monkeypatch):
    from app.config import Settings
    from app.services import connections
    monkeypatch.setattr(connections, "settings", lambda: Settings(_env_file=None))
    for name in list(Settings.model_fields):
        monkeypatch.delenv(f"HUB_{name.upper()}", raising=False)
    assert connections.env_seed() == {}


# Reachability checks (docs/setup-api.md)


def _admin_key(scopes=("read", "write", "admin")):
    from app.auth import new_api_key
    from app.db import session_scope
    from app.tenant import use_team
    with session_scope() as db, use_team(1):
        return new_api_key(db, "reach test", list(scopes), team_id=1)


def _auth(raw):
    return {"Authorization": f"Bearer {raw}"}


def test_a_reach_check_answers_only_its_own_token_and_records_that_it_arrived(client):
    from app.routers import setup
    setup._tries.clear()
    key = _admin_key()
    made = client.post("/api/setup/v1/reach", headers=_auth(key)).json()
    token = made["token"]
    assert made["path"] == f"/api/setup/v1/reach/{token}"
    assert client.get(f"/api/setup/v1/reach/{token}/status", headers=_auth(key)).json() == {
        "seen": False, "seen_at": None, "via_proxy": None}
    other = "x" * 32
    assert client.get(f"/api/setup/v1/reach/{other}").status_code == 404
    hit = client.get(made["path"], headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.7"})
    assert hit.status_code == 200 and hit.json()["ok"] is True
    status = client.get(f"/api/setup/v1/reach/{token}/status", headers=_auth(key)).json()
    assert status["seen"] is True and status["via_proxy"] is True and status["seen_at"]


def test_reach_tokens_need_an_admin_key_and_expire(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.config import settings
    from app.db import SessionLocal
    from app.models import ReachCheck
    from app.routers import setup
    setup._tries.clear()
    monkeypatch.setattr(settings(), "dev_auth_bypass", False)
    assert client.post("/api/setup/v1/reach").status_code == 401
    assert client.post("/api/setup/v1/reach", headers=_auth(_admin_key(("read",)))).status_code == 403
    key = _admin_key()
    token = client.post("/api/setup/v1/reach", headers=_auth(key)).json()["token"]
    s = SessionLocal()
    row = s.query(ReachCheck).filter(ReachCheck.token_hash == setup._reach_hash(token)).one()
    assert token not in str(row.token_hash)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    s.commit()
    s.close()
    assert client.get(f"/api/setup/v1/reach/{token}").status_code == 404
    assert client.get(f"/api/setup/v1/reach/{token}/status", headers=_auth(key)).status_code == 404
    # A malformed token is refused before any lookup.
    assert client.get("/api/setup/v1/reach/short").status_code == 422
