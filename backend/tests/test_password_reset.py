"""Forgot your password: the hub's reset page, /account/password, and the link the hub's owner makes from People.

The page looks the login up in LLDAP (a fake here) and, when it exists with an email address and the hub has mail set
up, emails a one-time link to the hub's page where a new password is chosen. The answer on screen is the same for a
login that exists and one that does not. Requests are limited per client and per login, only a hash of a link is
kept, a new link cancels older ones, and the access log never shows a link.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.test_lldap import FakeLldap
from tests.test_teams import as_user, people, real_auth  # noqa: F401  (fixtures)

SENT = "If that account exists, a link is on its way. It works once, for 30 minutes."


class Lldap(FakeLldap):
    bind_user = "hub-service"

    def user(self, uid):
        u = self.users.get(uid)
        return {"id": u["id"], "email": u["email"]} if u else None


@pytest.fixture
def lldap(monkeypatch):
    from app.clients import lldap_api
    fake = Lldap()
    monkeypatch.setattr(lldap_api, "client", lambda: fake)
    return fake


@pytest.fixture
def mail(monkeypatch):
    """Mail set up, and every message the hub sends kept here instead."""
    from app import tasks
    from app.config import settings
    from app.routers import account
    monkeypatch.setattr(settings(), "smtp_host", "smtp.hub.example")
    account._client_hits.clear()
    sent = []
    monkeypatch.setattr(tasks, "send_mail", lambda subject, body, **kw: sent.append({"subject": subject, "text": body, **kw}))
    yield sent
    account._client_hits.clear()


def _client():
    from app.main import app
    return TestClient(app, follow_redirects=False)


def _login(lldap, email=None):
    """A login of its own for each test, so the per-login limit of one test does not reach another."""
    uid = f"maker-{secrets.token_hex(3)}"
    lldap.users[uid] = {"id": uid, "email": email or f"{uid}@shop.example", "groups": {"hub-users"}}
    return uid, lldap.users[uid]["email"]


def _path(link):
    return "/" + link.split("://", 1)[1].split("/", 1)[1]


def _link_in(message):
    line = next(x for x in message["text"].splitlines() if x.startswith("Choose a new password: "))
    return line.split(": ", 1)[1]


def test_the_page_asks_for_a_username_or_email(real_auth, lldap, mail):
    r = _client().get("/account/password")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "Reset your password" in r.text and "Username or email" in r.text and "Send reset link" in r.text
    assert 'action="/account/password"' in r.text and chr(0x2014) not in r.text


def test_the_answer_is_the_same_for_a_login_that_exists_and_one_that_does_not(real_auth, lldap, mail):
    uid, email = _login(lldap)
    c = _client()
    known = c.post("/account/password", data={"login": uid})
    by_email = c.post("/account/password", data={"login": email.upper()})
    unknown = c.post("/account/password", data={"login": "nobody-here"})
    unknown_email = c.post("/account/password", data={"login": "nobody@shop.example"})
    answers = {(r.status_code, r.text) for r in (known, by_email, unknown, unknown_email)}
    assert len(answers) == 1, "the page must not tell which logins exist"
    assert known.status_code == 200 and SENT in known.text
    # Mail went only to the login that exists, at the address LLDAP has, once per request.
    assert [m["to"] for m in mail] == [[email], [email]]
    assert mail[0]["subject"] == "Reset your ArtisanHub password" and "<!doctype html>" in mail[0]["html"]
    assert _link_in(mail[1]) in mail[1]["html"]


def test_a_login_without_an_email_address_gets_nothing(real_auth, lldap, mail):
    uid, _ = _login(lldap)
    lldap.users[uid]["email"] = ""
    r = _client().post("/account/password", data={"login": uid})
    assert r.status_code == 200 and SENT in r.text and mail == []


def test_the_hubs_own_lldap_account_is_never_reset(real_auth, lldap, mail):
    lldap.users["hub-service"] = {"id": "hub-service", "email": "ops@shop.example", "groups": set()}
    r = _client().post("/account/password", data={"login": "hub-service"})
    assert r.status_code == 200 and SENT in r.text and mail == []


def test_the_link_sets_the_password_once_and_a_new_link_cancels_the_old(real_auth, lldap, mail):
    uid, _ = _login(lldap)
    c = _client()
    c.post("/account/password", data={"login": uid})
    c.post("/account/password", data={"login": uid})
    first, second = _path(_link_in(mail[0])), _path(_link_in(mail[1]))
    assert c.get(first).status_code == 410, "an older unused link stops working when a new one is made"
    page = c.get(second)
    assert page.status_code == 200 and uid in page.text
    done = c.post(second, data={"password": "a-long-new-password", "again": "a-long-new-password"})
    assert done.status_code == 303 and lldap.passwords[uid] == "a-long-new-password"
    assert c.get(second).status_code == 410, "a link works once"


def test_the_link_stops_working_after_30_minutes(real_auth, lldap, mail):
    from app.db import session_scope
    from app.models import PasswordLink
    from app.services.teams import _h
    from app.tenant import unscoped
    uid, _ = _login(lldap)
    c = _client()
    c.post("/account/password", data={"login": uid})
    raw = _link_in(mail[0]).rsplit("/", 1)[1]
    with session_scope() as db, unscoped():
        row = db.query(PasswordLink).filter(PasswordLink.token_hash == _h(raw)).one()
        exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        made = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        assert timedelta(minutes=29) < exp - made <= timedelta(minutes=30, seconds=5)
        # Only the hash is kept.
        assert row.token_hash != raw and raw not in {row.token_hash, row.uid, row.email}
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert c.get(f"/account/password/{raw}").status_code == 410


def test_requests_are_limited_per_client(real_auth, lldap, mail):
    c = _client()
    codes = [c.post("/account/password", data={"login": f"someone-{n}"}).status_code for n in range(6)]
    assert codes == [200] * 5 + [429]
    assert "Try again in 15 minutes" in c.post("/account/password", data={"login": "x"}).text


def test_links_are_limited_per_login_without_saying_so(real_auth, lldap, mail, monkeypatch):
    from app.routers import account
    uid, _ = _login(lldap)
    c = _client()
    answers = set()
    for n in range(5):
        account._client_hits.clear()      # a different client each time, as far as the per-client limit goes
        r = c.post("/account/password", data={"login": uid})
        answers.add((r.status_code, r.text))
    assert len(answers) == 1 and len(mail) == 3


def test_with_no_mail_set_up_the_page_says_to_ask_the_owner(real_auth, lldap, monkeypatch):
    from app import tasks
    from app.config import settings
    monkeypatch.setattr(settings(), "smtp_host", "")
    sent = []
    monkeypatch.setattr(tasks, "send_mail", lambda *a, **kw: sent.append(a))
    uid, _ = _login(lldap)
    c = _client()
    page = c.get("/account/password")
    assert page.status_code == 200 and "Ask the hub's owner" in page.text and "<form" not in page.text
    posted = c.post("/account/password", data={"login": uid})
    assert "<form" not in posted.text and sent == []


def test_without_lldap_the_page_says_to_ask_the_owner(real_auth, mail, monkeypatch):
    from app.clients import lldap_api
    monkeypatch.setattr(lldap_api, "client", lambda: None)
    page = _client().get("/account/password")
    assert "Ask the hub's owner" in page.text and "<form" not in page.text


def test_the_access_log_never_shows_a_link():
    from app.routers.account import _NoTokens
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("10.0.0.1:5000", "GET", "/account/password/AbCdEfGhIjKlMnOpQrStUvWxYz012345", "1.1", 200),
                            None)
    _NoTokens().filter(rec)
    line = rec.getMessage()
    assert "AbCdEfGhIjKlMnOpQrStUvWxYz012345" not in line and "/account/password/***" in line


def test_the_hubs_owner_makes_a_link_from_people(people, real_auth, lldap, mail):
    from app.db import session_scope
    from app.models import Membership, User
    from app.tenant import unscoped
    tag = secrets.token_hex(3)
    with session_scope() as db, unscoped():
        boss = User(sub=f"boss-{tag}", email=f"boss-{tag}@shop.example", name="Hub Owner", platform_admin=True)
        db.add(boss)
        db.flush()
        db.add(Membership(team_id=1, user_id=boss.id, role="member"))
        boss_id = boss.id
    lldap.users["outsider"] = {"id": "outsider", "email": people["outsider_email"], "groups": {"hub-users"}}
    r = as_user(boss_id).post(f"/api/account/password-link/{people['outsider']}")
    assert r.status_code == 200, r.text
    assert r.json()["user"] == "outsider" and r.json()["expires_in_minutes"] == 30
    assert _client().get(_path(r.json()["link"])).status_code == 200
    # A team owner is not the hub's owner.
    team_owner = as_user(people["owner"])
    assert team_owner.post(f"/api/account/password-link/{people['outsider']}").status_code == 403


# ---- Change your password, signed in (B35) -------------------------------------------------------------------------

class CheckingLldap(Lldap):
    def __init__(self):
        super().__init__()
        self.current = {}

    def check_password(self, uid, password):
        return self.current.get(uid) == password

    def set_password(self, uid, password):
        super().set_password(uid, password)
        self.current[uid] = password


@pytest.fixture
def lldap_checking(monkeypatch):
    from app.clients import lldap_api
    from app.routers import account
    fake = CheckingLldap()
    monkeypatch.setattr(lldap_api, "client", lambda: fake)
    account._client_hits.clear()
    yield fake
    account._client_hits.clear()


def _with_login(user_id, uid):
    from app.db import session_scope
    from app.models import User
    from app.tenant import unscoped
    with session_scope() as db, unscoped():
        db.get(User, user_id).username = uid


def test_someone_signed_in_changes_their_password_with_the_current_one(people, real_auth, lldap_checking):
    uid = f"maker-{secrets.token_hex(3)}"
    lldap_checking.users[uid] = {"id": uid, "email": f"{uid}@shop.example", "groups": {"hub-users"}}
    lldap_checking.current[uid] = "old-password-1"
    _with_login(people["member"], uid)
    c = as_user(people["member"])
    page = c.get("/account/password/change")
    assert page.status_code == 200 and "Change your password" in page.text and uid in page.text
    bad = c.post("/account/password/change", data={"current": "wrong-one", "password": "new-password-22",
                                                   "again": "new-password-22"})
    assert bad.status_code == 400 and "not right" in bad.text
    assert lldap_checking.current[uid] == "old-password-1"
    short = c.post("/account/password/change", data={"current": "old-password-1", "password": "short", "again": "short"})
    assert short.status_code == 400
    differ = c.post("/account/password/change", data={"current": "old-password-1", "password": "new-password-22",
                                                      "again": "new-password-23"})
    assert differ.status_code == 400 and "not the same" in differ.text
    ok = c.post("/account/password/change", data={"current": "old-password-1", "password": "new-password-22",
                                                  "again": "new-password-22"})
    assert ok.status_code == 200 and "Password changed" in ok.text
    assert lldap_checking.current[uid] == "new-password-22"


def test_changing_the_password_needs_a_sign_in_and_is_limited_per_login(people, real_auth, lldap_checking):
    r = _client().get("/account/password/change")
    assert r.status_code == 303 and r.headers["location"] == "/auth/login?next=/account/password/change"
    uid = f"maker-{secrets.token_hex(3)}"
    lldap_checking.users[uid] = {"id": uid, "email": f"{uid}@shop.example", "groups": {"hub-users"}}
    lldap_checking.current[uid] = "old-password-1"
    _with_login(people["viewer"], uid)
    c = as_user(people["viewer"])
    codes = [c.post("/account/password/change", data={"current": f"guess-{i}", "password": "new-password-22",
                                                      "again": "new-password-22"}).status_code for i in range(6)]
    assert codes[:5] == [400] * 5 and codes[5] == 429


def test_a_person_without_a_login_the_hub_manages_is_told_so(people, real_auth, monkeypatch):
    from app.clients import lldap_api
    monkeypatch.setattr(lldap_api, "client", lambda: None)
    r = as_user(people["owner"]).get("/account/password/change")
    assert r.status_code == 409 and "does not manage your login" in r.text
