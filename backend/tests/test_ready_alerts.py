"""Alerts for queued jobs waiting on a printer that isn't ready (a plate to clear)."""
import secrets as _s

import pytest


class FakeFarm:
    def __init__(self, printers):
        self.list = printers

    def printers(self):
        return {"printers": self.list}


@pytest.fixture
def setup(monkeypatch):
    from app.db import session_scope
    from app.kv import set_setting
    from app.models import FarmJob
    from app.services import alerts, farm
    sent = []
    monkeypatch.setattr(alerts, "notify", lambda db, kind, title, message, **kw: sent.append((kind, title, message)))
    printers = [{"name": "H2D", "online": True, "busy": False, "ready": False},
                {"name": "H2C", "online": True, "busy": True, "ready": False}]
    monkeypatch.setattr(farm, "_client", lambda: FakeFarm(printers))
    tag = _s.token_hex(3)
    with session_scope() as db:
        set_setting(db, "farm_ready_alerts", None)
        for j in db.query(FarmJob).filter(FarmJob.status.in_(("queued", "assigned"))):
            j.status = "completed"
        db.add(FarmJob(external_id=f"r{tag}1", status="queued", name="Planter", printer_name="H2D"))
        db.add(FarmJob(external_id=f"r{tag}2", status="queued", name="Bracket", printer_name="H2D"))
    yield sent, printers
    with session_scope() as db:
        for j in db.query(FarmJob).filter(FarmJob.external_id.like(f"r{tag}%")):
            db.delete(j)
        set_setting(db, "farm_ready_alerts", None)


def run(at):
    from app.db import session_scope
    from app.services import farm
    with session_scope() as db:
        return farm.ready_alerts(db, at=at)


def test_one_alert_after_the_grace_then_quiet(setup):
    sent, printers = setup
    run(1000)
    assert sent == []                          # not yet: only just not ready
    run(1000 + 301)
    assert len(sent) == 1
    kind, title, message = sent[0]
    assert kind == "waiting_ready" and title == "H2D: 2 jobs waiting"
    assert "Planter and 1 more" in message and "can't start on H2D" in message
    assert "plate" in message
    run(1000 + 900)
    assert len(sent) == 1                      # once until the printer is ready again
    printers[0]["ready"] = True                # plate cleared
    run(2000)
    printers[0]["ready"] = False               # the next print ended, work still waiting
    run(2100)
    run(2100 + 301)
    assert len(sent) == 2


def test_busy_printers_are_not_waiting(setup):
    sent, printers = setup
    printers[0]["busy"] = True
    run(1000)
    run(1000 + 400)
    assert sent == []


def test_waiting_ready_is_an_alert_event():
    from app.services import alerts
    assert "waiting_ready" in alerts.EVENTS
    assert "push" in alerts.DEFAULT_RULES["events"]["waiting_ready"]
