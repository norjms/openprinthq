"""The OpenPrintHQ driver: engine answers mapped to the shapes the hub reads, through the control plane with a key."""
import hashlib
import hmac
import json

import httpx
import pytest

API = "/api/engine/api/v1"


class FakeOPHQ:
    """Just enough of an OpenPrintHQ control plane + engine for the driver."""

    def __init__(self):
        self.calls = []
        self.printers = [{"id": 1, "name": "X1C", "model": "X1C", "ip_address": "10.0.0.5", "connection_type": "bambu",
                          "is_active": True, "nozzle_count": 1, "location": "Rack A"},
                         {"id": 2, "name": "Voron", "model": "voron24", "ip_address": "10.0.0.6",
                          "connection_type": "moonraker", "is_active": True}]
        self.status = {1: {"connected": True, "state": "RUNNING", "progress": 42.0, "remaining_time": 31,
                           "subtask_name": "Benchy", "layer_num": 10, "total_layers": 100,
                           "temperatures": {"nozzle": 220, "bed": 60},
                           "ams": [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF"},
                                                      {"id": 1, "tray_type": None}]}], "vt_tray": []},
                       2: {"connected": True, "state": "IDLE", "awaiting_plate_clear": False}}
        self.queue = [
            {"id": 11, "status": "pending", "printer_id": None, "library_file_id": 5, "library_file_name": "Benchy.3mf",
             "waiting_reason": None, "created_at": "2026-10-22T10:00:00Z"},
            {"id": 12, "status": "pending", "printer_id": None, "library_file_id": 6, "library_file_name": "Clip.3mf",
             "waiting_reason": "Waiting for filament: PETG", "filament_type": "PETG"},
            {"id": 13, "status": "printing", "printer_id": 1, "printer_name": "X1C", "library_file_id": 5,
             "library_file_name": "Benchy.3mf", "print_time_seconds": 3600, "filament_used_grams": 12.5,
             "started_at": "2026-10-22T11:00:00Z"},
            {"id": 14, "status": "skipped", "printer_id": 1, "printer_name": "X1C", "library_file_id": 5},
        ]
        self.next_id = 100
        self.providers = []
        # False = an engine from before out of service / maintenance log / priority (the probe gets a 422).
        self.parity = False
        self.down = []
        # None = an OpenPrintHQ from before named cameras (GET /api/cameras answers 404).
        self.cameras = None
        self.frames = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        p, m = request.url.path, request.method
        body = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else None
        self.calls.append((m, p, body, dict(request.url.params)))
        assert request.headers["authorization"] == "Bearer ophqk_test"
        if p == "/api/me":
            return httpx.Response(200, json={"id": 1, "apiKey": {"id": "k", "scope": "control"}})
        if p == API + "/printers/out-of-service":
            if not self.parity:
                return httpx.Response(422, json={"detail": [{"msg": "Input should be a valid integer"}]})
            return httpx.Response(200, json=self.down)
        if p.endswith("/out-of-service") and m in ("PUT", "DELETE"):
            pid = int(p.split("/")[-2])
            name = [x["name"] for x in self.printers if x["id"] == pid][0]
            reason = (body or {}).get("reason") if m == "PUT" else None
            self.down = [d for d in self.down if d["printer_id"] != pid]
            if m == "PUT":
                self.down.append({"printer_id": pid, "name": name, "reason": reason, "since": "2026-10-24T12:00:00Z"})
            return httpx.Response(200, json={"printer_id": pid, "name": name, "reason": reason})
        if p.startswith(API + "/maintenance/printers/") and p.endswith("/log") and m == "POST":
            return httpx.Response(200, json={"id": 51, "printer_id": int(p.split("/")[-2]), **body})
        if p.startswith(API + "/queue/") and m == "PATCH":
            return httpx.Response(200, json={"id": int(p.rstrip("/").split("/")[-1]), **(body or {})})
        if p == API + "/printers/":
            return httpx.Response(200, json=self.printers)
        if p.startswith(API + "/printers/") and p.endswith("/status"):
            return httpx.Response(200, json=self.status[int(p.split("/")[-2])])
        if p.startswith(API + "/printers/") and m == "POST":
            return httpx.Response(200, json={"success": True})
        if p == API + "/printers/1/camera/snapshot":
            return httpx.Response(200, content=b"\xff\xd8jpeg")
        if p == "/api/cameras":
            if self.cameras is None:
                return httpx.Response(404, json={"error": "Not Found"})
            return httpx.Response(200, json={"streams": self.cameras})
        if p == "/api/cameras/frame.jpeg":
            src = request.url.params.get("src")
            return (httpx.Response(200, content=b"\xff\xd8" + src.encode()) if src in self.frames
                    else httpx.Response(502, json={"error": "the camera did not return a frame"}))
        if p == API + "/queue/" and m == "GET":
            return httpx.Response(200, json=self.queue)
        if p == API + "/queue/" and m == "POST":
            self.next_id += 1
            return httpx.Response(200, json={"id": self.next_id, **body})
        if p.startswith(API + "/queue/") and m == "GET":
            qid = int(p.rstrip("/").split("/")[-1])
            hit = [q for q in self.queue if q["id"] == qid]
            return httpx.Response(200, json=hit[0]) if hit else httpx.Response(404, json={"detail": "not found"})
        if p.startswith(API + "/queue/") and m == "POST":
            return httpx.Response(200, json={"ok": True})
        if p == API + "/library/files/5":
            return httpx.Response(200, json={"id": 5, "metadata": {"sliced_for_model": "X1C"}})
        if p == API + "/library/files" and m == "POST":
            return httpx.Response(200, json={"id": 77, "filename": "Benchy.3mf", "file_type": "3mf", "file_size": 3})
        if p == API + "/printer-groups/":
            return httpx.Response(200, json=[{"id": 4, "name": "Shop"}])
        if p == API + "/library/folders" and m == "GET":
            return httpx.Response(200, json=[{"id": 8, "name": "Artisan", "parent_id": None,
                                              "children": [{"id": 9, "name": "Benchy", "parent_id": 8}]}])
        if p == API + "/library/folders" and m == "POST":
            return httpx.Response(200, json={"id": 10, **body})
        if p == API + "/notifications/" and m == "GET":
            return httpx.Response(200, json=self.providers)
        if p == API + "/notifications/" and m == "POST":
            self.providers.append({"id": 3, **body})
            return httpx.Response(200, json=self.providers[-1])
        if p == API + "/maintenance/overview":
            return httpx.Response(200, json=[{"printer_id": 1, "printer_name": "X1C", "total_print_hours": 120.0,
                "due_count": 1, "warning_count": 0, "maintenance_items": [
                    {"id": 31, "printer_id": 1, "printer_name": "X1C", "maintenance_type_name": "Lubricate rods",
                     "enabled": True, "interval_hours": 100, "interval_type": "hours", "current_hours": 120.0,
                     "hours_since_maintenance": 120.0, "hours_until_due": -20.0, "days_since_maintenance": None,
                     "days_until_due": None, "is_due": True, "is_warning": False, "last_performed_at": None},
                    {"id": 32, "printer_id": 1, "printer_name": "X1C", "maintenance_type_name": "Clean build plate",
                     "enabled": True, "interval_hours": 7, "interval_type": "days", "current_hours": 120.0,
                     "hours_since_maintenance": 3.0, "hours_until_due": 0, "days_since_maintenance": 2.0,
                     "days_until_due": 5.0, "is_due": False, "is_warning": False,
                     "last_performed_at": "2026-10-20T09:00:00Z"}]}])
        if p.startswith(API + "/maintenance/items/") and p.endswith("/perform"):
            return httpx.Response(200, json={"id": int(p.split("/")[-2]), "is_due": False})
        return httpx.Response(200, json={"ok": True})


@pytest.fixture
def ophq():
    from app.clients.openprinthq_api import OpenPrintHQClient
    fake = FakeOPHQ()
    c = OpenPrintHQClient("https://ophq.example", "ophqk_test")
    c.client = httpx.Client(base_url="https://ophq.example", transport=httpx.MockTransport(fake.handler),
                            headers=c.client.headers)
    return c, fake


def test_is_a_farm_backend(ophq):
    from app.clients.farm_base import CAPABILITIES, FarmBackend
    c, _ = ophq
    assert isinstance(c, FarmBackend) and c.kind == "openprinthq"
    assert set(c.CAPABILITIES) <= set(CAPABILITIES)
    assert c.has("queue") and c.has("webhooks")
    assert c.ping()["apiKey"]["scope"] == "control"


def test_printers_in_the_hubs_shape(ophq):
    c, _ = ophq
    ps = {p["name"]: p for p in c.printers()["printers"]}
    x = ps["X1C"]
    assert x["online"] and x["busy"] and not x["ready"] and x["progress"] == 42.0 and x["eta_minutes"] == 31
    assert x["job"] == "Benchy" and x["nozzle_temp"] == 220 and x["layer"] == 10
    assert ps["Voron"]["ready"] and not ps["Voron"]["busy"]
    full = c.printers_full()
    assert full[0]["ip_address"] == "10.0.0.5" and full[0]["capabilities"]["ams_units"] == 1
    fa = c.filament_assignments()["printers"]
    assert fa[1]["assignments"] == [{"spool_id": None, "unit": 0, "slot": 0, "material_type": "PLA",
                                     "spool_color_hex": "#FF0000", "name": None, "remain_pct": None}]
    assert c.snapshot("X1C") == b"\xff\xd8jpeg"


def test_queue_maps_statuses_for_the_farm_mirror(ophq):
    from app.services.farm import fields_private
    c, _ = ophq
    jobs = {j["id"]: j for j in c.queue()["jobs"]}
    assert jobs[11]["status"] == "queued" and jobs[12]["status"] == "waiting_filament"
    assert jobs[13]["status"] == "printing" and jobs[14]["status"] == "cancelled"
    f = fields_private(jobs[13])
    assert f["printer_name"] == "X1C" and f["file_ref"] == "5" and f["est_time_s"] == 3600 and f["filament_g"] == 12.5
    assert fields_private(jobs[12])["waiting_reason"] == "Waiting for filament: PETG"
    active = c.queue(statuses=["queued", "waiting_filament", "printing"])["jobs"]
    assert [j["id"] for j in active] == [11, 12, 13]
    assert c.queue(sort="job_id_desc")["jobs"][0]["id"] == 14
    assert c.job(13)["assigned_printer"] == "X1C" and c.job(13)["status"] == "printing"
    from app.clients.base import ApiError
    with pytest.raises(ApiError) as e:
        c.job(999)
    assert e.value.status == 404


def test_submit_picks_the_model_or_group_and_honours_quantity_and_priority(ophq):
    c, fake = ophq
    out = c.submit_job(file_ref="5", quantity=2, job_name="#1001 BENCHY", priority=100)
    assert out["job_ids"] == [101, 102]
    posts = [b for (m, p, b, _q) in fake.calls if m == "POST" and p == API + "/queue/"]
    assert posts == [{"library_file_id": 5, "insert_at_top": True, "target_model": "X1C"}] * 2
    c.target_group = "Shop"
    c.submit_job(file_ref="5")
    assert [b for (m, p, b, _q) in fake.calls if m == "POST" and p == API + "/queue/"][-1] == \
        {"library_file_id": 5, "insert_at_top": False, "target_group_id": 4}


def test_commands_files_folders_and_cancel(ophq):
    c, fake = ophq
    c.command("X1C", "set_ready")
    c.command("X1C", "pause")
    from app.clients.farm_base import FarmUnsupported
    with pytest.raises(FarmUnsupported):
        c.command("X1C", "self_destruct")
    c.cancel_queued(11, "order cancelled")
    c.cancel_job(13)
    posts = [p for (m, p, _b, _q) in fake.calls if m == "POST"]
    assert API + "/printers/1/clear-plate" in posts and API + "/printers/1/print/pause" in posts
    assert API + "/queue/11/cancel" in posts and API + "/queue/13/stop" in posts
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".3mf") as fh:
        fh.write(b"3mf")
        fh.flush()
        assert c.upload_file(fh.name, "Benchy.3mf")["uuid"] == "77"
    assert c.library_folder("Benchy", 8)["id"] == 9          # found, not made again
    assert c.library_folder("Clip", 8)["id"] == 10           # made
    c.move_library_file("77", 9)
    assert ("POST", API + "/library/files/move", {"file_ids": [77], "folder_id": 9}, {}) in fake.calls


def test_webhook_is_a_signed_notification_provider(ophq):
    from app.services.setup import FARM_EVENTS
    c, fake = ophq
    c.add_webhook("https://hub.example/hooks/openprinthq", FARM_EVENTS, "s3cret")
    body = fake.providers[0]
    assert body["provider_type"] == "webhook" and body["config"]["signing_secret"] == "s3cret"
    assert body["on_print_complete"] and body["on_queue_job_started"] and body["on_printer_error"]
    hooks = c.webhooks()
    assert hooks == [{"id": 3, "url": "https://hub.example/hooks/openprinthq", "events": FARM_EVENTS, "active": True}]


def test_openprinthq_webhook_checks_the_signature_and_routes(env):
    from fastapi.testclient import TestClient
    from app.db import session_scope
    from app.kv import set_setting
    from app.main import app
    from app.models import Job
    with session_scope() as db:
        set_setting(db, "ophq_webhook_secret", "s3cret")
        before = db.query(Job).filter(Job.kind.in_(("farm.sync", "farm.event"))).count()
    c = TestClient(app)
    body = json.dumps({"event": "print_complete", "printer": "X1C", "source": "OpenPrintHQ"}).encode()
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert c.post("/hooks/openprinthq", content=body, headers={"x-ophq-signature": "sha256=nope"}).status_code == 401
    assert c.post("/hooks/openprinthq", content=body,
                  headers={"x-ophq-signature": sig, "x-ophq-event": "print_complete"}).status_code == 200
    alarm = json.dumps({"event": "printer_error", "printer": "X1C", "message": "AMS jam"}).encode()
    sig2 = "sha256=" + hmac.new(b"s3cret", alarm, hashlib.sha256).hexdigest()
    assert c.post("/hooks/openprinthq", content=alarm,
                  headers={"x-ophq-signature": sig2, "x-ophq-event": "printer_error"}).status_code == 200
    with session_scope() as db:
        kinds = [j.kind for j in db.query(Job).filter(Job.kind.in_(("farm.sync", "farm.event")))]
        assert len(kinds) >= before + 1 and "farm.event" in kinds
        ev = db.query(Job).filter_by(kind="farm.event").order_by(Job.id.desc()).first()
        assert ev.payload["event"] == "alarm.raised" and ev.payload["data"]["printer_name"] == "X1C"


def test_maintenance_items_are_the_schedules(ophq):
    c, fake = ophq
    assert c.has("maintenance") and not c.has("out_of_service") and not c.has("maintenance_log")
    rows = {r["id"]: r for r in c.maint_schedules()}
    assert rows[31]["printer_name"] == "X1C" and rows[31]["task_name"] == "Lubricate rods"
    assert rows[31]["interval_hours"] == 100 and rows[31]["interval_days"] is None and rows[31]["status"] == "due"
    assert rows[32]["interval_days"] == 7 and rows[32]["interval_hours"] is None and rows[32]["status"] == "ok"
    assert rows[32]["last_completed_at"] == "2026-10-20T09:00:00Z"
    c.maint_complete(31, "james", "new grease", 15)
    m, path, body, _ = fake.calls[-1]
    assert (m, path) == ("POST", API + "/maintenance/items/31/perform")
    assert body["notes"] == "new grease; by james; 15 min"
    assert c.maint_down() == []
    from app.clients.farm_base import FarmUnsupported
    with pytest.raises(FarmUnsupported):
        c.set_down("X1C", True)


def test_newer_engine_has_out_of_service_maintenance_log_and_priority(ophq):
    c, fake = ophq
    fake.parity = True
    assert c.has("out_of_service") and c.has("maintenance_log")
    assert {"out_of_service", "maintenance_log"} <= set(c.describe()["capabilities"])

    c.set_down("X1C", True, "Lubricate rods (OpenPrintHQ)")
    m, path, body, _ = fake.calls[-1]
    assert (m, path, body) == ("PUT", API + "/printers/1/out-of-service", {"reason": "Lubricate rods (OpenPrintHQ)"})
    assert c.maint_down() == [{"printer_name": "X1C", "reason": "Lubricate rods (OpenPrintHQ)",
                               "since": "2026-10-24T12:00:00Z"}]
    c.set_down("X1C", False)
    assert fake.calls[-1][:2] == ("DELETE", API + "/printers/1/out-of-service")
    assert c.maint_down() == []

    c.maint_log("Voron", "Replaced PTFE tube", "Consumable", "james", "Parts: PTFE 1m", 10)
    m, path, body, _ = fake.calls[-1]
    assert (m, path) == ("POST", API + "/maintenance/printers/2/log")
    assert body == {"title": "Replaced PTFE tube", "category": "consumable", "notes": "Parts: PTFE 1m; 10 min",
                    "performed_by": "james"}
    c.maint_log("Voron", "Something", "vibes", "")
    assert fake.calls[-1][2]["category"] == "other" and fake.calls[-1][2]["performed_by"] is None

    c.maint_complete(31, "james", "new grease", 15)
    assert fake.calls[-1][2] == {"notes": "new grease; 15 min", "performed_by": "james"}

    c.submit_job(file_ref="5", priority=100)
    post = [b for (m, p, b, _q) in fake.calls if m == "POST" and p == API + "/queue/"][-1]
    assert post == {"library_file_id": 5, "priority": 100, "target_model": "X1C"}
    c.set_priority(11, 60)
    assert fake.calls[-1][:3] == ("PATCH", API + "/queue/11", {"priority": 60})
    c.set_priority(11, 500)
    assert fake.calls[-1][2] == {"priority": 100}


def test_older_engine_probe_is_cached_and_keeps_the_old_behaviour(ophq):
    c, fake = ophq
    assert not c.has("out_of_service") and not c.has("maintenance_log")
    probes = [x for x in fake.calls if x[1] == API + "/printers/out-of-service"]
    assert len(probes) == 1  # the second has() used the cached answer
    from app.clients.farm_base import FarmUnsupported
    with pytest.raises(FarmUnsupported):
        c.maint_log("X1C", "x", "inspection", "james")
    c.set_priority(11, 80)
    assert fake.calls[-1][:3] == ("POST", API + "/queue/reorder", {"items": [{"id": 11, "position": 0}]})
    assert c.set_priority(11, 50) == {"ok": True, "unchanged": True}


def test_probe_network_errors_are_not_cached(ophq):
    from app.clients.base import ApiError
    c, fake = ophq
    calls = {"n": 0}
    real = c.e

    def flaky(method, path, **kw):
        if path == "/printers/out-of-service":
            calls["n"] += 1
            if calls["n"] == 1:
                raise ApiError("openprinthq", None, "connection refused")
        return real(method, path, **kw)

    c.e = flaky
    fake.parity = True
    assert c.engine_parity() is False   # unknown counts as no
    assert c.engine_parity() is True    # and is asked again next time
    assert calls["n"] == 2


def test_last_finished_takes_the_item_that_just_ended_on_that_printer(ophq):
    from datetime import datetime, timedelta, timezone
    c, fake = ophq
    now = datetime.now(timezone.utc)
    fake.queue += [
        {"id": 20, "status": "completed", "printer_id": 1, "printer_name": "X1C", "library_file_name": "Old.3mf",
         "completed_at": (now - timedelta(hours=3)).isoformat()},
        {"id": 21, "status": "failed", "printer_id": 1, "printer_name": "X1C", "library_file_name": "Clip.3mf",
         "error_message": "spaghetti", "started_at": (now - timedelta(hours=1)).isoformat(),
         "completed_at": (now - timedelta(minutes=2)).isoformat()},
        {"id": 22, "status": "completed", "printer_id": 2, "printer_name": "Voron", "library_file_name": "Box.3mf",
         "completed_at": now.isoformat()},
    ]
    j = c.last_finished("X1C")
    assert j["id"] == 21 and j["name"] == "Clip.3mf" and j["status"] == "failed" and j["error_message"] == "spaghetti"
    assert c.last_finished("Nobody") is None
    fake.queue = [q for q in fake.queue if q["id"] != 21]
    assert c.last_finished("X1C") is None            # only an older item: not this print


def test_print_webhooks_queue_a_print_history_record(env):
    from fastapi.testclient import TestClient
    from app.db import session_scope
    from app.kv import set_setting
    from app.main import app
    from app.models import Job
    with session_scope() as db:
        set_setting(db, "ophq_webhook_secret", "s3cret")
    c = TestClient(app)
    body = json.dumps({"event": "print_failed", "printer": "X1C", "filename": "Clip", "reason": "spaghetti",
                       "timestamp": "2026-10-22T12:00:00Z", "source": "OpenPrintHQ"}).encode()
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert c.post("/hooks/openprinthq", content=body,
                  headers={"x-ophq-signature": sig, "x-ophq-event": "print_failed"}).status_code == 200
    with session_scope() as db:
        j = db.query(Job).filter_by(kind="slicing.farm_job").order_by(Job.id.desc()).first()
        assert j.payload["event"] == "queue.job_failed"
        assert j.payload["data"]["printer_name"] == "X1C" and j.payload["data"]["source"] == "openprinthq"
        assert j.payload["data"]["message"] == "spaghetti"


def test_submit_job_pinned_to_a_printer(ophq):
    c, fake = ophq
    assert c.has("pin_printer")
    c.submit_job(file_ref="5", job_name="Box", printer="voron")      # case differs from the farm's "Voron"
    post = [b for (m, p, b, _q) in fake.calls if m == "POST" and p == API + "/queue/"][-1]
    assert post["printer_id"] == 2 and "target_model" not in post and "target_group_id" not in post


def test_cameras_are_read_with_a_chamber_camera_first(ophq):
    c, fake = ophq
    fake.cameras = [
        {"printer_id": 1, "printer_name": "X1C", "kind": "builtin", "camera_name": "Printer camera",
         "stream_name": "p1", "enabled": True, "state": "ONLINE"},
        {"printer_id": 1, "printer_name": "X1C", "kind": "extra", "camera_name": "Chamber", "stream_name": "p1_c4",
         "enabled": True, "state": "UNKNOWN"},
        {"printer_id": 1, "printer_name": "X1C", "kind": "extra", "camera_name": "Top", "stream_name": "p1_c5",
         "enabled": True, "state": "OFFLINE"},
        {"printer_id": 1, "printer_name": "X1C", "kind": "engine", "camera_name": "External", "stream_name": "p1_ext",
         "enabled": False, "state": "DISABLED"},
        {"printer_id": 2, "printer_name": "Voron", "kind": "builtin", "camera_name": "Printer camera",
         "stream_name": "p2", "enabled": True, "state": "ONLINE"},
    ]
    assert c.camera_sources("X1C") == ["p1_c4", "p1"]
    assert c.camera_sources("voron") == ["p2"]
    fake.frames = {"p1_c4", "p1"}
    assert c.frame("p1_c4") == b"\xff\xd8p1_c4"
    assert c.snapshot("X1C") == b"\xff\xd8p1_c4", "the chamber camera first"
    fake.frames = {"p1"}
    assert c.snapshot("X1C") == b"\xff\xd8p1", "then the next camera"
    fake.frames = set()
    assert c.frame("p1") is None
    assert c.snapshot("X1C") == b"\xff\xd8jpeg", "and the engine's own snapshot when no stream answers"


def test_an_openprinthq_without_named_cameras_keeps_the_engine_snapshot(ophq):
    c, fake = ophq
    assert fake.cameras is None
    assert c.camera_sources("X1C") == []
    assert c.snapshot("X1C") == b"\xff\xd8jpeg"
