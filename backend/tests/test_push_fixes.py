"""Guided fixes the hub describes (services/alert_fixes.py): each kind's ways to fix it, carried out through
POST /alerts/{id}/do/{way}, and the alerts closing on their own when the problem goes away elsewhere."""
import secrets as _s
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


def _client():
    from app.main import app
    return TestClient(app, follow_redirects=False)


def _key():
    from app.auth import new_api_key
    from app.db import session_scope
    from app.models import Membership, User
    from app.tenant import unscoped
    tag = _s.token_hex(3)
    with session_scope() as db, unscoped():
        u = User(sub=f"g-{tag}", email=f"g-{tag}@x.example", name=f"G{tag}")
        db.add(u)
        db.flush()
        db.add(Membership(team_id=1, user_id=u.id, role="owner"))
        uid = u.id
    with session_scope() as db:
        return {"Authorization": "Bearer " + new_api_key(db, "phone", ["read", "write", "admin"], team_id=1, user_id=uid)}


class Farm:
    """The farm, recorded."""

    def __init__(self, name, state="idle", online=True):
        self.name, self.calls = name, []
        self.p = {"id": 1, "name": name, "online": online, "busy": state in ("printing", "pause"),
                  "ready": state == "idle", "state": state if online else "offline"}

    def printer(self, name):
        return dict(self.p)

    def printers(self):
        return {"printers": [dict(self.p), {"id": 2, "name": "Other", "online": True, "busy": False, "ready": True}]}

    def command(self, name, command, args=None, confirm=False):
        self.calls.append(("command", name, command))

    def cancel_job(self, job_id):
        self.calls.append(("cancel", job_id))

    def alarms(self, state="active", printer=None, **kw):
        return [{"id": 77, "code": "0300-1A00-0002-0001", "printer_name": printer}]

    def dismiss_alarm(self, aid, by=None):
        self.calls.append(("dismiss", aid))

    def suppress_alarm(self, aid, by=None):
        self.calls.append(("suppress", aid))

    def ack_printer_alarms(self, name, by=None):
        self.calls.append(("ack", name))

    def set_down(self, name, down, reason=""):
        self.calls.append(("down", name, down))

    def move_job(self, job_id, printer_id):
        self.calls.append(("move", job_id, printer_id))

    def set_priority(self, job_id, priority):
        self.calls.append(("priority", job_id, priority))


@pytest.fixture
def setup(monkeypatch):
    from app.services import alerts, farm, printer_access
    monkeypatch.setattr(alerts, "enqueue", lambda *a, **kw: None)
    monkeypatch.setattr(alerts, "configured", lambda: ["push"])
    monkeypatch.setattr(printer_access, "may_use", lambda db, p, name: True)
    monkeypatch.setattr(farm, "stagger", lambda db: timedelta(0))

    def use(f):
        monkeypatch.setattr(farm, "_client", lambda: f)
        return f
    return use


def _alert(kind, title, ref, subject, message=""):
    from app.db import session_scope
    from app.models import Alert
    from app.services import alerts
    with session_scope() as db:
        alerts.notify(db, kind, title, message, ref=ref, subject=subject)
        return db.query(Alert).filter(Alert.ref == ref).order_by(Alert.id.desc()).first().id


def _paths(c, h, aid):
    a = c.get(f"/api/alerts/{aid}", headers=h).json()
    assert a["fixable"] == "guided" and a["context"]["kind"] == "guided"
    return a, {p["id"]: p for p in a["context"]["paths"]}


def test_every_guided_kind_describes_its_fixes(setup):
    from app.services import alert_fixes, alert_inbox
    assert set(alert_fixes.KINDS) == {k for k, v in alert_inbox.FIXES.items() if v == "guided"}
    setup(Farm("H2D"))
    h, c = _key(), _client()
    for kind in alert_fixes.KINDS:
        aid = _alert(kind, f"test {kind}", f"t:{kind}:{_s.token_hex(3)}", {"printer": "H2D"})
        a, paths = _paths(c, h, aid)
        assert a["context"]["paths"] or a["context"]["note"], kind
        for p in paths.values():
            assert p["title"] and p["button"] and p["icon"], (kind, p)


def test_plate_done_pass_and_fail_with_reprint(setup):
    from app.db import session_scope
    from app.models import Alert, PlateCheck
    f = setup(Farm("H2D"))
    h, c = _key(), _client()
    with session_scope() as db:
        pc = PlateCheck(printer_name="H2D", job_name="Planter", state="waiting", job_outcome="completed")
        db.add(pc)
        db.flush()
        cid = pc.id
    aid = _alert("print_done", "H2D finished Planter", f"alert:check:{cid}", {"printer": "H2D", "check": cid})
    a, paths = _paths(c, h, aid)
    assert set(paths) == {"pass", "fail"} and paths["fail"]["fields"][0]["type"] == "choice"
    r = c.post(f"/api/alerts/{aid}/do/pass", headers=h, json={}).json()
    assert r["closed"] and r["alert"]["state"] == "fixed" and r["alert"]["fix"] == "plate check passed"
    with session_scope() as db:
        assert db.get(PlateCheck, cid).state == "passed"
    assert ("command", "H2D", "set_ready") in f.calls

    with session_scope() as db:
        pc = PlateCheck(printer_name="H2D", job_name="Bracket", state="waiting", job_outcome="failed")
        db.add(pc)
        db.flush()
        cid = pc.id
    aid = _alert("print_failed", "H2D: print failed", f"alert:check:{cid}", {"printer": "H2D", "check": cid})
    assert c.post(f"/api/mobile/v1/alerts/{aid}/do/drop", headers=h, json={"values": {"reason": "nope"}}).status_code == 400
    r = c.post(f"/api/mobile/v1/alerts/{aid}/do/drop", headers=h, json={"values": {"reason": "layer_shift"}}).json()
    assert r["closed"] and r["alert"]["fix"] == "Layer shift"
    with session_scope() as db:
        assert db.get(PlateCheck, cid).reason == "layer_shift"
        assert db.get(Alert, aid).closed_by


def test_a_check_done_elsewhere_closes_its_alert(setup):
    from app.db import session_scope
    from app.models import Alert, PlateCheck
    from app.services import farm
    setup(Farm("H2D"))
    with session_scope() as db:
        pc = PlateCheck(printer_name="H2D", job_name="Planter", state="waiting", job_outcome="completed")
        db.add(pc)
        db.flush()
        cid = pc.id
    aid = _alert("print_done", "H2D finished Planter", f"alert:check:{cid}", {"printer": "H2D", "check": cid})
    with session_scope() as db:
        farm.skip_check(db, db.get(PlateCheck, cid), "someone")
    with session_scope() as db:
        assert db.get(Alert, aid).state == "resolved"


def test_alarm_resume_clear_and_mute(setup):
    from app.db import session_scope
    from app.models import Alert
    from app.services import farm
    f = setup(Farm("H2C", state="pause"))
    h, c = _key(), _client()
    sub = {"printer": "H2C", "code": "0300-1A00-0002-0001", "message": "The nozzle is clogged"}
    aid = _alert("alarm", "H2C alarm", "alert:alarm:H2C:03001A0000020001", sub)
    a, paths = _paths(c, h, aid)
    assert paths["resume"]["off"] is None and paths["task"]["open"]["app"] == "farm:maintenance"
    assert ["What it means", "The nozzle is clogged"] in a["context"]["summary"]
    assert c.post(f"/api/alerts/{aid}/do/task", headers=h, json={}).status_code == 400     # a link, not an action
    r = c.post(f"/api/alerts/{aid}/do/resume", headers=h, json={}).json()
    assert r["closed"] and ("command", "H2C", "resume") in f.calls and ("dismiss", 77) in f.calls

    aid = _alert("alarm", "H2C alarm", "alert:alarm:H2C:03001A0000020001", sub)
    r = c.post(f"/api/alerts/{aid}/do/mute", headers=h, json={}).json()
    assert r["closed"] and ("suppress", 77) in f.calls

    aid = _alert("alarm", "H2C alarm", "alert:alarm:H2C:03001A0000020001", sub)
    with session_scope() as db:
        farm.ingest_event(db, "alarm.cleared", {"printer_name": "H2C", "code": "HMS_0300_1A00_0002_0001"})
    with session_scope() as db:
        assert db.get(Alert, aid).state == "resolved"


def test_failure_pause_keeps_it_open_then_fine_resumes(setup):
    f = setup(Farm("MK4S", state="printing"))
    h, c = _key(), _client()
    aid = _alert("failure_detected", "MK4S print failure detected (91%)", f"alert:failure_detected:MK4S",
                 {"printer": "MK4S", "confidence": 0.91})
    a, paths = _paths(c, h, aid)
    assert ["How sure", "91%"] in a["context"]["summary"] and paths["stop"]["off"]   # no job known to stop
    r = c.post(f"/api/alerts/{aid}/do/pause", headers=h, json={}).json()
    assert not r["closed"] and r["alert"]["state"] == "open" and ("command", "MK4S", "pause") in f.calls
    f.p["state"] = "pause"
    r = c.post(f"/api/alerts/{aid}/do/fine", headers=h, json={}).json()
    assert r["closed"] and r["alert"]["fix"] == "a false alarm; resumed"


def test_offline_check_again_and_back_online(setup):
    from app.db import session_scope
    from app.models import Alert
    from app.services import farm
    f = setup(Farm("MK3S", online=False))
    h, c = _key(), _client()
    aid = _alert("printer_offline", "MK3S went offline", "alert:printer_offline:MK3S", {"printer": "MK3S"})
    r = c.post(f"/api/alerts/{aid}/do/back", headers=h, json={})
    assert r.status_code == 400 and "still offline" in r.json()["detail"]
    r = c.post(f"/api/alerts/{aid}/do/down", headers=h, json={"values": {"reason": "Moving it"}}).json()
    assert r["closed"] and ("down", "MK3S", True) in f.calls
    aid = _alert("printer_offline", "MK3S went offline", "alert:printer_offline:MK3S", {"printer": "MK3S"})
    f.p["online"] = True
    with session_scope() as db:
        assert farm.offline_resolve(db) == ["MK3S"]
    with session_scope() as db:
        assert db.get(Alert, aid).state == "resolved"


def test_maintenance_out_of_service_then_done(setup):
    from app.db import session_scope
    from app.models import MaintTask
    f = setup(Farm("H2D"))
    h, c = _key(), _client()
    with session_scope() as db:
        t = MaintTask(printer_name="H2D", title="Clean the carbon rods", source="plan", state="open")
        db.add(t)
        db.flush()
        tid = t.id
    aid = _alert("maintenance_due", "H2D: Clean the carbon rods", f"alert:maint:{tid}", {"task": tid, "printer": "H2D"})
    r = c.post(f"/api/alerts/{aid}/do/down", headers=h, json={}).json()
    assert not r["closed"] and {p["id"]: p for p in r["alert"]["context"]["paths"]}["down"]["off"]
    r = c.post(f"/api/alerts/{aid}/do/done", headers=h, json={"values": {"notes": "Wiped"}}).json()
    assert r["closed"] and ("down", "H2D", False) in f.calls
    with session_scope() as db:
        assert db.get(MaintTask, tid).state == "done"


def test_late_order_moves_its_due_date(setup):
    from app.db import session_scope
    from app.models import Order
    setup(Farm("H2D"))
    h, c = _key(), _client()
    due = datetime(2026, 11, 20, 17, tzinfo=timezone.utc)
    with session_scope() as db:
        o = Order(number=f"L{_s.token_hex(2)}", status="processing", due_at=due)
        db.add(o)
        db.flush()
        oid = o.id
    aid = _alert("order_late", "Order may ship late", f"alert:late:{oid}", {"order": oid})
    assert c.post(f"/api/alerts/{aid}/do/later", headers=h, json={"values": {"days": "5"}}).status_code == 400
    r = c.post(f"/api/alerts/{aid}/do/later", headers=h, json={"values": {"days": "2"}}).json()
    assert r["closed"] and r["alert"]["fix"] == "due date moved to Sun Nov 22"
    with session_scope() as db:
        o = db.get(Order, oid)
        assert o.due_at.date().isoformat() == "2026-11-22" and o.late_alerted_at is None


def test_a_closed_alert_cant_be_fixed_again(setup):
    setup(Farm("H2D", online=True))
    h, c = _key(), _client()
    aid = _alert("printer_offline", "H2D went offline", f"alert:printer_offline:H2D:{_s.token_hex(2)}", {"printer": "H2D"})
    assert c.post(f"/api/alerts/{aid}/do/back", headers=h, json={}).json()["closed"]
    r = c.post(f"/api/alerts/{aid}/do/back", headers=h, json={})
    assert r.status_code == 400 and "already fixed" in r.json()["detail"]
