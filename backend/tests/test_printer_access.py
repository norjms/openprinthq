"""Printers shared by the team, narrowed per person by the owner (B32): the hub keeps who may use which printer,
applies it wherever it shows or acts on a printer, and hands it to the farm. The farm is a fake with three
printers."""
import pytest

from app.clients.base import ApiError
from tests.test_teams import as_user, people, real_auth  # noqa: F401  (fixtures)

PRINTERS = [{"id": 1, "name": "Alpha", "online": True, "busy": False, "state": "idle"},
            {"id": 2, "name": "Beta", "online": True, "busy": False, "state": "idle"},
            {"id": 3, "name": "Gamma", "online": False, "busy": False, "state": "offline"}]


class FakeFarm:
    kind = "openprinthq"

    def __init__(self):
        self.commands, self.pushed, self.push_status = [], [], None

    def printers(self):
        return {"printers": [dict(x) for x in PRINTERS]}

    def printer(self, name):
        return dict(next(x for x in PRINTERS if x["name"].lower() == name.lower()))

    def nozzles(self, pid):
        return []

    filament_paths = loaded = nozzles

    def command(self, name, command, args=None, confirm=False):
        self.commands.append((name, command))
        return {"ok": True}

    def alarms(self, state="active", printer=None, unacknowledged=False):
        return [{"id": 10, "printer_id": 1, "code": "A"}, {"id": 11, "printer_id": 2, "code": "B"}]

    def alarm_summary(self):
        return {"active": 2, "printers": [1, 2], "printer_names": ["Alpha", "Beta"]}

    def alarm(self, alarm_id):
        return next(dict(a) for a in self.alarms() if a["id"] == alarm_id)

    def dismiss_alarm(self, alarm_id, by=None):
        self.commands.append(("dismiss", alarm_id))
        return {"dismissed": 1}

    def suppress_alarm(self, alarm_id, by=None):
        self.commands.append(("suppress", alarm_id))
        return {"id": 5, "printer_id": self.alarm(alarm_id)["printer_id"]}

    def alarm_suppressions(self, printer=None):
        return [{"id": 5, "printer_id": 1, "code": "A"}, {"id": 6, "printer_id": 2, "code": "B"}]

    def unsuppress_alarm(self, sid):
        self.commands.append(("unsuppress", sid))
        return {"removed": 1}

    def push_printer_access(self, body):
        if self.push_status:
            raise ApiError("openprinthq", self.push_status, "nope")
        self.pushed.append(body)


@pytest.fixture
def farm(monkeypatch):
    from app import clients
    from app.routers import mobile
    from app.services import printer_access
    f = FakeFarm()
    monkeypatch.setattr(clients, "farm", lambda: f)
    monkeypatch.setattr(clients, "farm_kind", lambda team_id=None: "openprinthq")
    monkeypatch.setattr(mobile, "_farm", lambda: f)
    printer_access._cache.update(at=0.0, printers=[])
    yield f
    printer_access._cache.update(at=0.0, printers=[])


def _names(c):
    r = c.get("/api/mobile/v1/printers")
    assert r.status_code == 200, r.text
    return [x["name"] for x in r.json()["printers"]]


def test_everyone_has_every_printer_until_the_owner_narrows_it(farm, people, real_auth):
    assert _names(as_user(people["member"])) == ["Alpha", "Beta", "Gamma"]
    team = as_user(people["owner"]).get("/api/team").json()
    mine = {m["user_id"]: m["printers"] for m in team["members"]}
    assert mine[people["member"]] == "all" and mine[people["owner"]] == "all"


def test_the_owner_chooses_printers_and_the_rest_disappear(farm, people, real_auth):
    boss, mo = as_user(people["owner"]), as_user(people["member"])
    assert boss.get("/api/team/printers").json()["printers"] == [
        {"id": "1", "name": "Alpha"}, {"id": "2", "name": "Beta"}, {"id": "3", "name": "Gamma"}]
    r = boss.put(f"/api/team/members/{people['member']}/printers", json={"printers": ["2"]})
    assert r.status_code == 200, r.text
    assert r.json()["printers"] == ["2"]
    assert _names(mo) == ["Beta"]
    assert _names(boss) == ["Alpha", "Beta", "Gamma"]          # owners always have all
    assert mo.get("/api/mobile/v1/printers/Alpha").status_code == 404
    assert mo.get("/api/mobile/v1/printers/beta").status_code == 200   # names match as the farm's do
    assert mo.post("/api/mobile/v1/printers/Alpha/command", json={"command": "pause"}).status_code == 404
    assert mo.post("/api/mobile/v1/printers/Beta/command", json={"command": "pause"}).status_code == 200
    assert farm.commands == [("Beta", "pause")]
    assert [a["id"] for a in mo.get("/api/farm/alarms").json()["alarms"]] == [11]
    s = mo.get("/api/farm/alarms/summary").json()
    assert s["printers"] == [2] and s["printer_names"] == ["Beta"]
    assert mo.get("/api/farm/alarms", params={"printer": "Alpha"}).status_code == 404
    # Acting on an alarm or a suppression: only on her own printers.
    m = "/api/mobile/v1"
    assert mo.post(f"{m}/alarms/10/dismiss").status_code == 404
    assert mo.post(f"{m}/alarms/10/suppress").status_code == 404
    assert mo.post(f"{m}/alarms/10/ack").status_code == 404
    assert mo.post(f"{m}/alarms/11/dismiss").status_code == 200
    r = mo.post(f"{m}/alarms/11/suppress")
    assert r.status_code == 200 and r.json()["suppression"]["printer_id"] == 2
    assert [x["id"] for x in mo.get(f"{m}/alarm-suppressions").json()["suppressions"]] == [6]
    assert mo.delete(f"{m}/alarm-suppressions/5").status_code == 404
    assert mo.delete(f"{m}/alarm-suppressions/6").status_code == 200
    assert farm.commands[-3:] == [("dismiss", 11), ("suppress", 11), ("unsuppress", 6)]
    # back to all
    boss.put(f"/api/team/members/{people['member']}/printers", json={"printers": "all"})
    assert _names(mo) == ["Alpha", "Beta", "Gamma"]


def test_an_empty_list_means_no_printers(farm, people, real_auth):
    as_user(people["owner"]).put(f"/api/team/members/{people['member']}/printers", json={"printers": []})
    assert _names(as_user(people["member"])) == []


def test_only_an_owner_chooses_and_owners_cannot_be_narrowed(farm, people, real_auth):
    mo = as_user(people["member"])
    assert mo.put(f"/api/team/members/{people['viewer']}/printers", json={"printers": ["1"]}).status_code == 403
    boss = as_user(people["owner"])
    assert boss.put(f"/api/team/members/{people['owner']}/printers", json={"printers": ["1"]}).status_code == 400
    assert boss.put(f"/api/team/members/{people['member']}/printers", json={"printers": 5}).status_code in (400, 422)


def test_the_farm_is_told_who_may_use_which_printer(farm, people, real_auth):
    from app.db import session_scope
    from app.kv import set_setting
    from app.models import User
    from app.services import printer_access
    from app.tenant import use_team
    as_user(people["owner"]).put(f"/api/team/members/{people['member']}/printers", json={"printers": ["2", "3"]})
    with session_scope() as db, use_team(1):
        set_setting(db, printer_access.LAST_PUSH, None)
        db.commit()
        out = printer_access.push(db, 1)
        assert out["pushed"] >= 3
        body = farm.pushed[-1]
        mo = db.get(User, people["member"]).email.lower()
        boss = db.get(User, people["owner"]).email.lower()
        assert body["version"] == 1
        assert body["people"][mo] == [2, 3]
        assert body["people"][boss] == "all" and boss in body["owners"]
        assert printer_access.push(db, 1) == {"unchanged": True}
        set_setting(db, printer_access.LAST_PUSH, None)
        farm.push_status = 404                 # an older farm: noted, not an error
        assert printer_access.push(db, 1) == {"skipped": "older farm"}
        farm.push_status = 500
        with pytest.raises(ApiError):
            printer_access.push(db, 1)


def test_a_change_queues_the_push(farm, people, real_auth):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Job
    from app.tenant import unscoped
    as_user(people["owner"]).put(f"/api/team/members/{people['member']}/printers", json={"printers": ["1"]})
    with session_scope() as db, unscoped():
        assert db.execute(select(Job).where(Job.kind == "farm.push_printer_access", Job.state == "queued")).first()


def test_nothing_is_pushed_before_a_farm_is_chosen(farm, people, monkeypatch):
    from app import clients
    from app.db import session_scope
    from app.services import printer_access
    from app.services.installation import FarmNotChosen
    from app.tenant import use_team

    def none(team_id=None):
        raise FarmNotChosen()
    monkeypatch.setattr(clients, "farm_kind", none)
    with session_scope() as db, use_team(1):
        assert printer_access.push(db, 1) == {"skipped": "no farm chosen"}
