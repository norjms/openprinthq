"""Tests for openprinthq_print_queue.py (OpenPrintHQ Print Queue) against a fake orca host and a mock OpenPrintHQ."""

import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import threading
from unittest import mock
import time
import types
import unittest
import urllib.request
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "plugin", "openprinthq_print_queue.py")
KEY = "ophqk_" + "A1b2_C3d4-" * 4 + "xyz"


# ---------------------------------------------------------------- fake orca host

class FakePreset:
    def __init__(self, name, host, key, agent="openprinthq", visible=True):
        self.name = name
        self.is_visible = visible
        self._cfg = {"print_host": host, "printhost_apikey": key, "printer_agent": agent}

    def is_user(self):
        return True

    def config_value(self, key):
        return self._cfg.get(key)


class FakeCollection:
    def __init__(self, bundle):
        self.bundle = bundle

    def preset_names(self):
        return [p.name for p in self.bundle.all_presets()]

    def find_preset(self, name):
        for p in self.bundle.all_presets():
            if p.name == name:
                return p
        return None


class FakeBundle:
    def __init__(self):
        self.preset = FakePreset("", "", "")
        self.extra = []
        self.printers = FakeCollection(self)

    def all_presets(self):
        return [self.preset] + self.extra

    def current_printer_preset(self):
        return self.preset


NOTIFICATIONS = []
WINDOWS = []
MODAL_ANSWER = {}   # what the fake pre-send window answers: {"action": "send"|"", "printer": ...}
BUNDLE = FakeBundle()


class FakeWindow:
    _next = 1

    def __init__(self, html, title, on_message, on_close):
        self.html, self.title, self.on_message, self.on_close = html, title, on_message, on_close
        self.posted = []
        self.open = True
        self.id = FakeWindow._next
        FakeWindow._next += 1

    def post(self, data):
        self.posted.append(data)

    def close(self):
        self.open = False

    def is_open(self):
        return self.open


def make_fake_orca():
    orca = types.ModuleType("orca")
    pa = types.ModuleType("orca.printer_agent")

    class PrinterAgentBase:
        def __init__(self):
            self._stored_config = "{}"

        def get_config(self):
            return self._stored_config

        def save_config(self, text):  # Orca's API takes a JSON string
            if not isinstance(text, str):
                raise TypeError("save_config expects a JSON string")
            self._stored_config = text
            return True

    class AgentInfo:
        def __init__(self, id, name, version, description):
            self.id, self.name, self.version, self.description = id, name, version, description

    pa.PrinterAgentBase = PrinterAgentBase
    pa.AgentInfo = AgentInfo
    pa.FilamentSyncMode = types.SimpleNamespace(None_="none", Subscription="subscription", Pull="pull")
    orca.printer_agent = pa

    class Levels:
        pass
    for n in ("WarningNotificationLevel", "ErrorNotificationLevel", "PrintInfoNotificationLevel"):
        setattr(Levels, n, n)
    def create_window(html, title="OrcaSlicer", width=820, height=600, on_message=None, on_close=None,
                      style=0, on_submit=None):
        w = FakeWindow(html, title, on_message, on_close)
        w.on_submit, w.style = on_submit, style
        WINDOWS.append(w)
        if style and MODAL_ANSWER:
            answer = dict(MODAL_ANSWER)
            threading.Timer(0.05, lambda: on_submit(answer) if answer.get("action") else on_close()).start()
        return w
    ui = types.SimpleNamespace(NotificationLevel=Levels, create_window=create_window,
                               push_notification=lambda level, text: NOTIFICATIONS.append((level, text)))
    pages = types.ModuleType("orca.pages")

    class PagesPluginCapabilityBase:
        def __init__(self):
            self.posted = []

        def post_message(self, data):
            self.posted.append(data)
    pages.PagesPluginCapabilityBase = PagesPluginCapabilityBase
    orca.pages = pages
    orca.host = types.SimpleNamespace(preset_bundle=lambda: BUNDLE, ui=ui)

    class base:
        pass
    orca.base = base
    orca.registered = []
    orca.plugin = lambda cls: cls
    orca.register_capability = orca.registered.append
    return orca


sys.modules["orca"] = make_fake_orca()
spec = importlib.util.spec_from_file_location("openprinthq_print_queue", PLUGIN)
wt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wt)


# Tests never touch the real OS credential store unless they ask for it.
wt.SECRETS = wt.SecretStore("openprinthq-print-queue", platform="plan9")
MOD = wt


# ---------------------------------------------------------------- mock OpenPrintHQ (control plane + engine API)

ENGINE = "/api/engine/api/v1"


def printer_rows():
    def row(pid, name, model, conn, **kw):
        r = {"id": pid, "name": name, "model": model, "connection_type": conn, "is_active": True,
             "controls": {"pause_resume_stop": True, "print": True}, "out_of_service": False,
             "out_of_service_reason": None, "ip_address": "10.0.0.%d" % pid, "serial_number": "SECRET-%d" % pid}
        r.update(kw)
        return r
    return [row(1, "H2D", "H2D", "bambu"), row(2, "MK3S", "MK3S", "octoprint"),
            row(3, "MK4S", "Prusa MK4S", "prusalink"), row(4, "H2C", "H2C", "bambu")]


H2D_AMS = [{"id": 0, "humidity": 4, "temp": 29.2, "is_ams_ht": False, "serial_number": "S0", "tray": [
               {"id": 0, "tray_type": "ABS", "tray_info_idx": "GFB00", "tray_color": "0A2CA5FF", "remain": 72,
                "tag_uid": "AD920D3F00000100", "tray_uuid": "X", "state": 11},
               {"id": 1, "tray_type": "PLA", "tray_info_idx": "GFL99", "tray_color": "898989FF", "remain": -1},
               {"id": 2, "tray_type": "PLA", "tray_info_idx": "GFL99", "tray_color": "898989FF", "remain": -1},
               {"id": 3, "tray_type": None, "remain": 0}]},
           {"id": 128, "humidity": 5, "temp": 27.0, "is_ams_ht": True, "tray": [
               {"id": 0, "tray_type": "PETG", "tray_info_idx": "Pb5067e0", "tray_color": "FF5722FF", "remain": -1}]}]


def statuses():
    temps = {"nozzle": 26.0, "nozzle_target": 0.0, "bed": 25.0, "bed_target": 0.0}
    return {
        1: {"id": 1, "name": "H2D", "connected": True, "state": "RUNNING", "progress": 32.0, "remaining_time": 94,
            "layer_num": 30, "total_layers": 223, "subtask_name": "part", "awaiting_plate_clear": False,
            "temperatures": {"nozzle": 44.0, "nozzle_target": 0.0, "bed": 55.0, "bed_target": 55.0},
            "ams": [json.loads(json.dumps(u)) for u in H2D_AMS], "vt_tray": [{"id": 254, "tray_type": None}],
            "nozzles": [{"nozzle_diameter": "0.4", "nozzle_type": "hardened_steel"}] * 2},
        2: {"id": 2, "name": "MK3S", "connected": True, "state": "IDLE", "progress": 0, "awaiting_plate_clear": True,
            "subtask_name": "old benchy", "temperatures": dict(temps), "ams": [], "vt_tray": []},
        3: {"id": 3, "name": "MK4S", "connected": True, "state": "IDLE", "awaiting_plate_clear": False,
            "temperatures": dict(temps), "ams": [], "vt_tray": []},
        4: {"id": 4, "name": "H2C", "connected": False, "state": None, "temperatures": {}, "ams": [], "vt_tray": []},
    }


def spool(material, rgba, brand="Polymaker", weight=1000, used=250, **kw):
    s = {"id": 0, "material": material, "rgba": rgba, "brand": brand, "label_weight": weight, "weight_used": used,
         "color_name": kw.pop("color_name", ""), "archived_at": None, "low_stock_threshold_pct": None,
         "nozzle_temp_min": 240, "nozzle_temp_max": 260, "slicer_filament": ""}
    s.update(kw)
    return s


class Eng:
    """State of the mock farm."""

    @classmethod
    def reset(cls):
        cls.key, cls.scope, cls.email = KEY, "control", "farm@example.com"
        cls.calls = []          # (method, path after the API prefix, query, body)
        cls.fail = {}           # (method, path regex) -> (status, body dict)
        cls.html = False        # answer every request with an HTML sign-in page
        cls.printers = printer_rows()
        cls.status = statuses()
        cls.nozzles = {1: [0.4, 0.4], 2: [0.6], 3: [0.4], 4: [0.4, 0.4]}
        cls.assignments = {3: [{"ams_id": 255, "tray_id": 0, "printer_id": 3, "spool": spool("ABS", "000000FF")}],
                           2: [{"ams_id": 255, "tray_id": 0, "printer_id": 2, "spool": spool("PLA", "00BCD4FF")}]}
        cls.library = {}        # id -> {"id", "filename", "file_hash", "data"}
        cls.next_file = 40
        cls.queue = {}
        cls.next_item = 100
        for item in ({"id": 1, "status": "pending", "printer_id": 3, "priority": 50, "position": 1,
                      "library_file_name": "Filamentalist.gcode", "print_time_seconds": 10080,
                      "created_by_username": "farmuser"},
                     {"id": 2, "status": "completed", "printer_id": 3, "priority": 50, "position": 0,
                      "library_file_name": "Old.gcode"},
                     {"id": 5, "status": "printing", "printer_id": 2, "priority": 50, "position": 1,
                      "library_file_name": "3dbenchy.gcode", "created_by_username": "farmuser"},
                     {"id": 6, "status": "pending", "printer_id": None, "target_model": "H2D", "priority": 75,
                      "position": 2, "library_file_name": "Bracket.gcode.3mf", "required_filament_types": ["PETG"],
                      "waiting_reason": "Waiting for filament: PETG on H2D"}):
            cls.add_item(**item)
        cls.out_of_service = []
        cls.spools = [spool("PLA", "FF0000FF", color_name="Red", used=950), spool("PETG", "00FF00FF", used=100)]
        cls.settings = {"low_stock_threshold": 20.0}
        cls.reasons = {}        # printer name -> preflight reasons (default: [])
        cls.preflights = []

    @classmethod
    def add_item(cls, **kw):
        item = {"id": cls.next_item, "status": "pending", "printer_id": None, "priority": 50, "position": 0,
                "library_file_id": None, "library_file_name": None, "archive_name": None, "target_model": None,
                "batch_id": None, "manual_start": False, "waiting_reason": None, "error_message": None,
                "required_filament_types": None, "filament_type": None, "created_by_username": None,
                "print_time_seconds": None, "created_at": "2026-11-08T10:00:00"}
        item.update(kw)
        item["printer_name"] = next((p["name"] for p in cls.printers if p["id"] == item["printer_id"]), None)
        cls.queue[item["id"]] = item
        cls.next_item = max(cls.next_item, item["id"]) + 1
        return item

    @classmethod
    def posted(cls, method, path):
        return [c for c in cls.calls if c[0] == method and re.fullmatch(path, c[1])]


Eng.reset()

SUBMIT_WRITES = (r"/queue/?", r"/library/files/?")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, doc, ctype="application/json"):
        raw = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self, method):
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if Eng.html:
            return self._send(200, b"<html><body>Sign in</body></html>", "text/html")
        if self.headers.get("Authorization") != "Bearer " + Eng.key:
            return self._send(401, {"detail": "Invalid API key"} if url.path.startswith("/api/v1") else
                              {"error": "invalid or expired API key"})
        if url.path == "/api/me":
            return self._send(200, {"email": Eng.email, "apiKey": {"id": 7, "scope": Eng.scope}})
        if url.path.startswith(ENGINE):
            path = url.path[len(ENGINE):]
            if method != "GET" and Eng.scope == "submit" and not (
                    method == "POST" and any(re.fullmatch(p, path) for p in SUBMIT_WRITES)):
                return self._send(403, {"error": "this key's scope is submit; this needs control", "needs": "control"})
        elif url.path.startswith("/api/v1"):
            path = url.path[len("/api/v1"):]
        else:
            return self._send(404, {"detail": "Not Found"})
        body = None
        if raw and "json" in (self.headers.get("Content-Type") or ""):
            body = json.loads(raw)
        Eng.calls.append((method, path, query, body if body is not None else raw))
        for (m, rx), (status, doc) in Eng.fail.items():
            if m == method and re.fullmatch(rx, path):
                return self._send(status, doc)
        out = self._engine(method, path, query, body, raw)
        if out is None:
            return self._send(404, {"detail": "Not Found"})
        status, doc = out
        return self._send(status, doc)

    def _engine(self, method, path, query, body, raw):
        E = Eng
        m = re.fullmatch(r"/printers/(\d+)(/.*)?", path)
        if method == "GET" and path == "/printers/":
            return 200, E.printers
        if method == "GET" and path == "/printers/out-of-service":
            return 200, E.out_of_service
        if m:
            pid, rest = int(m.group(1)), m.group(2) or ""
            if pid not in E.status:
                return 404, {"detail": "Printer not found"}
            if method == "GET" and rest == "/status":
                return 200, E.status[pid]
            if method == "GET" and rest == "/nozzles":
                return 200, {"nozzle_count": len(E.nozzles[pid]), "nozzles": [
                    {"extruder_index": i, "diameter_mm": d, "nozzle_type": "hardened_steel" if pid == 1 else "undefine",
                     "volume_type": "High Flow" if pid == 1 else "Standard", "source": "mqtt"}
                    for i, d in enumerate(E.nozzles[pid])]}
            n = re.fullmatch(r"/nozzles/(\d+)", rest)
            if method == "PUT" and n:
                E.nozzles[pid][int(n.group(1))] = body["diameter_mm"]
                return 200, {"nozzles": []}
            if method == "POST" and rest == "/clear-plate":
                if not E.status[pid].get("awaiting_plate_clear"):
                    return 400, {"detail": "Printer is not awaiting plate-clear acknowledgment (state=IDLE)"}
                E.status[pid]["awaiting_plate_clear"] = False
                return 200, {"success": True}
            if method == "POST" and rest in ("/print/pause", "/print/resume", "/print/stop"):
                return 200, {"success": True}
        if method == "GET" and path == "/inventory/assignments":
            return 200, E.assignments.get(int(query.get("printer_id", 0)), [])
        if method == "GET" and path == "/inventory/spools":
            return 200, E.spools
        if method == "GET" and path == "/settings/":
            return 200, E.settings
        if method == "POST" and path == "/library/files":
            name = re.search(rb'filename="([^"]+)"', raw).group(1).decode()
            data = raw.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
            digest = hashlib.sha256(data).hexdigest()
            if query.get("reuse_duplicate") == "true":
                for f in E.library.values():
                    if f["file_hash"] == digest:
                        return 200, dict(f, data=None, reused=True)
            E.next_file += 1
            E.library[E.next_file] = {"id": E.next_file, "filename": name, "file_hash": digest, "data": data}
            return 200, {"id": E.next_file, "filename": name, "reused": False, "duplicate_of": None}
        h = re.fullmatch(r"/library/files/by-hash/([0-9a-f]{64})", path)
        if method == "GET" and h:
            f = next((f for f in E.library.values() if f["file_hash"] == h.group(1)), None)
            return (200, {k: v for k, v in f.items() if k != "data"}) if f else (404, {"detail": "Library file not found"})
        if method == "GET" and path == "/queue/":
            rows = [i for i in E.queue.values() if not query.get("status") or i["status"] == query["status"]]
            if query.get("printer_id"):
                rows = [i for i in rows if i["printer_id"] == int(query["printer_id"])]
            return 200, sorted(rows, key=lambda i: (i["printer_id"] or 0, i["position"]))
        if method == "POST" and path == "/queue/":
            if body.get("library_file_id") not in E.library:
                return 404, {"detail": "Library file not found"}
            qty = body.get("quantity", 1)
            batch = 900 + E.next_item if qty > 1 else None
            first = None
            for i in range(qty):
                item = E.add_item(id=E.next_item, printer_id=body.get("printer_id"), priority=body.get("priority", 50),
                                  position=(0 if body.get("insert_at_top") else 10) + i, batch_id=batch,
                                  library_file_id=body["library_file_id"],
                                  library_file_name=E.library[body["library_file_id"]]["filename"])
                first = first or item
            return 200, first
        if method == "POST" and path == "/queue/reorder":
            for it in body["items"]:
                if it["id"] in E.queue and E.queue[it["id"]]["status"] == "pending":
                    E.queue[it["id"]]["position"] = it["position"]
            return 200, {"message": "ok"}
        if method == "POST" and path == "/queue/preflight":
            E.preflights.append(body)
            rows = [{"printer_id": p["id"], "name": p["name"], "model": p["model"],
                     "reasons": list(E.reasons.get(p["name"], [])),
                     "eligible": not E.reasons.get(p["name"])} for p in E.printers]
            return 200, {"printers": rows, "eligible": [r["name"] for r in rows if r["eligible"]]}
        q = re.fullmatch(r"/queue/(\d+)(/\w+)?", path)
        if q:
            item = E.queue.get(int(q.group(1)))
            if item is None:
                return 404, {"detail": "Queue item not found"}
            action = q.group(2) or ""
            if method == "GET" and not action:
                return 200, item
            if method == "PATCH" and not action:
                item.update(body)
                item["printer_name"] = next((p["name"] for p in E.printers if p["id"] == item["printer_id"]), None)
                return 200, item
            if method == "DELETE" and not action:
                if item["status"] == "printing":
                    return 400, {"detail": "Cannot delete item that is currently printing"}
                del E.queue[item["id"]]
                return 200, {"message": "Queue item deleted"}
            if method == "POST" and action == "/cancel":
                item["status"] = "cancelled"
                return 200, {"message": "cancelled"}
            if method == "POST" and action == "/stop":
                item["status"] = "cancelled"
                return 200, {"message": "stopped"}
        return None

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_PATCH(self):
        self._route("PATCH")

    def do_DELETE(self):
        self._route("DELETE")


def printer_view(name):
    """A printer as the plugin shows it, from the mock farm."""
    row = next(p for p in Eng.printers if p["name"] == name)
    return wt.printer_view(row, Eng.status[row["id"]])


def make_3mf(plates=1, text=b"; gcode\nG28\n", filaments=None, nozzle=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        if filaments is not None or nozzle is not None:
            info = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
            for i in range(1, plates + 1):
                info.append("<plate>")
                info.append('<metadata key="index" value="%d"/>' % i)
                if nozzle is not None:
                    info.append('<metadata key="nozzle_diameters" value="%s"/>' % nozzle)
                for f in filaments or []:
                    info.append('<filament id="%s" type="%s" color="%s" used_m="%s" used_g="%s"/>'
                                % (f[0], f[1], f[2], "0" if float(f[3]) == 0 else "1.0", f[3]))
                info.append("</plate>")
            info.append("</config>")
            zf.writestr("Metadata/slice_info.config", "\n".join(info))
        for i in range(1, plates + 1):
            zf.writestr("Metadata/plate_%d.gcode" % i, text + b"; plate %d\n" % i)
    return buf.getvalue()


class Params:
    def __init__(self, **kw):
        self.dev_id = "openprinthq"
        self.host = "openprinthq.example"
        self.port = ""
        self.password = ""
        self.use_ssl = False
        self.project_name = ""
        self.task_name = ""
        self.preset_name = ""
        self.filename = ""
        self.dst_file = ""
        self.plate_index = 0
        self.__dict__.update(kw)


# ---------------------------------------------------------------- tests

class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        NOTIFICATIONS.clear(); WINDOWS.clear(); MODAL_ANSWER.clear(); Eng.reset()
        sys.modules.pop(wt.SYNC_BUS_NAME, None)  # each test starts with a fresh in-process sync bus
        wt.HUB._watch_stop.set()
        wt.HUB.__init__()
        self.events = []

    def agent(self, preset="Bambu Lab H2D 0.4 nozzle", mapping=None, connect=True, **cfg):
        BUNDLE.preset = FakePreset(preset, "openprinthq.example", "")
        sys.modules.pop(wt.SYNC_BUS_NAME, None)  # each test agent stands for its own computer
        a = wt.OpenPrintHQAgent()
        conf = {"printers": mapping if mapping is not None else {}, "status_poll_seconds": 60}
        if connect:
            conf.update({"ophq_url": self.base, "ophq_key": KEY})
        conf.update(cfg)
        a.get_config = lambda: json.dumps(conf)
        a.set_on_local_connect_fn(lambda s, d, m: self.events.append(("local_connect", s, d)))
        a.set_on_printer_connected_fn(lambda d: self.events.append(("connected", d)))
        a.set_on_local_message_fn(lambda d, msg: self.events.append(("msg", d, json.loads(msg))))
        return a

    def connected(self, preset="Bambu Lab H2D 0.4 nozzle", printer="H2D", **cfg):
        a = self.agent(preset, mapping={preset: printer}, **cfg)
        self.assertEqual(a.connect_printer(Params()), wt.SUCCESS, NOTIFICATIONS)
        return a

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def notes(self):
        return " | ".join(n[1] for n in NOTIFICATIONS)

    def queued(self):
        return [c[3] for c in Eng.posted("POST", r"/queue/")]


class AgentTests(ServerCase):
    def test_registers_agent_and_tab(self):
        wt.OpenPrintHQPrintQueuePlugin().register_capabilities()
        self.assertIn(wt.OpenPrintHQAgent, sys.modules["orca"].registered)
        self.assertIn(wt.OpenPrintHQTab, sys.modules["orca"].registered)
        info = wt.OpenPrintHQAgent().get_agent_info()
        self.assertEqual((info.id, info.name), ("openprinthq", "OpenPrintHQ"))

    def test_connect_with_mapping_dispatches_connected_and_status(self):
        a = self.connected()
        kinds = [e[0] for e in self.events]
        self.assertIn("local_connect", kinds)
        self.assertIn("connected", kinds)
        pushes = [e[2]["print"] for e in self.events if e[0] == "msg" and "print" in e[2]]
        self.assertEqual(pushes[0]["gcode_state"], "IDLE")          # busy reported idle by default
        self.assertEqual(pushes[0]["nozzle_temper"], 44.0)
        self.assertEqual(pushes[0]["bed_target_temper"], 55.0)
        self.assertEqual(pushes[0]["mc_percent"], 32)
        self.assertEqual(a._family, "bambu")
        self.assertEqual(Eng.calls[0][:2], ("GET", "/printers/"))

    def test_connect_falls_back_to_unique_name_match(self):
        a = self.agent("Original Prusa MK3S 0.6")
        self.assertEqual(a.connect_printer(Params()), wt.SUCCESS)
        self.assertEqual((a._printer, a._family), ("MK3S", "octoprint"))

    def test_connect_refuses_ambiguous_or_unknown_preset(self):
        self.assertEqual(self.agent("H2D and H2C combo").connect_printer(Params()), wt.ERR_CONNECTION)
        self.assertIn("matches several OpenPrintHQ printers", self.notes())
        self.assertEqual(self.agent("Voron 2.4").connect_printer(Params()), wt.ERR_CONNECTION)
        self.assertIn("no OpenPrintHQ printer name appears", self.notes())

    def test_connect_rejects_mapping_to_unknown_printer(self):
        a = self.agent(mapping={"Bambu Lab H2D 0.4 nozzle": "X1C"})
        self.assertEqual(a.connect_printer(Params()), wt.ERR_CONNECTION)
        self.assertIn("'X1C' is not in OpenPrintHQ", self.notes())

    def test_connect_bad_key(self):
        a = self.agent(mapping={"Bambu Lab H2D 0.4 nozzle": "H2D"}, ophq_key="ophqk_" + "b" * 43)
        self.assertEqual(a.connect_printer(Params()), wt.ERR_CONNECTION)
        self.assertIn("check the OpenPrintHQ API key", self.notes())

    def test_connect_without_settings(self):
        a = self.agent(connect=False)
        self.assertEqual(a.connect_printer(Params()), wt.ERR_INVALID_HANDLE)
        self.assertIn("enter the OpenPrintHQ address and an API key", self.notes())
        self.assertNotIn("Legacy farm", self.notes())

    def test_verify_job_is_a_ping_not_a_job(self):
        a = self.connected()
        self.assertEqual(a.start_send_gcode_to_sdcard(Params(project_name="verify_job"), None, None, None), wt.SUCCESS)
        self.assertFalse(Eng.posted("POST", r".*"))

    def test_print_now_to_bambu_uploads_3mf_and_queues_first(self):
        a = self.connected()
        stages = []
        path = self.write("part.gcode.3mf", make_3mf())
        rc = a.start_local_print(Params(project_name="Part.gcode.3mf", filename=path),
                                 lambda s, c, i: stages.append(s), lambda: False)
        self.assertEqual(rc, wt.SUCCESS, self.notes())
        up = Eng.posted("POST", r"/library/files")
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0][2], {"reuse_duplicate": "true"})
        self.assertIn(b'filename="Part.gcode.3mf"', up[0][3])
        self.assertEqual(self.queued(), [{"library_file_id": 41, "printer_id": 1, "priority": 100, "quantity": 1,
                                          "insert_at_top": True}])
        self.assertEqual(stages[-1], wt.STAGE_FINISHED)
        self.assertIn("Print now: job #", self.notes())
        self.assertIn("on H2D, priority 100", self.notes())

    def test_send_to_queue_for_octoprint_extracts_gcode(self):
        a = self.connected("Original Prusa MK3S 0.6", "MK3S")
        path = self.write("p.gcode.3mf", make_3mf(plates=2))
        rc = a.start_send_gcode_to_sdcard(Params(project_name="Plate", filename=path, plate_index=1), None,
                                          lambda: False, None)
        self.assertEqual(rc, wt.SUCCESS, self.notes())
        up = Eng.posted("POST", r"/library/files")[0][3]
        self.assertIn(b'filename="Plate.gcode"', up)
        self.assertIn(b"; plate 2\n", up)
        self.assertNotIn(b"PK\x03\x04", up)
        self.assertEqual(self.queued(), [{"library_file_id": 41, "printer_id": 2, "priority": 50, "quantity": 1}])

    def test_custom_priorities(self):
        a = self.connected(print_now_priority=80, queue_priority=20)
        path = self.write("a.gcode.3mf", make_3mf())
        a.start_local_print(Params(filename=path), None, None)
        a.start_send_gcode_to_sdcard(Params(filename=path, project_name="Other"), None, None, None)
        self.assertEqual([b["priority"] for b in self.queued()], [80, 20])

    def test_plain_gcode_to_bambu_is_refused_before_upload(self):
        a = self.connected()
        path = self.write("a.gcode", b"; plain gcode\n")
        self.assertEqual(a.start_local_print(Params(filename=path), None, None), wt.ERR_INVALID_RESULT)
        self.assertFalse(Eng.posted("POST", r".*"))
        self.assertIn("needs a .3mf", self.notes())

    def test_dst_file_takes_precedence(self):
        a = self.connected()
        good = self.write("good.gcode.3mf", make_3mf())
        self.assertEqual(a.start_local_print(Params(filename="/nope", dst_file=good), None, None), wt.SUCCESS)

    def test_missing_file(self):
        a = self.connected()
        self.assertEqual(a.start_local_print(Params(filename="/no/such/file.3mf"), None, None), wt.ERR_FILE_NOT_EXIST)

    def test_cancel_before_upload(self):
        a = self.connected()
        path = self.write("c.gcode.3mf", make_3mf())
        self.assertEqual(a.start_local_print(Params(filename=path), None, lambda: True), wt.ERR_CANCELED)
        self.assertFalse(Eng.posted("POST", r"/library/files"))

    def test_upload_failure_codes_differ_for_print_and_send(self):
        a = self.connected()
        Eng.fail[("POST", r"/library/files")] = (500, {"detail": "disk full"})
        path = self.write("u.gcode.3mf", make_3mf())
        self.assertEqual(a.start_local_print(Params(filename=path), None, None), wt.ERR_PRINT_UPLOAD)
        self.assertEqual(a.start_send_gcode_to_sdcard(Params(filename=path), None, None, None), wt.ERR_SEND_UPLOAD)
        self.assertIn("upload failed: disk full", self.notes())

    def test_read_only_key_gets_a_scope_hint(self):
        a = self.connected()
        Eng.fail[("POST", r"/queue/")] = (403, {"detail": "API key does not have 'can_queue' permission"})
        path = self.write("s.gcode.3mf", make_3mf())
        self.assertEqual(a.start_local_print(Params(filename=path), None, None), wt.ERR_PRINT_PUBLISH)
        self.assertIn("needs the submit scope", self.notes())

    def test_not_connected(self):
        a = self.agent()
        self.assertEqual(a.start_local_print(Params(filename="x"), None, None), wt.ERR_CONNECTION)

    def test_sdcard_print_unsupported(self):
        self.assertEqual(self.connected().start_sdcard_print(Params(), None, None), wt.ERR_INVALID_RESULT)

    def test_pause_resume_stop_act_on_the_printer(self):
        a = self.connected()
        for cmd in ("pause", "resume", "stop"):
            self.assertEqual(a.send_message("d", json.dumps({"print": {"command": cmd}}), 0, 0), wt.SUCCESS)
        self.assertEqual([c[1] for c in Eng.posted("POST", r"/printers/1/print/\w+")],
                         ["/printers/1/print/pause", "/printers/1/print/resume", "/printers/1/print/stop"])

    def test_pause_when_idle_does_nothing(self):
        a = self.connected("Original Prusa MK3S", "MK3S")
        self.assertEqual(a.send_message("d", json.dumps({"print": {"command": "pause"}}), 0, 0), wt.ERR_INVALID_RESULT)
        self.assertFalse(Eng.posted("POST", r".*"))

    def test_pause_forbidden_hints_control_scope(self):
        a = self.connected()
        Eng.scope = "submit"
        self.assertEqual(a.send_message("d", json.dumps({"print": {"command": "pause"}}), 0, 0), wt.ERR_PRINT_PUBLISH)
        self.assertIn("this key's scope is submit; this needs control", self.notes())
        self.assertNotIn("(the API key", self.notes())

    def test_get_version_and_pushall(self):
        a = self.connected()
        self.events.clear()
        a.send_message("d", json.dumps({"info": {"command": "get_version"}}), 0, 0)
        a.send_message("d", json.dumps({"print": {"command": "pushall"}}), 0, 0)
        kinds = [list(e[2].keys())[0] for e in self.events if e[0] == "msg"]
        self.assertEqual(kinds[:2], ["info", "print"])
        self.assertEqual(self.events[0][2]["info"]["module"][0]["product_name"], "OpenPrintHQ")

    def test_poller_pushes_status(self):
        a = self.agent(mapping={"Bambu Lab H2D 0.4 nozzle": "H2D"}, status_poll_seconds=2)
        a.connect_printer(Params())
        before = len([e for e in self.events if e[0] == "msg"])
        time.sleep(2.6)
        a.disconnect_printer()
        self.assertGreater(len([e for e in self.events if e[0] == "msg"]), before)


class KeyAndStatusTests(ServerCase):
    def test_resolve_api_key(self):
        self.assertEqual(wt.resolve_api_key(" " + KEY + " "), KEY)
        self.assertEqual(wt.resolve_api_key("bb_abcdefgh123"), "bb_abcdefgh123")
        self.assertEqual(wt.resolve_api_key("wt_" + "a" * 32), "")
        self.assertEqual(wt.resolve_api_key("openprinthq_virtual_printer:printer=MK3S"), "")
        self.assertEqual(wt.resolve_api_key(None), "")

    def test_engine_api_base(self):
        b = wt.engine_api_base
        self.assertEqual(b("openprinthq.com", KEY), "https://openprinthq.com/api/engine/api/v1")
        self.assertEqual(b("https://openprinthq.com/", KEY), "https://openprinthq.com/api/engine/api/v1")
        self.assertEqual(b("http://engine:8000", "bb_abcdefgh"), "http://engine:8000/api/v1")
        self.assertEqual(b("https://x.example/api/v1", KEY), "https://x.example/api/v1")
        self.assertEqual(b("https://x.example/api/engine", KEY), "https://x.example/api/engine/api/v1")
        self.assertEqual(b("", KEY), "")

    def test_normalize_ophq_url(self):
        n = wt.normalize_ophq_url
        self.assertEqual(n("openprinthq.com"), "https://openprinthq.com")
        self.assertEqual(n(" HTTPS://openprinthq.com/sub/?x=1 "), "https://openprinthq.com/sub")
        self.assertEqual(n(""), "")

    def test_engine_key_goes_straight_to_the_engine(self):
        Eng.key = "bb_enginekey123"
        c = wt.OpenPrintHQClient(self.base, "bb_enginekey123")
        self.assertEqual([p["name"] for p in c.printer_rows()], ["H2D", "MK3S", "MK4S", "H2C"])
        self.assertIsNone(c.me())

    def test_sign_in_page_is_explained(self):
        Eng.html = True
        with self.assertRaises(wt.OpenPrintHQError) as cm:
            wt.OpenPrintHQClient(self.base, KEY).printer_rows()
        self.assertIn("sign-in page answered", str(cm.exception))

    def test_error_text(self):
        t = wt._error_text
        self.assertEqual(t(b'{"detail": "Printer not found"}', 404), ("Printer not found", ""))
        self.assertEqual(t(b'{"detail": {"code": "unsupported_for_printer_type", "message": "no homing"}}', 400),
                         ("no homing", "unsupported_for_printer_type"))
        self.assertEqual(t(b'{"detail": [{"msg": "field required"}]}', 422)[0], "field required")
        self.assertEqual(t(b'{"error": "this key\'s scope is submit; this needs control"}', 403)[0],
                         "this key's scope is submit; this needs control")
        self.assertEqual(wt.scope_hint(wt.OpenPrintHQError("API key does not have 'can_queue' permission", 403), "submit"),
                         " (the API key needs the submit scope)")
        self.assertEqual(wt.scope_hint(wt.OpenPrintHQError("x", 500), "submit"), "")
        self.assertEqual(t(b"", 502, "Bad Gateway")[0], "Bad Gateway")

    def test_printer_view(self):
        h2d, mk3s, h2c = printer_view("H2D"), printer_view("MK3S"), printer_view("H2C")
        self.assertEqual((h2d["state"], h2d["busy"], h2d["file"], h2d["eta_minutes"]), ("Printing", True, "part", 94))
        self.assertEqual(h2d["layers"], {"current": 30, "total": 223})
        self.assertEqual(h2d["temperatures"]["bed"], {"current": 55.0, "target": 55.0})
        self.assertEqual((mk3s["state"], mk3s["busy"], mk3s["ready"], mk3s["file"]), ("Idle", False, False, None))
        self.assertEqual((h2c["state"], h2c["online"], h2c["ready"]), ("Offline", False, None))
        self.assertNotIn("serial_number", json.dumps(h2d))
        self.assertNotIn("ip_address", json.dumps(h2d))

    def test_busy_printer_reported_idle_by_default(self):
        p = wt.build_status_payload(printer_view("H2D"), busy_as_idle=True)["print"]
        self.assertEqual(p["gcode_state"], "IDLE")
        self.assertEqual(wt.build_status_payload(printer_view("H2D"))["print"]["gcode_state"], "RUNNING")

    def test_busy_setting_flows_to_pushes(self):
        a = self.connected(report_busy_as_idle=False)
        pushes = [e[2]["print"] for e in self.events if e[0] == "msg" and "print" in e[2]]
        self.assertEqual(pushes[0]["gcode_state"], "RUNNING")

    def test_requests_run_inline_by_default(self):
        """0.15.0: OFF_CONTEXT is off, so requests run on the calling (audited) thread."""
        names = []
        real = urllib.request.urlopen

        def spy(*a, **k):
            names.append(threading.current_thread().name)
            return real(*a, **k)
        urllib.request.urlopen = spy
        try:
            wt.OpenPrintHQClient(self.base, KEY).ping()
        finally:
            urllib.request.urlopen = real
        self.assertEqual(names, [threading.current_thread().name])


class ConfigPageTests(ServerCase):
    def data(self, a):
        html = a.get_config_ui()
        blob = html[html.index("var D = ") + len("var D = "):]
        return json.loads(blob[:blob.index(";\nvar OP")]), html

    def test_has_custom_ui(self):
        self.assertTrue(self.agent().has_config_ui())

    def test_page_lists_presets_and_live_printers(self):
        BUNDLE.extra = [FakePreset("Original Prusa MK3S", "x", "", agent="")]
        try:
            d, html = self.data(self.agent(mapping={"Bambu Lab H2D 0.4 nozzle": "H2D"}))
        finally:
            BUNDLE.extra = []
        self.assertTrue(d["ophq"]["ok"], d["ophq"])
        self.assertEqual(d["ophq"]["user"], "farm@example.com")
        self.assertEqual(d["ophq"]["scope"], "control")
        self.assertEqual([p["name"] for p in d["ophq"]["printers"]], ["H2D", "MK3S", "MK4S", "H2C"])
        self.assertEqual(d["ophq"]["api"], self.base + "/api/engine/api/v1")
        self.assertEqual([p["name"] for p in d["presets"]], ["Bambu Lab H2D 0.4 nozzle", "Original Prusa MK3S"])
        self.assertEqual(d["agent_id"], "openprinthq")
        self.assertIn("ophqk_", html)
        self.assertNotIn(KEY, html)
        self.assertNotIn("SECRET-1", html)
        self.assertNotIn("Legacy farm sign-in", html)

    def test_page_when_not_set_up(self):
        d, _ = self.data(self.agent(connect=False))
        self.assertFalse(d["ophq"]["ok"])
        self.assertIn("no OpenPrintHQ address yet", d["ophq"]["error"])
        d, _ = self.data(self.agent(connect=False, ophq_url=self.base))
        self.assertIn("no OpenPrintHQ API key yet", d["ophq"]["error"])

    def test_page_reports_bad_key(self):
        d, _ = self.data(self.agent(ophq_key="ophqk_" + "z" * 43))
        self.assertFalse(d["ophq"]["ok"])
        self.assertIn("check the OpenPrintHQ API key", d["ophq"]["error"])

    def test_page_survives_host_errors(self):
        a = self.agent()
        a._config_page_data = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        d, _ = self.data(a)
        self.assertIn("boom", d["ophq"]["error"])


class JobTests(ServerCase):
    """Duplicate uploads, copies, the job window, finished alerts and the tab's own jobs."""

    def send(self, a, data=None, **kw):
        path = self.write("j.gcode.3mf", data or make_3mf())
        return a.start_local_print(Params(filename=path, project_name=kw.pop("name", "Job")), None, None)

    def test_same_file_twice_uploads_once(self):
        a = self.connected()
        self.assertEqual(self.send(a), wt.SUCCESS)
        self.assertEqual(self.send(a), wt.SUCCESS)
        self.assertEqual(len(Eng.posted("POST", r"/library/files")), 1)
        self.assertEqual([b["library_file_id"] for b in self.queued()], [41, 41])
        self.assertIn("already uploaded from this OrcaSlicer session", self.notes())

    def test_identical_file_in_library_is_reused_by_hash(self):
        data = make_3mf(text=b"; same\n")
        Eng.library[7] = {"id": 7, "filename": "old.gcode.3mf", "file_hash": hashlib.sha256(data).hexdigest(),
                          "data": data}
        a = self.connected()
        self.assertEqual(self.send(a, data), wt.SUCCESS)
        self.assertFalse(Eng.posted("POST", r"/library/files"))
        self.assertEqual(self.queued()[0]["library_file_id"], 7)
        self.assertIn("identical file already in the OpenPrintHQ library", self.notes())

    def test_library_file_gone_falls_back_to_upload(self):
        data = make_3mf(text=b"; gone\n")
        a = self.connected()
        wt.HUB.remember_upload(hashlib.sha256(data).hexdigest(), "x", len(data), 999)
        self.assertEqual(self.send(a, data), wt.SUCCESS, self.notes())
        self.assertEqual(len(Eng.posted("POST", r"/library/files")), 1)
        self.assertEqual(self.queued()[-1]["library_file_id"], 41)

    def test_dedupe_off_uploads_without_reuse(self):
        a = self.connected(skip_duplicate_uploads=False)
        self.send(a)
        self.send(a)
        ups = Eng.posted("POST", r"/library/files")
        self.assertEqual(len(ups), 2)
        self.assertEqual(ups[0][2], {})
        self.assertFalse(Eng.posted("GET", r"/library/files/by-hash/.*"))

    def test_default_copies_sets_quantity_and_tracks_every_job(self):
        a = self.connected(default_copies=3)
        self.assertEqual(self.send(a), wt.SUCCESS)
        self.assertEqual(self.queued()[0]["quantity"], 3)
        self.assertEqual(len(wt.HUB.jobs), 3)
        self.assertIn(" x3 on H2D", self.notes())

    def test_job_window_opens_with_job_ids_and_can_be_disabled(self):
        a = self.connected()
        self.send(a)
        self.assertEqual(len(WINDOWS), 1)
        self.assertEqual(WINDOWS[0].title, "OpenPrintHQ")
        self.assertIn('"Printing next on H2D"', WINDOWS[0].html)
        WINDOWS.clear()
        self.send(self.connected(show_job_window=False))
        self.assertFalse(WINDOWS)

    def test_finished_failed_cancelled_alerts_once(self):
        a = self.connected()
        self.send(a)
        jid = next(iter(wt.HUB.jobs))
        for status, want in (("completed", "finished on H2D"), ("failed", "failed on H2D: nozzle clog"),
                             ("cancelled", "was cancelled")):
            NOTIFICATIONS.clear()
            wt.HUB.jobs[jid].update(state="queued", notified=False)
            wt.HUB.update_job(jid, {"status": status, "assigned_printer": "H2D", "error_message": "nozzle clog"})
            wt.HUB.update_job(jid, {"status": status, "assigned_printer": "H2D", "error_message": "nozzle clog"})
            self.assertEqual(len(NOTIFICATIONS), 1, NOTIFICATIONS)
            self.assertIn(want, NOTIFICATIONS[0][1])

    def test_alerts_can_be_turned_off(self):
        a = self.connected(notify_job_events=False)
        self.send(a)
        NOTIFICATIONS.clear()
        wt.HUB.update_job(next(iter(wt.HUB.jobs)), {"status": "completed"})
        self.assertFalse(NOTIFICATIONS)

    def test_watch_maps_engine_items(self):
        c = wt.OpenPrintHQClient(self.base, KEY)
        self.assertEqual(c.job(5)["status"], "printing")
        self.assertEqual(c.job(5)["assigned_printer"], "MK3S")
        Eng.status[2]["state"] = "PAUSE"
        self.assertEqual(c.job(5)["status"], "paused")
        self.assertEqual(c.job(6)["status"], "waiting_filament")
        Eng.queue[6]["status"] = "skipped"
        self.assertEqual(wt.job_state(c.job(6)["status"]), "cancelled")
        with self.assertRaises(wt.OpenPrintHQError) as cm:
            c.job(4242)
        self.assertEqual(cm.exception.code, 404)

    def test_add_copies_from_window_submits_same_file(self):
        a = self.connected()
        self.send(a)
        jid = next(iter(wt.HUB.jobs))
        text = wt.HUB._do({"cmd": "copies", "id": jid, "n": 2})
        self.assertIn("Added 2 copies on H2D", text)
        self.assertEqual(self.queued()[-1], {"library_file_id": 41, "printer_id": 1, "priority": 100, "quantity": 2})
        self.assertEqual(len(wt.HUB.jobs), 3)

    def test_cancel_waiting_job_and_stop_printing_one(self):
        self.connected()
        wt.HUB._do({"cmd": "cancel", "id": 1})
        wt.HUB._do({"cmd": "cancel", "id": 5})
        self.assertEqual(Eng.posted("POST", r"/queue/1/cancel")[0][3], {"reason": "Cancelled from OrcaSlicer",
                                                                      "by": "OrcaSlicer"})
        self.assertTrue(Eng.posted("POST", r"/queue/5/stop"))
        Eng.queue[5]["status"] = "printing"
        wt.HUB._do({"cmd": "pause", "id": 5})
        self.assertTrue(Eng.posted("POST", r"/printers/2/print/pause"))
        with self.assertRaises(wt.OpenPrintHQError):
            wt.HUB._do({"cmd": "pause", "id": 1})

    def test_tab_posts_snapshot_with_printers_and_jobs(self):
        a = self.connected()
        self.send(a)
        tab = wt.OpenPrintHQTab()
        done = threading.Event()
        tab.post_message = lambda m: (tab.posted.append(m), done.set())
        tab.posted = []
        tab.on_message({"cmd": "hello"})
        self.assertTrue(done.wait(5))
        time.sleep(0.3)
        snap = [m for m in tab.posted if m.get("type") == "state"][-1]
        self.assertEqual([p["name"] for p in snap["printers"]], ["H2D", "MK3S", "MK4S", "H2C"])
        self.assertEqual(snap["jobs"][0]["printer"], "H2D")
        self.assertTrue(snap["signin"]["configured"])
        self.assertIn(5, [r["id"] for r in snap["queue"] or []])
        self.assertEqual(tab.get_name(), "OpenPrintHQ")

    def test_tab_without_connection_reports_setup_error(self):
        self.agent(connect=False)
        snap = (wt.HUB._do({"cmd": "refresh"}), wt.HUB.snapshot())[1]
        self.assertIn("add the OpenPrintHQ address and an API key", snap["error"])
        self.assertFalse(snap["signin"]["configured"])


class FarmTests(ServerCase):
    """AMS and spool sync, the pre-send check, the farm queue, Set ready and nozzles."""

    def tab(self, msg):
        return wt.HUB._do(msg)

    def test_h2d_ams_payload_from_engine_status(self):
        out = wt.ams_payload_from_status(Eng.status[1])
        ams = out["ams"]
        self.assertEqual([u["id"] for u in ams["ams"]], ["0", "128"])
        self.assertEqual(ams["ams_exist_bits"], "11")          # AMS 0 and the AMS HT (bit 4)
        self.assertEqual(ams["tray_exist_bits"], "10007")      # trays 0-2 and the HT's tray (bit 16)
        self.assertEqual(ams["ams"][0]["tray"][0]["id"], "0")
        self.assertNotIn("tag_uid", ams["ams"][0]["tray"][0])
        self.assertEqual(ams["ams"][0]["humidity"], "4")
        self.assertNotIn("vt_tray", out)                       # the external spool holder is empty

    def test_external_spool_from_engine_status(self):
        st = dict(Eng.status[1], ams=[], vt_tray=[{"id": 254, "tray_type": "PLA", "tray_color": "FF0000FF"}])
        self.assertEqual(wt.ams_payload_from_status(st)["vt_tray"]["tray_type"], "PLA")

    def test_prusa_spool_becomes_external_tray(self):
        rows = wt.OpenPrintHQClient(self.base, KEY).assignments("MK4S")
        self.assertEqual(rows[0]["slot_type"], "external")
        self.assertEqual(rows[0]["remaining_percentage"], 75)
        vt = wt.ams_payload_from_assignments(rows)["vt_tray"]
        self.assertEqual((vt["id"], vt["tray_type"], vt["tray_color"]), ("254", "ABS", "000000FF"))

    def test_printer_extras_for_h2d_and_mk4s(self):
        self.agent()
        h2d = wt.HUB.printer_extras("H2D", "Bambu Lab H2D")
        self.assertEqual((h2d["printer_type"], h2d["nozzle_diameter"]), ("O1D", "0.4"))
        self.assertIn("ams", h2d)
        mk4s = wt.HUB.printer_extras("MK4S", "Prusa MK4S")
        self.assertEqual(mk4s["vt_tray"]["tray_type"], "ABS")
        self.assertNotIn("ams", mk4s)

    def test_ams_sync_off_sends_no_slots(self):
        self.agent(ams_sync=False)
        self.assertNotIn("ams", wt.HUB.printer_extras("H2D", "Bambu Lab H2D"))

    def test_status_push_carries_ams(self):
        a = self.connected()
        a._printer_model = "Bambu Lab H2D"
        a._refresh_extras("H2D", force=True)
        a.send_message("d", json.dumps({"print": {"command": "pushall"}}), 0, 0)
        push = [e[2]["print"] for e in self.events if e[0] == "msg" and "print" in e[2]][-1]
        self.assertIn("ams", push)
        self.assertEqual(push["printer_type"], "O1D")

    def test_filament_sync_mode_follows_connection(self):
        self.assertEqual(self.agent().get_filament_sync_mode(), "subscription")
        self.assertEqual(self.agent(connect=False).get_filament_sync_mode(), "none")
        self.assertEqual(self.agent(ams_sync=False).get_filament_sync_mode(), "none")

    def test_preflight_blocks_and_names_printers_of_the_same_model(self):
        Eng.printers.append(dict(Eng.printers[0], id=5, name="H2D-2"))
        Eng.status[5] = dict(Eng.status[1], state="IDLE")
        Eng.reasons = {"H2D": ["missing filament: PETG", "busy"], "MK3S": [], "H2C": ["offline"]}
        self.agent()
        out = wt.HUB.preflight("H2D", [{"type": "PETG"}], [0.4])
        self.assertEqual(out["issues"], ["H2D: missing filament: PETG."])
        self.assertEqual(out["alternatives"], ["H2D-2"])      # MK3S is another model, H2C is offline
        self.assertEqual(Eng.preflights[-1], {"filament_types": ["PETG"], "skip_filament_check": False,
                                              "nozzle_diameter": 0.4})

    def test_busy_offline_and_plate_are_not_problems(self):
        Eng.reasons = {"H2D": ["busy", "plate not cleared", "offline"]}
        self.agent()
        self.assertEqual(wt.HUB.preflight("H2D", [{"type": "PLA"}], [0.4])["issues"], [])

    def test_preflight_blocks(self):
        b = wt.preflight_blocks
        self.assertTrue(b("out of service: new hotend"))
        self.assertFalse(b("out of service: x", {"maintenance_check": False}))
        self.assertTrue(b("needs a 0.6mm nozzle (has 0.4mm)"))
        self.assertFalse(b("needs a 0.6mm nozzle (has 0.4mm)", {"nozzle_check": False}))
        self.assertTrue(b("needs a hardened nozzle for PLA-CF (has brass)", {"nozzle_check": False}))
        self.assertTrue(b("file sliced for X1C"))
        self.assertFalse(b("plate not cleared"))

    def test_checks_follow_the_settings(self):
        self.agent(nozzle_check=False, filament_check=False)
        wt.HUB.preflight("H2D", [{"type": "PETG"}], [0.4])
        self.assertEqual(Eng.preflights[-1], {"filament_types": [], "skip_filament_check": True})

    def test_problem_asks_and_cancel_stops_the_job(self):
        Eng.reasons = {"H2D": ["out of service: new hotend"]}
        a = self.connected()
        MODAL_ANSWER.update(action="")
        path = self.write("p.gcode.3mf", make_3mf(filaments=[("1", "PLA", "#FF0000", "3.1")], nozzle="0.4"))
        self.assertEqual(a.start_local_print(Params(filename=path), None, None), wt.ERR_CANCELED)
        self.assertFalse(self.queued())
        self.assertIn("H2D: out of service: new hotend.", WINDOWS[0].html)
        self.assertEqual(WINDOWS[0].title, "OpenPrintHQ check")

    def test_problem_send_anyway_to_another_printer(self):
        Eng.printers.append(dict(Eng.printers[0], id=5, name="H2D-2"))
        Eng.status[5] = dict(Eng.status[1], state="IDLE")
        Eng.reasons = {"H2D": ["needs a 0.6mm nozzle (has 0.4mm)"]}
        a = self.connected()
        MODAL_ANSWER.update(action="send", printer="H2D-2")
        path = self.write("p.gcode.3mf", make_3mf(filaments=[("1", "PLA", "#FF0000", "3.1")], nozzle="0.6"))
        self.assertEqual(a.start_local_print(Params(filename=path), None, None), wt.SUCCESS, self.notes())
        self.assertEqual(self.queued()[0]["printer_id"], 5)

    def test_all_clear_sends_without_asking(self):
        a = self.connected()
        path = self.write("p.gcode.3mf", make_3mf(filaments=[("1", "PLA", "#FF0000", "3.1")], nozzle="0.4"))
        self.assertEqual(a.start_local_print(Params(filename=path), None, None), wt.SUCCESS)
        self.assertFalse([w for w in WINDOWS if w.title == "OpenPrintHQ check"])
        self.assertEqual(Eng.preflights[0]["filament_types"], ["PLA"])

    def test_checks_off_never_call_preflight(self):
        a = self.connected(nozzle_check=False, filament_check=False, maintenance_check=False)
        path = self.write("p.gcode.3mf", make_3mf(filaments=[("1", "PLA", "#FF0000", "3.1")], nozzle="0.4"))
        self.assertEqual(a.start_local_print(Params(filename=path), None, None), wt.SUCCESS)
        self.assertFalse(Eng.preflights)

    def test_submit_key_skips_the_check_with_a_warning_and_still_prints(self):
        Eng.scope = "submit"
        a = self.connected()
        path = self.write("p.gcode.3mf", make_3mf(filaments=[("1", "PLA", "#FF0000", "3.1")], nozzle="0.4"))
        self.assertEqual(a.start_local_print(Params(filename=path), None, None), wt.SUCCESS)
        self.assertIn("Pre-send check unavailable: this key's scope is submit; this needs control", self.notes())
        self.assertEqual(len(self.queued()), 1)

    def test_low_stock_warning(self):
        self.agent()
        out = wt.HUB.preflight("H2D", [{"type": "PLA"}], [])
        self.assertEqual(out["warnings"], ["Low stock: Polymaker PLA Red, 50 g left."])
        self.assertEqual(wt.HUB.preflight("H2D", [{"type": "PETG"}], [])["warnings"], [])

    def test_queue_position_in_notification(self):
        Eng.add_item(id=50, printer_id=1, priority=100, position=0, library_file_name="ahead.gcode.3mf")
        a = self.connected(queue_priority=50)
        path = self.write("q.gcode.3mf", make_3mf())
        a.start_send_gcode_to_sdcard(Params(filename=path, project_name="Later"), None, None, None)
        self.assertIn("2nd in line for H2D (of 2 waiting)", self.notes())

    def test_tab_shows_farm_queue_and_alerts(self):
        Eng.out_of_service = [{"printer_id": 4, "name": "H2C", "reason": "new hotend"}]
        self.agent()
        self.tab({"cmd": "refresh"})
        snap = wt.HUB.snapshot()
        rows = snap["queue"]
        self.assertEqual([r["id"] for r in rows], [5, 6, 1])           # printing first, then priority
        self.assertEqual(rows[1]["status"], "waiting_filament")
        self.assertEqual(rows[1]["materials"], ["PETG"])
        self.assertEqual(rows[1]["printer"], "any H2D")
        self.assertEqual(rows[2]["hours"], 2.8)
        self.assertEqual(snap["alerts"]["down"], ["H2C: new hotend"])
        self.assertEqual(snap["alerts"]["low"], ["Polymaker PLA Red"])
        self.assertEqual(snap["alerts"]["stats"], {"printing": 1, "queued": 1, "assigned": 1})
        self.assertEqual(snap["nozzles"]["MK3S"], [0.6])
        self.assertEqual(snap["signin"]["ok"], True)

    def test_tab_priority_and_print_now(self):
        self.agent()
        self.assertIn("priority set to 80", self.tab({"cmd": "priority", "id": 1, "priority": 80}))
        self.assertEqual(Eng.posted("PATCH", r"/queue/1")[0][3], {"priority": 80})
        Eng.add_item(id=60, printer_id=1, position=1)
        self.assertIn("first in line on H2D", self.tab({"cmd": "print_now", "id": 6, "printer": "H2D"}))
        self.assertEqual(Eng.posted("PATCH", r"/queue/6")[0][3], {"printer_id": 1, "target_model": None,
                                                                 "priority": 100})
        self.assertEqual(Eng.posted("POST", r"/queue/reorder")[-1][3]["items"],
                         [{"id": 6, "position": 1}, {"id": 60, "position": 2}])
        self.assertFalse(Eng.posted("POST", r"/printers/1/clear-plate"))
        with self.assertRaises(wt.OpenPrintHQError):
            self.tab({"cmd": "priority", "id": 1, "priority": 101})

    def test_clear_bed_and_print_pins_first_then_clears_the_plate(self):
        self.agent()
        text = self.tab({"cmd": "clear_print", "id": 1, "printer": "MK3S"})
        self.assertIn("Bed marked clear on MK3S", text)
        order = [(c[0], c[1]) for c in Eng.calls if c[0] != "GET"]
        self.assertEqual(order[-3:], [("PATCH", "/queue/1"), ("POST", "/queue/reorder"),
                                      ("POST", "/printers/2/clear-plate")])
        self.assertFalse(Eng.status[2]["awaiting_plate_clear"])

    def test_clear_bed_and_print_on_a_printer_that_is_already_clear(self):
        self.agent()
        text = self.tab({"cmd": "clear_print", "id": 6, "printer": "MK4S"})
        self.assertIn("Job #6 is first in line on MK4S; the bed was not marked clear: Printer is not awaiting", text)
        self.assertEqual(Eng.queue[6]["printer_id"], 3)

    def test_tab_reorder_sends_positions_and_rejects_bad_orders(self):
        self.agent()
        self.assertEqual(self.tab({"cmd": "reorder", "order": [1, 6]}), "Queue order saved.")
        self.assertEqual(Eng.posted("POST", r"/queue/reorder")[0][3],
                         {"items": [{"id": 1, "position": 1}, {"id": 6, "position": 2}]})
        for bad in ([1], [1, 1]):
            with self.assertRaises(wt.OpenPrintHQError):
                self.tab({"cmd": "reorder", "order": bad})

    def test_remove_deletes_a_waiting_job_and_refuses_a_printing_one(self):
        self.agent()
        self.assertIn("removed", self.tab({"cmd": "remove", "id": 1}))
        self.assertNotIn(1, Eng.queue)
        with self.assertRaises(wt.OpenPrintHQError) as cm:
            self.tab({"cmd": "remove", "id": 5})
        self.assertIn("use Cancel", str(cm.exception))

    def test_set_ready_clears_the_plate(self):
        self.agent()
        self.assertIn("MK3S marked ready", self.tab({"cmd": "set_ready", "printer": "MK3S"}))
        self.assertTrue(Eng.posted("POST", r"/printers/2/clear-plate"))
        Eng.scope = "submit"
        with self.assertRaises(wt.OpenPrintHQError) as cm:
            self.tab({"cmd": "set_ready", "printer": "MK3S"})
        self.assertIn("control scope", str(cm.exception))

    def test_set_nozzle_puts_only_the_changed_extruder(self):
        self.agent()
        self.assertIn("H2C nozzle set to 0.4 / 0.6 mm", self.tab({"cmd": "set_nozzle", "printer": "H2C",
                                                                  "sizes": [0.4, 0.6]}))
        puts = Eng.posted("PUT", r"/printers/4/nozzles/\d")
        self.assertEqual([(c[1], c[3]) for c in puts], [("/printers/4/nozzles/1", {
            "diameter_mm": 0.6, "nozzle_type": "undefine", "volume_type": "Standard"})])
        with self.assertRaises(wt.OpenPrintHQError):
            self.tab({"cmd": "set_nozzle", "printer": "H2C", "sizes": [0.4]})
        with self.assertRaises(wt.OpenPrintHQError):
            self.tab({"cmd": "set_nozzle", "printer": "MK3S", "sizes": [5]})

    def test_set_nozzle_refused_while_printing(self):
        self.agent()
        with self.assertRaises(wt.OpenPrintHQError) as cm:
            self.tab({"cmd": "set_nozzle", "printer": "H2D", "sizes": [0.6, 0.6]})
        self.assertIn("is printing", str(cm.exception))
        self.assertFalse(Eng.posted("PUT", r".*"))

    def test_bambu_nozzle_keeps_type_and_flow(self):
        self.agent()
        Eng.status[1]["state"] = "IDLE"
        self.tab({"cmd": "set_nozzle", "printer": "H2D", "sizes": [0.6, 0.4]})
        self.assertEqual(Eng.posted("PUT", r"/printers/1/nozzles/0")[0][3],
                         {"diameter_mm": 0.6, "nozzle_type": "hardened_steel", "volume_type": "High Flow"})

    def test_tab_markup(self):
        script = wt.TAB_HTML[wt.TAB_HTML.index("<script>"):]
        for text in ("Set in OpenPrintHQ", "Set ready", "Clear bed & print", "Print now on", "Needs filament"):
            self.assertIn(text, script)
        self.assertNotIn("Legacy farm", wt.TAB_HTML.replace("data-skin=legacy_farm", ""))


class MigrationTests(ServerCase):
    """0.12.0: Legacy farm settings from 0.11 and older."""

    def setUp(self):
        super().setUp()
        self.saved_store = wt.SECRETS
        wt.SECRETS = wt.SecretStore("openprinthq-print-queue", platform="test")
        wt.SECRETS._impl = wt.MemorySecretBackend()

    def tearDown(self):
        wt.SECRETS = self.saved_store
        super().tearDown()

    def test_legacy_farm_secrets_are_deleted_once_and_settings_ignored(self):
        wt.SECRETS.set("api_key", "wt_" + "a" * 32)
        wt.SECRETS.set("wt_password", "hunter2")
        a = self.agent(connect=False, base_url="https://watch.example", api_key="", wt_username="farm",
                       wt_password="", match_filament=True, queue_priority=42)
        written = []
        a.save_config = lambda text: written.append(json.loads(text)) or True
        cfg = a._config()
        self.assertIsNone(wt.SECRETS.get("api_key"))
        self.assertIsNone(wt.SECRETS.get("wt_password"))
        for k in wt.LEGACY_KEYS:
            self.assertNotIn(k, cfg)
        self.assertEqual(cfg["queue_priority"], 42)
        self.assertTrue(a._legacy_found)
        self.assertEqual(a.connect_printer(Params()), wt.ERR_INVALID_HANDLE)
        self.assertIn("Legacy farm support was removed", self.notes())
        html = a.get_config_ui()
        self.assertIn('"legacy": true', html)
        wt.SECRETS.set("api_key", "something new")   # the second read does not touch the store again
        a._config()
        self.assertEqual(wt.SECRETS.get("api_key"), "something new")

    def test_plaintext_legacy_farm_secrets_are_blanked(self):
        a = self.agent(connect=False, api_key="wt_" + "b" * 32, wt_password="pw")
        written = []
        a.save_config = lambda text: written.append(json.loads(text)) or True
        a._config()
        self.assertTrue(written)
        self.assertEqual((written[0]["api_key"], written[0]["wt_password"]), ("", ""))
        self.assertNotIn("wt_" + "b" * 32, json.dumps(written))

    def test_new_install_has_no_legacy_notice(self):
        a = self.agent(connect=False)
        a.save_config = lambda text: True
        a._config()
        self.assertFalse(getattr(a, "_legacy_found", False))
        self.assertIn('"legacy": false', a.get_config_ui())

    def test_key_moves_to_the_credential_store_and_still_prints(self):
        a = self.agent("Original Prusa MK3S", mapping={"Original Prusa MK3S": "MK3S"})
        written = []
        a.save_config = lambda text: written.append(json.loads(text)) or True
        cfg = a._config()
        self.assertEqual(cfg["ophq_key"], KEY)
        self.assertEqual(written[-1]["ophq_key"], "")
        self.assertEqual(wt.SECRETS.get("ophq_key"), KEY)
        a.get_config = lambda: json.dumps(written[-1])
        self.assertEqual(a._config()["ophq_key"], KEY)
        self.assertEqual(a.connect_printer(Params()), wt.SUCCESS)
        self.assertNotIn(KEY, json.dumps(written))

    def test_config_page_reports_where_secrets_live(self):
        a = self.agent()
        a.save_config = lambda text: True
        html = a.get_config_ui()
        self.assertIn('"backend": "test store"', html)
        self.assertIn('"ophq_key": "store"', html)
        self.assertNotIn(KEY, html)


class HelperTests(unittest.TestCase):
    def test_job_state(self):
        js = wt.job_state
        self.assertEqual([js("queued"), js("printing"), js("paused"), js("completed"), js("failed"), js("cancelled"),
                          js("waiting_filament"), js("held"), js(None)],
                         ["queued", "active", "active", "done", "failed", "cancelled", "queued", "queued", "queued"])

    def test_item_status(self):
        s = wt.item_status
        self.assertEqual(s({"status": "pending", "printer_id": 2}), "assigned")
        self.assertEqual(s({"status": "pending"}), "queued")
        self.assertEqual(s({"status": "pending", "manual_start": True}), "held")
        self.assertEqual(s({"status": "pending", "waiting_reason": "Waiting for filament"}), "waiting_filament")
        self.assertEqual(s({"status": "skipped"}), "cancelled")

    def test_item_materials(self):
        self.assertEqual(wt.item_materials({"required_filament_types": '["PLA", "petg"]', "filament_type": "PLA"}),
                         ["PLA", "PETG"])
        self.assertEqual(wt.item_materials({"filament_type": "ABS,ASA"}), ["ABS", "ASA"])
        self.assertEqual(wt.item_materials({}), [])

    def test_queue_note(self):
        qn = wt.queue_note
        self.assertIn("about 1 h 34 min left", qn("H2D", printer_view("H2D")))
        self.assertIn("free", qn("MK3S", printer_view("MK3S")))
        self.assertIn("offline", qn("H2C", printer_view("H2C")))
        self.assertIn("2 earlier jobs from this OrcaSlicer are ahead", qn("MK3S", printer_view("MK3S"), 2))
        self.assertEqual(qn("X", None), "")

    def test_queue_position(self):
        rows = [{"id": 1, "printer": "MK4S", "state": "queued", "priority": 50, "position": 1},
                {"id": 9, "printer": "MK4S", "state": "queued", "priority": 100, "position": 5},
                {"id": 5, "printer": "MK4S", "state": "active", "priority": 50}]
        self.assertEqual(wt.queue_position(rows, "MK4S", 1), (2, 2))
        self.assertEqual(wt.queue_position(rows, "MK4S", 9), (1, 2))

    def test_minutes_text(self):
        self.assertEqual([wt.minutes_text(0.2), wt.minutes_text(45), wt.minutes_text(125), wt.minutes_text(None)],
                         ["under a minute", "45 min", "2 h 05 min", ""])

    def test_pages_html_has_no_unfilled_markers(self):
        self.assertNotIn("__WT", wt.TAB_HTML)
        self.assertIn("__WT_INIT__", wt.JOB_WINDOW_HTML)

    def test_plate_nozzles(self):
        self.assertEqual(wt.plate_nozzles(make_3mf(nozzle="0.4")), [0.4])
        self.assertEqual(wt.plate_nozzles(make_3mf(plates=2, nozzle="0.6,0.6"), 2), [0.6, 0.6])
        self.assertEqual(wt.plate_nozzles(make_3mf()), [])

    def test_plate_filaments_skips_unused(self):
        data = make_3mf(filaments=[("1", "PLA", "#FF0000", "3.2"), ("2", "PETG", "#00FF00", "0")])
        self.assertEqual([f["type"] for f in wt.plate_filaments(data)], ["PLA"])

    def test_model_ids(self):
        self.assertEqual(wt.model_id_for("Bambu Lab H2C"), "O1C2")
        self.assertEqual(wt.model_id_for("Prusa MK4S HF"), "MK4S HF")

    def test_status_offline_is_idle_without_telemetry(self):
        p = wt.build_status_payload(printer_view("H2C"))["print"]
        self.assertEqual(p["gcode_state"], "IDLE")
        self.assertNotIn("nozzle_temper", p)
        self.assertNotIn("mc_percent", p)

    def test_state_map(self):
        for state, orca_state in (("Paused", "PAUSE"), ("Finished", "FINISH"), ("Error", "FAILED"),
                                  ("Preparing", "PREPARE"), ("Idle", "IDLE"), ("Some Other State", "IDLE")):
            p = dict(printer_view("MK3S"), state=state)
            self.assertEqual(wt.build_status_payload(p)["print"]["gcode_state"], orca_state, state)

    def test_normalize_base_url(self):
        n = wt.normalize_base_url
        self.assertEqual(n("openprinthq.com"), "https://openprinthq.com")
        self.assertEqual(n("192.168.1.40", "8080"), "http://192.168.1.40:8080")
        self.assertEqual(n(""), "")

    def test_unquote(self):
        self.assertEqual(wt._unquote('"https://x"'), "https://x")
        self.assertEqual(wt._unquote(None), "")

    def test_job_basename(self):
        P = Params
        self.assertEqual(wt.job_basename(P(project_name="Bracket.gcode.3mf"), "x"), "Bracket")
        self.assertEqual(wt.job_basename(P(project_name="verify_job", task_name="T"), "x"), "T")
        self.assertEqual(wt.job_basename(P(), "/tmp/orcaslicer_model/.4242.0.3mf"), "orca-print")
        self.assertEqual(wt.job_basename(P(preset_name='a/b:c"d_plate_1'), "x"), "a_b_c_d_plate_1")

    def test_file_kind(self):
        self.assertEqual(wt.file_kind(make_3mf()), "3mf")
        self.assertEqual(wt.file_kind(b"GCDE\x01\x00"), "bgcode")
        self.assertEqual(wt.file_kind(b"; gcode"), "gcode")

    def test_extract_plate(self):
        data = make_3mf(plates=3)
        self.assertTrue(wt.extract_plate_gcode(data, 2).endswith(b"; plate 3\n"))
        self.assertIsNone(wt.extract_plate_gcode(data, None))  # ambiguous
        self.assertTrue(wt.extract_plate_gcode(make_3mf(), None).endswith(b"; plate 1\n"))

    def test_routing_printer_parse(self):
        self.assertEqual(wt.routing_printer("openprinthq_virtual_printer:printer=MK3S,queue=true"), "MK3S")
        self.assertEqual(wt.routing_printer(KEY), "")

    def test_resolve_prefers_mapping_then_routing_then_name(self):
        r = wt.resolve_printer
        names = ["H2D", "MK3S", "MK4S"]
        self.assertEqual(r("My MK3S", {"My MK3S": "MK4S"}, names, "H2D"), ("MK4S", "mapping"))
        self.assertEqual(r("Something", {}, names, "mk3s"), ("MK3S", "routing string"))
        self.assertEqual(r("Prusa MK4S 0.4", {}, names), ("MK4S", "name match"))


class SslContextTests(unittest.TestCase):
    """macOS Orca's embedded Python has no CA store; the plugin must still verify HTTPS."""

    def setUp(self):
        import ssl as _ssl
        self._ssl = _ssl
        self._orig = (wt.ssl.create_default_context, wt.SYSTEM_CA_FILES, wt._SSL_CONTEXT)
        wt._SSL_CONTEXT = None

    def tearDown(self):
        wt.ssl.create_default_context, wt.SYSTEM_CA_FILES, wt._SSL_CONTEXT = self._orig

    def test_bundled_roots_parse(self):
        ctx = self._ssl.SSLContext(self._ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cadata=wt.BUNDLED_ROOTS_PEM)
        self.assertEqual(wt._ca_count(ctx), 2)

    def test_empty_store_still_verifies(self):
        wt.ssl.create_default_context = lambda: self._ssl.SSLContext(self._ssl.PROTOCOL_TLS_CLIENT)
        wt.SYSTEM_CA_FILES = ("/nonexistent/cert.pem",)
        ctx = wt.ssl_context()
        self.assertGreaterEqual(wt._ca_count(ctx), 2)
        self.assertEqual(ctx.verify_mode, self._ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_context_is_cached(self):
        self.assertIs(wt.ssl_context(), wt.ssl_context())


class OffContextTests(unittest.TestCase):
    """0.8.2 / 0.3.2: network work on a plugin thread, for old nightlies. Off by default since 0.15.0."""

    def setUp(self):
        self.saved = wt.OFF_CONTEXT
        wt.OFF_CONTEXT = True

    def tearDown(self):
        wt.OFF_CONTEXT = self.saved

    def test_runs_on_another_thread_and_returns_the_value(self):
        here = threading.current_thread()
        seen = wt.off_context(lambda: threading.current_thread())
        self.assertIsNot(seen, here)
        self.assertEqual(seen.name, "nn-offcontext")

    def test_errors_come_back_with_their_type(self):
        def boom():
            raise KeyError("x")
        with self.assertRaises(KeyError):
            wt.off_context(boom)

    def test_nested_calls_stay_on_the_same_worker(self):
        outer = wt.off_context(lambda: (threading.current_thread(), wt.off_context(threading.current_thread)))
        self.assertIs(outer[0], outer[1])

    def test_off_by_default(self):
        self.assertFalse(self.saved)

    def test_switch_off_runs_inline(self):
        wt.OFF_CONTEXT = False
        self.assertIs(wt.off_context(threading.current_thread), threading.current_thread())


class TabDropdownTests(unittest.TestCase):
    """0.10.1: the tab's dropdowns are HTML lists; native <select> popups did not open in Orca's webview."""

    def tab_script(self):
        html = wt.TAB_HTML
        return html[html.index("<script>"):]

    def test_no_native_select_in_tab(self):
        self.assertNotIn('el("select"', self.tab_script())

    def test_dropdown_not_inside_a_label(self):
        # A label re-sends clicks inside it to its first button, which reopened the list after a pick.
        self.assertNotIn('el("label"', self.tab_script())

    def test_busy_printer_says_why_nozzle_is_locked(self):
        self.assertIn('"locked while "', self.tab_script())

    def test_close_does_not_rerender(self):
        script = self.tab_script()
        close = script[script.index("function ddClose"):]
        self.assertNotIn("render(", close[:close.index("\n")])


class SecretStoreTests(unittest.TestCase):
    """0.7.0: secrets live in the OS credential store, not Orca's plain-text config."""

    def store(self):
        s = wt.SecretStore("openprinthq-print-queue", platform="test")
        s._impl = wt.MemorySecretBackend()
        return s

    def test_secret_keys(self):
        self.assertEqual(wt.SECRET_KEYS, ("ophq_key", "hub_key"))

    def test_unsupported_platform_has_no_store(self):
        s = wt.SecretStore("x", platform="plan9")
        self.assertFalse(s.available())
        self.assertEqual(s.backend, "")
        self.assertIsNone(s.get("k"))
        self.assertFalse(s.set("k", "v"))

    def test_entry_names(self):
        s = self.store()
        s.set("ophq_key", KEY)
        self.assertIn(("NormanNetworks.OrcaPlugins/openprinthq-print-queue", "ophq_key"), s._impl.items)

    def test_migrate_moves_plaintext_into_store(self):
        s = self.store()
        use, save, status = wt.migrate_secrets({"ophq_key": KEY, "hub_key": "ahk_x", "ophq_url": "u"},
                                               s, wt.SECRET_KEYS)
        self.assertEqual(use["ophq_key"], KEY)
        self.assertEqual((save["ophq_key"], save["hub_key"]), ("", ""))
        self.assertEqual(save["ophq_url"], "u")  # not a secret
        self.assertEqual(save["secret_store"], "test store")
        self.assertEqual(status, {"ophq_key": "store", "hub_key": "store"})
        use2, save2, status2 = wt.migrate_secrets(save, s, wt.SECRET_KEYS)
        self.assertIsNone(save2)
        self.assertEqual(use2["hub_key"], "ahk_x")

    def test_blank_keeps_stored_value_and_new_value_replaces_it(self):
        s = self.store()
        s.set("ophq_key", "old")
        use, save, _ = wt.migrate_secrets({"ophq_key": ""}, s, wt.SECRET_KEYS)
        self.assertEqual(use["ophq_key"], "old")
        self.assertIsNone(save)
        use, save, _ = wt.migrate_secrets({"ophq_key": "new"}, s, wt.SECRET_KEYS)
        self.assertEqual(s.get("ophq_key"), "new")
        self.assertEqual(save["ophq_key"], "")

    def test_clear_secrets(self):
        s = self.store()
        s.set("ophq_key", KEY)
        use, save, status = wt.migrate_secrets({"ophq_key": "", "clear_secrets": ["ophq_key"]}, s, wt.SECRET_KEYS)
        self.assertIsNone(s.get("ophq_key"))
        self.assertNotIn("clear_secrets", save)
        self.assertEqual(use["ophq_key"], "")
        self.assertEqual(status["ophq_key"], "")

    def test_without_store_secrets_stay_in_config(self):
        s = wt.SecretStore("x", platform="plan9")
        use, save, status = wt.migrate_secrets({"ophq_key": KEY}, s, wt.SECRET_KEYS)
        self.assertIsNone(save)
        self.assertEqual(use["ophq_key"], KEY)
        self.assertEqual(status["ophq_key"], "config")

    def test_failed_write_keeps_config_copy(self):
        s = self.store()
        s._impl.set = lambda *a: (_ for _ in ()).throw(OSError("CredWriteW failed (error 1312)"))
        use, save, status = wt.migrate_secrets({"ophq_key": KEY}, s, wt.SECRET_KEYS)
        self.assertIsNone(save)
        self.assertEqual(use["ophq_key"], KEY)
        self.assertEqual(status["ophq_key"], "config")
        self.assertIn("1312", s.error)


def _has_libsecret():
    import ctypes.util
    return ctypes.util.find_library("secret-1") is not None


# A D-Bus session alone is not enough: without libsecret the store cannot load, and the test would fail
# instead of skipping (seen on a build host with an SSH session bus and no libsecret, 2026-11-14).
@unittest.skipUnless(sys.platform.startswith("linux") and os.environ.get("DBUS_SESSION_BUS_ADDRESS") and _has_libsecret(),
                     "needs a Linux desktop session with a Secret Service and libsecret (run under dbus-run-session with gnome-keyring)")
class LinuxSecretServiceLiveTests(unittest.TestCase):
    """Runs against a real Secret Service. Uses a throwaway plugin id and cleans up."""

    def test_round_trip(self):
        s = MOD.SecretStore("selftest-delete-me", platform="linux")
        self.assertTrue(s.available(), s.error)
        self.assertEqual(s.backend, "the Secret Service keyring")
        self.assertTrue(s.set("probe", "v1"), s.error)
        self.assertEqual(s.get("probe"), "v1")
        self.assertTrue(s.set("probe", "v2 \u00e9"), s.error)
        self.assertEqual(s.get("probe"), "v2 \u00e9")
        self.assertTrue(s.delete("probe"))
        self.assertIsNone(s.get("probe"))



def orca_escape(text):
    """What Orca's escape_string_cstyle does to a string option (config_value returns this)."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


class SyncFakePreset:
    def __init__(self, name, file="", notes=None, user=True):
        self.name, self.file, self._notes, self._user = name, file, notes, user

    def is_user(self):
        return self._user

    def config_value(self, key):
        return self._notes if key == "notes" else None


class SyncFakeCollection:
    def __init__(self, presets=()):
        self.presets = {p.name: p for p in presets}

    def preset_names(self):
        return list(self.presets)

    def find_preset(self, name):
        return self.presets.get(name)


class SyncFakeBundle:
    """A computer's presets as Orca's API shows them: a user printer preset, maybe the carrier."""

    def __init__(self, user_dir, carrier_file=None):
        printer = SyncFakePreset("My printer", os.path.join(user_dir, "machine", "My printer.json"))
        prints = []
        if carrier_file and os.path.exists(carrier_file):
            with open(carrier_file, encoding="utf-8") as fh:
                notes = json.load(fh)["notes"]
            escaped = orca_escape(notes)
            prints.append(SyncFakePreset(MOD.CARRIER_NAME, carrier_file, escaped))
        self.printers = SyncFakeCollection([printer])
        self.filaments = SyncFakeCollection()
        self.prints = SyncFakeCollection(prints)


class SettingsSyncTests(unittest.TestCase):
    """Carrier preset settings sync (shared block)."""

    def setUp(self):
        sys.modules.pop(MOD.SYNC_BUS_NAME, None)
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        sys.modules.pop(MOD.SYNC_BUS_NAME, None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unescape_matches_orca_escaping(self):
        raw = '{"a":"x\\\\y \\"q\\"","b":"line1\\nline2"}' + "\nreal newline"
        escaped = orca_escape(raw)
        self.assertEqual(MOD.unescape_cstyle(escaped), raw)
        self.assertEqual(MOD.unescape_cstyle(None), "")

    def test_carrier_setting_id_is_orca_uuid5(self):
        import uuid
        uid = "61d780f8-99f4-4886-bde7-cb447f263b95"
        want = str(uuid.uuid5(uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479"), uid + "/" + MOD.CARRIER_NAME))
        self.assertEqual(MOD.carrier_setting_id(uid), want)

    def test_parse_carrier_rejects_garbage(self):
        self.assertEqual(MOD.parse_carrier("not json"), MOD.empty_carrier())
        self.assertEqual(MOD.parse_carrier('{"schema": 99, "plugins": {}}'), MOD.empty_carrier())

    def test_carrier_preset_is_hidden_root_process_preset(self):
        p = MOD.carrier_preset_json(MOD.empty_carrier())
        self.assertEqual((p["inherits"], p["from"], p["compatible_printers_condition"]), ("", "User", "false"))
        self.assertEqual(MOD.carrier_cloud_content(MOD.empty_carrier())["type"], "print")

    def test_merge_first_time_adopts_carrier(self):
        section = {"settings": {"queue_priority": {"v": 40, "t": 100, "m": "A"}}}
        vals, state, sec, local, carrier = MOD.merge_settings({"queue_priority": 50, "status_poll_seconds": 5}, {}, section, 200, "B")
        self.assertEqual(vals["queue_priority"], 40)
        self.assertTrue(local)
        self.assertTrue(carrier)  # status_poll_seconds was not in the carrier yet
        self.assertEqual(sec["settings"]["status_poll_seconds"]["v"], 5)

    def test_merge_local_edit_publishes_and_remote_edit_pulls(self):
        state = {"base": {"q": 50, "p": 5}, "stamps": {"q": 100, "p": 100}}
        section = {"settings": {"q": {"v": 50, "t": 100, "m": "A"}, "p": {"v": 9, "t": 150, "m": "A"}}}
        vals, state2, sec, local, carrier = MOD.merge_settings({"q": 60, "p": 5}, state, section, 300, "B")
        self.assertEqual(sec["settings"]["q"], {"v": 60, "t": 300, "m": "B"})  # local edit wins and is published
        self.assertEqual(vals["p"], 9)                                       # newer remote value pulled
        self.assertTrue(local and carrier)
        self.assertEqual(state2["base"], {"q": 60, "p": 9})

    def test_merge_concurrent_edit_later_wins(self):
        state = {"base": {"q": 50}, "stamps": {"q": 100}}
        section = {"settings": {"q": {"v": 70, "t": 500, "m": "A"}}}
        vals, _s, sec, _l, _c = MOD.merge_settings({"q": 60}, state, section, 400, "B")
        self.assertEqual(vals["q"], 70)  # remote edit at 500 is later than ours at 400

    def test_merge_steady_state_changes_nothing(self):
        state = {"base": {"q": 50}, "stamps": {"q": 100}}
        section = {"settings": {"q": {"v": 50, "t": 100, "m": "A"}}}
        _v, s2, _sec, local, carrier = MOD.merge_settings({"q": 50}, state, section, 999, "B")
        self.assertFalse(local or carrier)
        self.assertEqual(s2, state)

    def test_syncable_excludes_secrets_and_reserved(self):
        s = MOD.SettingsSync("p", exclude=("api_key",), bundle=lambda: None)
        vals = s.syncable({"api_key": "wt_x", "_sync": {}, "secret_store": "x", "q": 1}, {"q": 0, "r": 2})
        self.assertEqual(vals, {"q": 1, "r": 2})

    def test_write_carrier_file_new_then_update(self):
        udir = os.path.join(self.tmp, "user", "uid1")
        doc = {"schema": 1, "plugins": {"p": {"settings": {"q": {"v": 1, "t": 5, "m": "A"}}}}}
        path = MOD.write_carrier_file(udir, doc)
        info = open(path[:-5] + ".info").read()
        self.assertIn("sync_info = create", info)
        self.assertIn("user_id = uid1", info)
        self.assertEqual(MOD.write_carrier_file(udir, doc), "")  # unchanged content: no rewrite
        with open(path[:-5] + ".info", "w") as fh:
            fh.write("sync_info = \nuser_id = uid1\nsetting_id = abc\nbase_id = \nupdated_time = 777\n")
        doc["plugins"]["p"]["settings"]["q"] = {"v": 2, "t": 6, "m": "A"}
        MOD.write_carrier_file(udir, doc)
        info = open(path[:-5] + ".info").read()
        self.assertIn("sync_info = update", info)
        self.assertIn("updated_time = 777", info)  # Orca's version check is never touched
        self.assertEqual(json.loads(json.load(open(path))["notes"])["plugins"]["p"]["settings"]["q"]["v"], 2)

    def test_files_writable(self):
        self.assertFalse(MOD.files_writable("/home/j/.config/OrcaSlicer/user/u"))
        self.assertTrue(MOD.files_writable("C:\\\\Users\\\\j\\\\AppData\\\\Roaming\\\\OrcaSlicer\\\\user\\\\u"))

    def test_two_computers_exchange_settings_through_the_carrier(self):
        """A edits, writes the carrier; Orca syncs the file to B; B adopts; B edits back."""
        dir_a = os.path.join(self.tmp, "A", "user", "uid")
        dir_b = os.path.join(self.tmp, "B", "user", "uid")
        defaults = {"q": 50, "p": 5}
        sync_a = MOD.SettingsSync("p", bundle=lambda: SyncFakeBundle(dir_a), machine="A", now=lambda: 1000)
        cfg_a = sync_a.sync({"q": 50, "p": 5}, defaults) or {}
        cfg_a["q"] = 61                                    # edited on A
        sys.modules.pop(MOD.SYNC_BUS_NAME, None)
        sync_a.now = lambda: 2000
        cfg_a = sync_a.sync(cfg_a, defaults)
        carrier_a = os.path.join(dir_a, "process", MOD.CARRIER_NAME + ".json")
        self.assertTrue(os.path.exists(carrier_a))
        # Orca's cloud sync copies the preset to B; B's plugin starts with defaults.
        os.makedirs(os.path.join(dir_b, "process"))
        import shutil
        shutil.copy(carrier_a, os.path.join(dir_b, "process"))
        sys.modules.pop(MOD.SYNC_BUS_NAME, None)           # B is a different Orca process
        carrier_b = os.path.join(dir_b, "process", MOD.CARRIER_NAME + ".json")
        sync_b = MOD.SettingsSync("p", bundle=lambda: SyncFakeBundle(dir_b, carrier_b), machine="B", now=lambda: 3000)
        cfg_b = sync_b.sync({"q": 50, "p": 5}, defaults)
        self.assertEqual(cfg_b["q"], 61)
        self.assertEqual(sync_b.status["detail"], "pulled newer settings")

    def test_bus_carries_changes_between_plugins_in_one_orca(self):
        pushed = []
        bus = MOD.sync_bus()
        bus.pusher = pushed.append
        s = MOD.SettingsSync("p", bundle=lambda: None, machine="A", now=lambda: 10)
        s.sync({"q": 1}, {"q": 1})
        deadline = time.time() + 5
        while not pushed and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(pushed[0]["plugins"]["p"]["settings"]["q"]["v"], 1)
        self.assertIs(MOD.sync_bus(), bus)
        self.assertEqual(carrier := bus.doc, pushed[0])


class AgentSettingsSyncTests(ServerCase):
    def test_agent_publishes_settings_without_secrets(self):
        saved_store = wt.SECRETS
        wt.SECRETS = wt.SecretStore("openprinthq-print-queue", platform="test")
        wt.SECRETS._impl = wt.MemorySecretBackend()
        try:
            a = self.agent(queue_priority=42, hub_key="ahk_0123abcd_" + "s" * 32, wt_username="farm")
            a.save_config = lambda text: True
            cfg = a._config()
            self.assertEqual(cfg["queue_priority"], 42)
            self.assertNotIn("_sync", cfg)
            doc = wt.sync_bus().doc
            text = wt.carrier_text(doc)
            settings = doc["plugins"]["openprinthq-print-queue"]["settings"]
            self.assertEqual(settings["queue_priority"]["v"], 42)
            for k in ("ophq_url", "ophq_key", "hub_key") + wt.LEGACY_KEYS:   # the address stays local (0.15.0)
                self.assertNotIn(k, settings)
            self.assertNotIn(KEY, text)
            self.assertNotIn("s" * 32, text)
        finally:
            wt.SECRETS = saved_store

    def test_unreadable_config_is_never_synced_or_written(self):
        """0.8.1: before Orca has loaded the capability, get_config() raises. Treat that as unknown."""
        a = self.agent(queue_priority=50)
        written = []

        def refuse():
            raise RuntimeError("get_config() is only available on a capability loaded by the plugin host")
        a.get_config = refuse
        a.save_config = lambda text: written.append(text) or True
        cfg = a._config()
        self.assertEqual(cfg["queue_priority"], wt.DEFAULT_CONFIG["queue_priority"])
        self.assertEqual(written, [])
        self.assertIsNone(wt.sync_bus().doc)   # defaults were not published as settings
        del a.get_config                        # loaded now: the real config is read and synced
        a._last_sync = 0
        self.assertEqual(a._config()["queue_priority"], 50)
        self.assertEqual(wt.sync_bus().doc["plugins"]["openprinthq-print-queue"]["settings"]["queue_priority"]["v"], 50)

    def test_agent_adopts_settings_from_another_computer(self):
        other = {"schema": 1, "plugins": {"openprinthq-print-queue": {"settings": {
            "queue_priority": {"v": 33, "t": 10 ** 13, "m": "workshop-pc"}}}}}
        a = self.agent(queue_priority=50)
        wt.sync_bus().offer(other, "cloud")
        written = []
        a.save_config = lambda text: written.append(json.loads(text)) or True
        self.assertEqual(a._config()["queue_priority"], 33)
        self.assertEqual(written[-1]["queue_priority"], 33)


class ManagedConnectionTests(ServerCase):
    """0.15.0: a managed install (OrcaSlicer in the browser on an ArtisanHub hub) gives the address and key in the
    environment. They fill only blanks, are never written to the plugin's settings and never sync."""

    def env(self, url=None, key=None):
        values = {}
        if url is not None:
            values[wt.MANAGED_URL_ENV] = url
        if key is not None:
            values[wt.MANAGED_KEY_ENV] = key
        return mock.patch.dict(os.environ, values)

    def test_environment_connects_a_blank_config(self):
        with self.env(self.base, KEY):
            a = self.agent(mapping={"Bambu Lab H2D 0.4 nozzle": "H2D"}, connect=False)
            self.assertEqual(a.connect_printer(Params()), wt.SUCCESS, NOTIFICATIONS)
            cfg = a._config()
        self.assertEqual((cfg["ophq_url"], cfg["ophq_key"]), (self.base, KEY))
        self.assertEqual(a._managed, {"url": self.base, "key": True})

    def test_own_settings_win_over_the_environment(self):
        with self.env("https://elsewhere.example", "ophqk_" + "e" * 43):
            cfg = self.agent()._config()
        self.assertEqual((cfg["ophq_url"], cfg["ophq_key"]), (self.base, KEY))

    def test_environment_values_are_never_written_or_synced(self):
        written = []
        with self.env(self.base, KEY):
            a = self.agent(connect=False, queue_priority=41)
            a.save_config = lambda text: written.append(text) or True
            a._config()
        for text in written:
            self.assertNotIn(KEY, text)
            self.assertNotIn(self.base, text)
        doc = wt.sync_bus().doc
        if doc:
            text = wt.carrier_text(doc)
            self.assertNotIn(KEY, text)
            self.assertNotIn(self.base, text)

    def test_values_that_are_not_an_address_or_key_are_ignored(self):
        self.assertEqual(wt.managed_connection({wt.MANAGED_URL_ENV: "", wt.MANAGED_KEY_ENV: "not-a-key"}), ("", ""))
        self.assertEqual(wt.managed_connection({}), ("", ""))

    def test_config_page_says_what_this_orcaslicer_was_given(self):
        with self.env(self.base, KEY):
            a = self.agent(connect=False)
            html = a.get_config_ui()
        blob = html[html.index("var D = ") + len("var D = "):]
        d = json.loads(blob[:blob.index(";\nvar OP")])
        self.assertEqual(d["managed"], {"url": self.base, "key": True})
        self.assertTrue(d["ophq"]["ok"], d["ophq"])
        self.assertIn("Set for this OrcaSlicer", html)
        self.assertNotIn(KEY, html)


# ---------------------------------------------------------------- Artisan Hub (0.11.0)

HUB_KEY = "ahk_0123abcd_" + "x" * 32


class HubMock:
    @classmethod
    def reset(cls):
        cls.calls = []
        cls.look = {"skin": "openprinthq", "mode": "dark"}
        cls.can_act = True

    @classmethod
    def farm(cls):
        return {"api": 1, "team": "Artisan Prints", "can_act": cls.can_act, "errors": {},
                "plate_checks_on": True, "reasons": {"spaghetti": "Spaghetti or came loose", "other": "Other"},
                "checks": [{"id": 7, "printer": "MK4S", "job_name": "Widget", "job_outcome": "completed", "state": "waiting",
                            "photo": "/api/plate-checks/7/photo", "created_at": "2026-10-21T10:00:00+00:00", "model": None}],
                "recent_checks": [], "history": [{"id": 1, "external_id": "24", "name": "Cube", "status": "completed",
                                                  "printer": "voron01", "filament_g": 280.2, "used_g": 267.3,
                                                  "used_source": "printer"}],
                "stagger": {"minutes": 3, "waiting": []},
                "timeline": {"now": "2026-10-21T10:00:00+00:00", "until": "2026-10-21T22:00:00+00:00", "turnaround_minutes": 10,
                             "lanes": [{"printer": "MK4S", "online": True, "state": "Idle", "down": False, "bars": []}],
                             "unscheduled": [], "orders": [{"id": 5, "number": "1042", "state": "late", "jobs": 2, "jobs_left": 1,
                                                            "stuck": 0, "due_at": None, "projected": None}]},
                "maintenance": {"open": [{"id": 3, "printer": "H2C", "title": "Clean fans", "parts": []}], "due": [],
                                "down": [], "linked": True},
                "filament": {"measured_jobs": 1, "measured_vs_estimate": 0.954, "klipper_printers": ["voron01"], "weigh_ins": []},
                "spools": {"enabled": True, "last": None, "linked": 6, "broken": 0},
                "report": {"jobs": 10, "success_rate": 90.0}, "quotes_waiting": 2, "look": cls.look}


HubMock.reset()


class HubHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, status, doc=None, raw=None, ctype="application/json"):
        body = raw if raw is not None else json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"null") if n else None
        HubMock.calls.append((method, self.path, body))
        if self.headers.get("Authorization") != "Bearer " + HUB_KEY:
            return self._reply(401, {"detail": "invalid API key"})
        if method == "GET" and self.path == "/api/plugin/farm":
            return self._reply(200, HubMock.farm())
        if method == "GET" and self.path == "/api/plate-checks/7/photo":
            return self._reply(200, raw=b"\xff\xd8JPEG", ctype="image/jpeg")
        m = re.match(r"^/api/plate-checks/(\d+)/(pass|fail|skip|retry-ready)$", self.path)
        if method == "POST" and m:
            if m.group(2) == "fail" and not (body or {}).get("reason"):
                return self._reply(422, {"detail": [{"msg": "reason is required"}]})
            return self._reply(200, {"id": int(m.group(1)), "printer": "MK4S", "state": m.group(2), "ready_set": True,
                                     "reason_label": "Spaghetti or came loose" if m.group(2) == "fail" else None,
                                     "reprint_job_id": 99 if (body or {}).get("reprint") else None})
        if method == "POST" and self.path == "/api/maintenance/tasks/3/done":
            return self._reply(200, {"id": 3, "title": "Clean fans", "printer": "H2C", "state": "done"})
        if method == "POST" and self.path == "/api/orders/5/prioritise":
            return self._reply(200, {"raised": 1, "errors": []})
        return self._reply(404, {"detail": "Not Found"})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


class HubTests(ServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hub_server = ThreadingHTTPServer(("127.0.0.1", 0), HubHandler)
        cls.hub_url = "http://127.0.0.1:%d" % cls.hub_server.server_address[1]
        threading.Thread(target=cls.hub_server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.hub_server.shutdown()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        HubMock.reset()
        self.saved_store = wt.SECRETS
        wt.SECRETS = wt.SecretStore("openprinthq-print-queue", platform="test")
        wt.SECRETS._impl = wt.MemorySecretBackend()

    def tearDown(self):
        wt.SECRETS = self.saved_store
        wt.HUB.agent = None

    def hub_agent(self, key=HUB_KEY, **cfg):
        a = self.agent(hub_url=self.hub_url, hub_key=key, **cfg)
        wt.HUB.agent = a
        return a

    # -- pure helpers
    def test_resolve_look(self):
        # 0.13.0: one look, artisanhub-ui's glass-dark, whatever the saved settings or the hub say
        for cfg, hub_look in (({}, None), ({"look": "orca"}, {"skin": "classic"}),
                              ({"look": "legacy_farm", "look_mode": "light"}, {"skin": "classic", "mode": "dark"})):
            self.assertEqual(wt.resolve_look(cfg, hub_look), {"theme": "glass-dark"})

    def test_hub_connection(self):
        self.assertEqual(wt.hub_connection({}), ("", ""))
        self.assertEqual(wt.hub_connection({"hub_url": "hub.example.com/", "hub_key": "k"}), ("https://hub.example.com", "k"))
        self.assertEqual(wt.hub_connection({"hub_url": "http://h", "hub_key": ""}), ("", ""))

    def test_themed_applies_glass_with_embedded_fonts(self):
        html = wt.themed("<!doctype html><html><head><title>x</title></head></html>", {"skin": "classic", "mode": "dark"})
        self.assertIn('<html lang="en" data-theme="glass-dark">', html)
        self.assertIn("data:font/woff2;base64,", html)
        html = wt.themed("<html><head></head></html>", None, "https://hub.example.com")
        self.assertNotIn("hub.example.com", html)   # 0.13.0: nothing is fetched from the hub any more

    def test_glass_covers_every_page(self):
        for page in (wt.TAB_HTML, wt.JOB_WINDOW_HTML, wt.PREFLIGHT_HTML):
            self.assertIn('[data-theme="glass-dark"]', page)
            self.assertIn("backdrop-filter:var(--ah-blur)", page)
            self.assertIn("@supports not ((backdrop-filter:blur(1px))", page)   # solid panels where glass is missing
            self.assertNotIn("data-skin", page)
        self.assertNotIn("\u2014", wt.TAB_HTML + wt.SHARED_CSS + wt.CONFIG_PAGE_HTML + wt.JOB_WINDOW_HTML + wt.PREFLIGHT_HTML)

    # -- without the hub nothing changes
    def test_without_hub_snapshot_says_not_configured(self):
        a = self.agent()
        wt.HUB.agent = a
        wt.HUB._do({"cmd": "refresh"})
        snap = wt.HUB.snapshot()
        self.assertEqual(snap["hub"], {"configured": False, "url": "", "ok": False, "error": ""})
        self.assertEqual(snap["look"], {"theme": "glass-dark"})
        self.assertEqual(HubMock.calls, [])
        with self.assertRaises(wt.OpenPrintHQError):
            wt.HUB._do({"cmd": "hub_pass", "id": 7})

    # -- with the hub
    def test_refresh_brings_hub_data_and_look(self):
        self.hub_agent()
        wt.HUB._do({"cmd": "refresh"})
        snap = wt.HUB.snapshot()
        self.assertTrue(snap["hub"]["ok"])
        self.assertEqual(snap["hub"]["data"]["checks"][0]["printer"], "MK4S")
        self.assertEqual(snap["look"], {"theme": "glass-dark"})   # the hub's skin no longer changes the plugin
        # cached: a second refresh within 20 s does not call the hub again
        n = len(HubMock.calls)
        wt.HUB._do({"cmd": "refresh"})
        self.assertEqual(len(HubMock.calls), n)
        # job windows get the look too
        self.assertEqual(wt.HUB.snapshot([1])["look"]["theme"], "glass-dark")
        self.assertNotIn("hub", wt.HUB.snapshot([1]))

    def test_old_look_settings_are_ignored(self):
        self.hub_agent(look="classic", look_mode="light")
        wt.HUB._do({"cmd": "refresh"})
        self.assertEqual(wt.HUB.snapshot()["look"], {"theme": "glass-dark"})

    def test_bad_key_shows_error_and_keeps_tab_working(self):
        self.hub_agent(key="ahk_0123abcd_" + "y" * 32)
        wt.HUB._do({"cmd": "refresh"})
        snap = wt.HUB.snapshot()
        self.assertFalse(snap["hub"]["ok"])
        self.assertIn("refused the API key", snap["hub"]["error"])
        self.assertTrue(snap["printers"])          # the farm side is unaffected

    def test_plate_check_actions(self):
        self.hub_agent()
        text = wt.HUB._do({"cmd": "hub_pass", "id": 7, "set_ready": True})
        self.assertIn("Plate on MK4S passed", text)
        self.assertIn(("POST", "/api/plate-checks/7/pass", {"set_ready": True, "note": None}), HubMock.calls)
        text = wt.HUB._do({"cmd": "hub_fail", "id": 7, "reason": "spaghetti", "reprint": True})
        self.assertIn("Spaghetti or came loose", text)
        self.assertIn("job #99", text)
        with self.assertRaises(wt.OpenPrintHQError) as cm:
            wt.HUB._do({"cmd": "hub_fail", "id": 7, "reason": ""})
        self.assertIn("why", str(cm.exception))
        self.assertIn("skipped", wt.HUB._do({"cmd": "hub_skip", "id": 7}))
        # every action refreshes the hub summary right away
        self.assertEqual(HubMock.calls[-1][1], "/api/plugin/farm")

    def test_maintenance_and_order_actions(self):
        self.hub_agent()
        self.assertIn("Clean fans on H2C marked done", wt.HUB._do({"cmd": "hub_maint_done", "id": 3}))
        self.assertIn("Raised 1", wt.HUB._do({"cmd": "hub_prioritise", "id": 5, "priority": 100}))
        with self.assertRaises(wt.OpenPrintHQError):
            wt.HUB._do({"cmd": "hub_prioritise", "id": 5, "priority": 60})
        with self.assertRaises(wt.OpenPrintHQError):
            wt.HUB._do({"cmd": "hub_nonsense"})

    def test_photo_comes_back_as_data_uri(self):
        self.hub_agent()
        got = []
        done = threading.Event()
        wt.HUB.photo_async(7, lambda m: (got.append(m), done.set()))
        self.assertTrue(done.wait(5))
        self.assertEqual(got[0]["type"], "photo")
        self.assertTrue(got[0]["data"].startswith("data:image/jpeg;base64,"))

    def test_hub_key_is_a_secret(self):
        a = self.hub_agent()
        cfg = a._config()
        self.assertEqual(cfg["hub_key"], HUB_KEY)          # usable
        self.assertEqual(wt.SECRETS.get("hub_key"), HUB_KEY)  # kept in the credential store
        self.assertIn("hub_key", wt.SECRET_KEYS)

    def test_tab_has_hub_views_and_look(self):
        self.hub_agent()
        wt.HUB._do({"cmd": "refresh"})
        ui = wt.OpenPrintHQTab().get_ui()
        for v in ("checks", "timeline", "maint", "filament", "reports"):
            self.assertIn('data-view="%s"' % v, ui)
        self.assertIn('data-theme="glass-dark"', ui)
        self.assertNotIn(self.hub_url + "/theme/fonts/", ui)
        self.assertNotIn(HUB_KEY, ui)
        self.assertIn('m.type === "photo"', ui)                 # photos land in the tab,
        self.assertNotIn('m.type === "photo"', wt.JOB_WINDOW_HTML)  # not in the job window

    def test_config_page_reports_hub(self):
        a = self.hub_agent()
        html = a.get_config_ui()
        m = re.search(r"var D = (\{.*?\});\nvar OP", html, re.S)
        data = json.loads(m.group(1).replace("<\\/", "</"))
        self.assertTrue(data["hub"]["ok"])
        self.assertEqual(data["hub"]["team"], "Artisan Prints")
        self.assertNotIn(HUB_KEY, html)
        self.assertIn('id="hurl"', html)
        self.assertIn('[data-theme="glass-dark"]', html)
        self.assertNotIn('id="look"', html)


class GlassLookTests(unittest.TestCase):
    """0.13.0: artisanhub-ui's glass-dark look, embedded so the pages work offline."""

    def pages(self):
        return (wt.TAB_HTML, wt.JOB_WINDOW_HTML, wt.PREFLIGHT_HTML, wt.CONFIG_PAGE_HTML)

    def test_values_come_from_artisanhub_ui(self):
        self.assertEqual(wt.AH_UI_VERSION, "0.1.0")
        for token in ("--ah-glass:", "--ah-chip-underlay:", "--ah-page-gradient:", "prefers-reduced-transparency"):
            self.assertIn(token, wt.AH_THEME_CSS)

    def test_fonts_are_embedded_not_fetched(self):
        for family in ("Manrope", "Sora", "JetBrains Mono"):
            self.assertIn('font-family:"%s"' % family, wt.AH_FONT_CSS)
        self.assertEqual(wt.AH_FONT_CSS.count("url(data:font/woff2;base64,"), 4)
        for page in self.pages() + (wt.SHARED_CSS,):
            self.assertNotRegex(page, r"url\(\s*['\"]?https?:")
            self.assertNotIn("<link", page)

    def test_tab_layout(self):
        for marker in ('class="app"', 'class="cols"', 'id="printers"', 'id="queue"', 'id="jobs"', 'id="alerts"'):
            self.assertIn(marker, wt.TAB_HTML)

    def test_lists_open_outside_scrolling_cards(self):
        script = wt.TAB_HTML[wt.TAB_HTML.index("<script>"):]
        self.assertIn("document.body.appendChild(list)", script)
        self.assertIn(".dd-list{position:fixed", wt.TAB_HTML)

    def test_product_name_is_one_word(self):
        for page in self.pages():
            self.assertNotIn("Artisan Hub", page)


class DeclarePermissionsTests(unittest.TestCase):
    """0.11.1: the farm's host is requested once at load, not per URL (0.12.0: openprinthq.com)."""

    def test_requests_the_openprinthq_host_and_threads(self):
        calls = []
        mod = types.SimpleNamespace(request_permissions=lambda **kw: calls.append(kw))
        self.assertEqual(wt.declare_permissions(mod), "network")
        self.assertEqual(calls, [{"network": ["https://openprinthq.com"], "threading": True}])

    def test_declared_host_is_a_bare_host(self):
        for url in wt.DECLARED_NETWORK:
            parts = urllib.parse.urlsplit(url)
            self.assertEqual(parts.scheme, "https")
            self.assertEqual(parts.path, "")
            self.assertIsNone(parts.port)

    def test_api_calls_stay_on_the_declared_host(self):
        hosts = {urllib.parse.urlsplit(u).netloc for u in wt.DECLARED_NETWORK}
        api = wt.engine_api_base("https://openprinthq.com", KEY)
        self.assertIn(urllib.parse.urlsplit(api).netloc, hosts)

    def test_orca_without_network_argument_asks_for_threads_only(self):
        calls = []

        def request(fs_read=(), fs_readwrite=(), network_http=(), network_socket=(), process=(), threading=False):
            calls.append(threading)
        self.assertEqual(wt.declare_permissions(types.SimpleNamespace(request_permissions=request)), "threading")
        self.assertEqual(calls, [True])

    def test_orca_without_the_api(self):
        self.assertIsNone(wt.declare_permissions(types.SimpleNamespace()))

    def test_outside_plugin_loading_is_ignored(self):
        def request(**kw):
            raise ValueError("request_permissions() called outside plugin discovery context")
        self.assertIsNone(wt.declare_permissions(types.SimpleNamespace(request_permissions=request)))

    def test_import_did_not_fail_with_the_fake_orca(self):
        self.assertIsNone(wt.DECLARED_PERMISSIONS)  # the fake orca has no request_permissions


if __name__ == "__main__":
    unittest.main()
