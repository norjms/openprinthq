"""OpenPrintHQ driver: per-printer commands from the engine's `controls`, and the alarm feed."""
import httpx
import pytest

from test_openprinthq_driver import API, FakeOPHQ

PRUSALINK = {"pause_resume_stop": True, "print": True, "home": False, "temperature": False, "gcode": False}
FULL = {"pause_resume_stop": True, "print": True, "home": True, "temperature": True, "gcode": True}


class AlarmFake(FakeOPHQ):
    def __init__(self, alarms=True):
        super().__init__()
        self.has_alarms = alarms
        self.printers.append({"id": 3, "name": "MK4S", "model": "", "ip_address": "10.0.0.7",
                              "connection_type": "prusalink", "is_active": True, "controls": PRUSALINK})
        self.status[3] = {"connected": True, "state": "IDLE"}
        self.printers[0]["controls"] = FULL
        self.alarm_rows = [{"id": 7, "printer_id": 3, "printer_name": "MK4S", "source": "prusalink", "code": "attention",
                            "severity": "warning", "message": "The printer needs attention", "active": True}]

    def handler(self, request):
        p, m = request.url.path, request.method
        if p.startswith(API + "/alarm") or "/alarms/ack" in p:
            self.calls.append((m, p, request.content.decode() or None, dict(request.url.params)))
            if not self.has_alarms:
                return httpx.Response(404, json={"detail": "Not Found"})
            if p == API + "/alarms/summary":
                return httpx.Response(200, json={"active": 1, "unacknowledged": 1, "by_severity": {"warning": 1},
                                                 "printers": [3]})
            if p == API + "/alarms" and m == "GET":
                return httpx.Response(200, json=self.alarm_rows)
            if p == API + "/alarms/7" and m == "GET":
                return httpx.Response(200, json=self.alarm_rows[0])
            if p == API + "/alarm-suppressions" and m == "GET":
                return httpx.Response(200, json=[{"id": 2, "printer_id": 3, "code": "attention"}])
            if p.endswith("/dismiss"):
                return httpx.Response(200, json={"dismissed": 1})
            if p.endswith("/suppress"):
                return httpx.Response(200, json={"id": 2, "printer_id": 3, "code": "attention"})
            if m == "DELETE":
                return httpx.Response(200, json={"removed": 1})
            return httpx.Response(200, json={"acknowledged": 1})
        return super().handler(request)


def _client(fake):
    from app.clients.openprinthq_api import OpenPrintHQClient
    c = OpenPrintHQClient("https://ophq.example", "ophqk_test")
    c.client = httpx.Client(base_url="https://ophq.example", transport=httpx.MockTransport(fake.handler),
                            headers=c.client.headers)
    return c


def test_commands_follow_controls():
    c = _client(AlarmFake())
    by = {x["name"]: x for x in c.printers()["printers"]}
    mk4 = by["MK4S"]["commands"]
    assert "pause" in mk4 and "cancel" in mk4 and "set_ready" in mk4
    assert not {"home", "preheat", "cooldown", "set_temperature", "light_on"} & set(mk4)
    assert {"home", "preheat", "light_on"} <= set(by["X1C"]["commands"])
    # An engine that sends no controls: every command, as before.
    from app.clients.farm_base import ALL_COMMANDS
    assert by["Voron"]["commands"] == list(ALL_COMMANDS)


def test_command_refused_before_reaching_the_engine():
    from app.clients.base import ApiError
    fake = AlarmFake()
    c = _client(fake)
    with pytest.raises(ApiError) as e:
        c.command("MK4S", "preheat", {"material": "PLA"})
    assert e.value.status == 400 and "MK4S" in str(e.value)
    assert not [x for x in fake.calls if "/temperature/" in x[1]]
    c.command("MK4S", "pause")  # still allowed
    assert fake.calls[-1][:2] == ("POST", API + "/printers/3/print/pause")


def test_printer_allows():
    from app.clients.farm_base import printer_allows
    assert printer_allows({}, "home") is True  # a printer without a commands list allows everything
    assert printer_allows({"commands": ["pause"]}, "home") is False


def test_alarm_feed_capability_and_calls():
    fake = AlarmFake()
    c = _client(fake)
    assert c.has("alarms")
    rows = c.alarms(printer="MK4S", severity=["warning"], unacknowledged=True)
    assert rows[0]["code"] == "attention"
    get = [x for x in fake.calls if x[1] == API + "/alarms"][-1]
    assert get[3]["printer_id"] == "3" and get[3]["severity"] == "warning" and get[3]["unacknowledged"] == "true"
    s = c.alarm_summary()
    assert s["active"] == 1 and s["printer_names"] == ["MK4S"]
    assert c.ack_alarm(7, "james") == {"acknowledged": 1}
    assert c.ack_printer_alarms("MK4S", "james") == {"acknowledged": 1}
    assert fake.calls[-1][1] == API + "/printers/3/alarms/ack"


def test_dismiss_and_suppress_calls():
    fake = AlarmFake()
    c = _client(fake)
    assert c.alarm(7)["code"] == "attention"
    assert c.dismiss_alarm(7, "james") == {"dismissed": 1}
    assert fake.calls[-1][:3] == ("POST", API + "/alarms/7/dismiss", '{"by":"james"}')
    assert c.suppress_alarm(7, "james")["id"] == 2
    assert fake.calls[-1][1] == API + "/alarms/7/suppress"
    assert c.alarm_suppressions("MK4S")[0]["code"] == "attention"
    assert fake.calls[-1][3] == {"printer_id": "3"}
    assert c.unsuppress_alarm(2) == {"removed": 1}
    assert fake.calls[-1][:2] == ("DELETE", API + "/alarm-suppressions/2")


def test_older_engine_has_no_alarms():
    from app.clients.farm_base import FarmUnsupported
    c = _client(AlarmFake(alarms=False))
    assert not c.has("alarms")
    with pytest.raises(FarmUnsupported):
        c.alarms()
