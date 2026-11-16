"""The alerts inbox: every alert recorded once per problem, closed when the problem goes away, dismissed or fixed
from any device; each person's choices of what reaches them; the actions the guided fixes use."""
import secrets as _s

import pytest
from fastapi.testclient import TestClient


def _client():
    from app.main import app
    return TestClient(app, follow_redirects=False)


def _person(role="owner"):
    from app.db import session_scope
    from app.models import Membership, User
    from app.tenant import unscoped
    tag = _s.token_hex(3)
    with session_scope() as db, unscoped():
        u = User(sub=f"a-{tag}", email=f"a-{tag}@x.example", name=f"A{tag}")
        db.add(u)
        db.flush()
        db.add(Membership(team_id=1, user_id=u.id, role=role))
        return u.id


def _key(uid):
    from app.auth import new_api_key
    from app.db import session_scope
    with session_scope() as db:
        return new_api_key(db, "phone", ["read", "write", "admin"], team_id=1, user_id=uid)


def H(raw):
    return {"Authorization": f"Bearer {raw}"}


@pytest.fixture
def queued(monkeypatch):
    """notify's channel step, recorded instead of sent."""
    from app.services import alerts
    out = []
    monkeypatch.setattr(alerts, "enqueue", lambda db, kind, ref=None, payload=None, **kw: out.append(payload))
    monkeypatch.setattr(alerts, "configured", lambda: ["push"])
    monkeypatch.setattr(alerts, "quiet_now", lambda r, at=None: False)
    return out


def test_every_alert_kind_has_a_choice():
    from app.services import alert_inbox, alerts
    assert set(alert_inbox.kinds()) == set(alerts.EVENTS)
    assert len(alert_inbox.kinds()) == len(set(alert_inbox.kinds()))


def test_recorded_once_per_problem_and_the_link_opens_it(queued):
    from app.db import session_scope
    from app.models import Alert
    from app.services import alerts
    ref = f"alert:wf:{_s.token_hex(3)}"
    with session_scope() as db:
        alerts.notify(db, "waiting_filament", "Filament needs mapping: Planter", "PETG not loaded",
                      link="https://hub/farm/jobs/9/filament", ref=ref, subject={"job": "9"})
        alerts.notify(db, "waiting_filament", "Filament needs mapping: Planter", "still PETG not loaded",
                      link="https://hub/farm/jobs/9/filament", ref=ref, subject={"job": "9"})
        rows = db.query(Alert).filter(Alert.ref == ref).all()
        assert len(rows) == 1 and rows[0].count == 2 and rows[0].message == "still PETG not loaded"
        aid = rows[0].id
        assert rows[0].link == "https://hub/farm/jobs/9/filament"          # where "Go to it" leads
    assert queued[-1]["link"].endswith(f"/alerts/{aid}") and queued[-1]["alert_id"] == aid


def test_closes_when_the_job_stops_waiting(queued):
    from app.db import session_scope
    from app.models import Alert, FarmJob
    from app.services import farm
    ext = f"7{_s.randbelow(10**6)}"
    with session_scope() as db:
        fj = FarmJob(external_id=ext, name="Gear housing", status="waiting_filament",
                     waiting_reason="Filament doesn't match: H2D: filament 2 PETG #FFFFFF")
        db.add(fj)
        db.flush()
        farm.transition(db, fj, "queued", live=True)
        a = db.query(Alert).filter(Alert.ref == f"alert:wf:{ext}").one()
        assert a.state == "open" and a.subject["job"] == ext
        fj.status = "printing"
        farm.transition(db, fj, "waiting_filament", live=True)
        db.flush()
        assert a.state == "resolved" and "no longer waiting" in a.fix


def test_open_dismiss_and_fix_from_the_api(queued):
    from app.db import session_scope
    from app.services import alerts
    raw = _key(_person())
    c = _client()
    with session_scope() as db:
        alerts.notify(db, "alarm", "H2D alarm: nozzle clog", "0300-1A00", ref=f"x:{_s.token_hex(3)}")
        alerts.notify(db, "maintenance_due", "H2D: grease rods", "Due", ref=f"y:{_s.token_hex(3)}")
    lst = c.get("/api/mobile/v1/alerts", headers=H(raw)).json()
    ids = [a["id"] for a in lst["alerts"][:2]]
    assert lst["open"] >= 2 and lst["urgent"] >= 1
    one = c.get(f"/api/mobile/v1/alerts/{ids[0]}", headers=H(raw)).json()
    assert one["state"] == "open" and "context" in one
    d = c.post(f"/api/mobile/v1/alerts/{ids[0]}/dismiss", headers=H(raw)).json()
    assert d["state"] == "dismissed" and d["closed_by"]
    f = c.post(f"/api/alerts/{ids[1]}/fixed", headers=H(raw), json={"how": "greased"}).json()
    assert f["state"] == "fixed" and f["fix"] == "greased"
    closed = [a["id"] for a in c.get("/api/alerts?state=closed", headers=H(raw)).json()["alerts"]]
    assert set(ids) <= set(closed)
    assert c.get("/api/stats", headers=H(raw)).json()["alerts_open"] >= 0


def test_ready_fix_context_lists_the_check_and_the_jobs(queued):
    from app.db import session_scope
    from app.models import FarmJob, PlateCheck
    from app.services import alerts
    raw = _key(_person())
    name = f"P{_s.token_hex(2)}"
    with session_scope() as db:
        db.add(FarmJob(external_id=f"r{name}", status="queued", name="Bracket", printer_name=name))
        db.add(PlateCheck(printer_name=name, job_name="Planter", state="waiting", job_outcome="completed"))
        alerts.notify(db, "waiting_ready", f"{name}: 1 job waiting", "clear it", ref=f"alert:ready:{name}",
                      subject={"printer": name})
        from app.models import Alert
        aid = db.query(Alert).filter(Alert.ref == f"alert:ready:{name}").one().id
    ctx = _client().get(f"/api/alerts/{aid}", headers=H(raw)).json()["context"]
    assert ctx["kind"] == "ready" and ctx["printer"] == name
    assert ctx["check"]["job_name"] == "Planter" and [j["name"] for j in ctx["waiting"]] == ["Bracket"]


def test_choices_keep_a_kind_off_this_persons_phone(monkeypatch):
    import httpx
    from app.db import session_scope
    from app.kv import set_setting
    from app.services import push
    raw_uid = _person()
    raw = _key(raw_uid)
    c = _client()
    ch = c.get("/api/mobile/v1/alerts/choices", headers=H(raw)).json()
    assert [g["id"] for g in ch["groups"]] == ["problems", "needs_you", "prints", "orders"]
    out = c.put("/api/mobile/v1/alerts/choices", headers=H(raw), json={"on": {"print_started": False}}).json()
    started = next(k for g in out["groups"] for k in g["kinds"] if k["kind"] == "print_started")
    assert started["on"] is False
    sent = []
    monkeypatch.setattr(push.httpx, "post", lambda url, json=None, **kw: sent.append(json) or httpx.Response(
        200, json={"ok": True}, request=httpx.Request("POST", url)))
    monkeypatch.setattr(push.httpx, "get", lambda url, **kw: httpx.Response(200, json={}, request=httpx.Request("GET", url)))
    with session_scope() as db:
        set_setting(db, "push_relay_url", "https://relay.example.test")
        from app.models import User
        u = db.get(User, raw_uid)
        push.set_choice(db, u, "on")
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        pub = push.b64e(X25519PrivateKey.generate().public_key().public_bytes(*push.RAW))
        tok = _s.token_hex(32)
        push.register(db, raw_uid, None, {"platform": "apns", "token": tok, "public_key": pub})
    with session_scope() as db:
        push.deliver(db, {"kind": "print_started", "title": "H2D started Planter", "message": ""})
        assert tok not in [b["token"] for b in sent]          # turned off for this person
        push.deliver(db, {"kind": "alarm", "title": "H2D alarm", "message": "", "alert_id": 5})
        assert tok in [b["token"] for b in sent]              # still on
    with session_scope() as db:
        set_setting(db, "push_relay_url", None)
        push.set_choice(db, db.get(User, raw_uid), None)      # forget this phone again


def test_move_and_slot_actions_reach_the_farm(monkeypatch):
    from app.services import farm, printer_access
    calls = []

    class Farm:
        def printers(self):
            return {"printers": [{"id": 3, "name": "H2C"}]}

        def move_job(self, job_id, printer_id):
            calls.append(("move", job_id, printer_id))

        def configure_slot(self, *a):
            calls.append(("slot", *a))

    monkeypatch.setattr(farm, "_client", lambda: Farm())
    monkeypatch.setattr(printer_access, "may_use", lambda db, p, name: True)
    raw = _key(_person())
    c = _client()
    assert c.post("/api/mobile/v1/farm/jobs/42/move", headers=H(raw), json={"printer_id": 3}).json()["printer"] == "H2C"
    assert c.post("/api/mobile/v1/farm/jobs/42/move", headers=H(raw), json={"printer_id": 9}).status_code == 404
    assert c.post("/api/farm/printers/H2C/slots/5/filament", headers=H(raw), json={"type": "petg", "color": "#ffffff"}).json()["ok"]
    assert c.post("/api/farm/printers/H2C/slots/254/filament", headers=H(raw), json={"type": "PLA", "color": "000000"}).status_code == 400
    assert calls == [("move", 42, 3), ("slot", "H2C", 1, 1, "PETG", "FFFFFF", 220, 260)]


def test_order_filament_puts_it_on_the_shopping_list(queued):
    from app.db import session_scope
    from app.kv import set_setting
    from app.models import Alert
    from app.services import alerts
    raw = _key(_person())
    c = _client()
    with session_scope() as db:
        set_setting(db, "filament_wanted", None)
        alerts.notify(db, "waiting_filament", "Filament needs mapping: Planter", "", ref=f"w:{_s.token_hex(3)}",
                      subject={"job": "9"})
        aid = db.query(Alert).order_by(Alert.id.desc()).first().id
    r = c.post(f"/api/alerts/{aid}/want-filament", headers=H(raw), json={"material": "petg", "color": "#FFFFFF", "grams": 18}).json()
    assert r["wanted"][-1]["material"] == "PETG" and r["wanted"][-1]["alert_id"] == aid
    assert c.get(f"/api/alerts/{aid}", headers=H(raw)).json()["subject"]["ordered"] == ["PETG #FFFFFF"]
    assert c.delete("/api/alerts/filament-wanted/0", headers=H(raw)).json()["wanted"] == []
