"""Logins in LLDAP, for a hub that signs people in through Authelia (the OpenPrintHQ installer's default).

An invite to someone with no login makes one in LLDAP and a one-time link to the hub's page where they choose its
password; someone who has a login joins the hub-users group. The installer's key, and only it, may ask for such a
link for the hub's owner. LLDAP is a fake that keeps users, groups and passwords the way LLDAP does.
"""

import re

import pytest
from fastapi.testclient import TestClient

from tests.test_setup_api import TOKEN, _set, new_install  # noqa: F401  (fixture)
from tests.test_teams import as_user, people, real_auth  # noqa: F401  (fixtures)


class FakeLldap:
    def __init__(self):
        self.users = {"existing": {"id": "existing", "email": "known@c.example", "groups": set()}}
        self.passwords = {}
        self.deleted = []

    def user_by_email(self, email):
        for u in self.users.values():
            if u["email"] == email.strip().lower():
                return {"id": u["id"], "email": u["email"]}
        return None

    def create_user(self, email, name):
        uid = re.sub(r"[^a-z0-9]", "-", email.split("@")[0].lower())
        self.users[uid] = {"id": uid, "email": email.strip().lower(), "groups": set()}
        return uid

    def add_to_group(self, uid, group=None):
        self.users[uid]["groups"].add(group or "hub-users")

    def remove_from_group(self, uid, group=None):
        self.users[uid]["groups"].discard(group or "hub-users")

    def delete_user(self, uid):
        self.deleted.append(uid)
        self.users.pop(uid, None)

    def set_password(self, uid, password):
        self.passwords[uid] = password


@pytest.fixture
def lldap(monkeypatch):
    from app.clients import lldap_api
    fake = FakeLldap()
    monkeypatch.setattr(lldap_api, "client", lambda: fake)
    return fake


def test_an_invite_to_someone_without_a_login_makes_one_and_a_password_link(people, real_auth, lldap):
    owner = as_user(people["owner"])
    r = owner.post("/api/team/invites", json={"email": "newbie@c.example", "role": "member"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["account"] == "invited" and "/account/password/" in body["signup_url"]
    assert lldap.users["newbie"]["groups"] == {"hub-users"}
    anyone = TestClient(__import__("app.main", fromlist=["app"]).app)
    path = body["signup_url"].split("://", 1)[1].split("/", 1)[1]
    page = anyone.get("/" + path)
    assert page.status_code == 200 and "Choose your password" in page.text and "newbie" in page.text
    short = anyone.post("/" + path, data={"password": "short", "again": "short"})
    assert short.status_code == 400 and lldap.passwords == {}
    differ = anyone.post("/" + path, data={"password": "a-long-password", "again": "another-long-one"})
    assert differ.status_code == 400
    done = anyone.post("/" + path, data={"password": "a-long-password", "again": "a-long-password"},
                       follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/auth/login"
    assert lldap.passwords == {"newbie": "a-long-password"}
    # It works once.
    assert anyone.get("/" + path).status_code == 410


def test_someone_with_a_login_just_joins_the_hub_users_group(people, real_auth, lldap):
    owner = as_user(people["owner"])
    r = owner.post("/api/team/invites", json={"email": "known@c.example", "role": "viewer"})
    assert r.json()["account"] == "existing" and r.json()["signup_url"] is None
    assert lldap.users["existing"]["groups"] == {"hub-users"}


def test_a_cancelled_invite_removes_the_login_nobody_finished(people, real_auth, lldap):
    owner = as_user(people["owner"])
    r = owner.post("/api/team/invites", json={"email": "never@c.example", "role": "member"})
    invite_id = r.json()["invite"]["id"]
    assert owner.delete(f"/api/team/invites/{invite_id}").status_code == 200
    assert lldap.deleted == ["never"]
    path = r.json()["signup_url"].split("://", 1)[1].split("/", 1)[1]
    assert owner.get("/" + path).status_code == 410


def test_only_the_key_setup_minted_may_ask_for_the_owners_password_link(new_install, lldap):
    from app.main import app
    client = TestClient(app)
    try:
        made = client.post("/api/setup/v1/initialize", json={"farm": "openprinthq", "team_name": "Lab"},
                           headers={"Authorization": f"Bearer {TOKEN}"}).json()
        key = {"Authorization": f"Bearer {made['api_key']}"}
        lldap.users["owner"] = {"id": "owner", "email": "admin@hub.example", "groups": {"hub-admins"}}
        r = client.post("/api/setup/v1/password-link", json={"email": "admin@hub.example"}, headers=key)
        assert r.status_code == 200, r.text
        assert r.json()["user"] == "owner" and r.json()["expires_in_minutes"] == 30
        assert client.post("/api/setup/v1/password-link", json={"email": "nobody@hub.example"},
                           headers=key).status_code == 404
        # Another key with the same scopes may not.
        other = client.post("/api/mobile/v1/keys", json={"name": "other", "scopes": ["admin"], "bind_instance": False}, headers=key)
        assert other.status_code == 200, other.text
        refused = client.post("/api/setup/v1/password-link", json={"email": "admin@hub.example"},
                              headers={"Authorization": f"Bearer {other.json()['key']}"})
        assert refused.status_code == 403
    finally:
        _set(installer_key_id=None)
