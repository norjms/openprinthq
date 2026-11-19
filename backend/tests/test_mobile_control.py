"""Mobile control (2026-10-23): the farm spool library, barcodes and identification, locations and scanning,
labels, printer hardware, the store from the phone, order status and the one-time library move."""
import io
import json
import secrets as _s
import zipfile

import httpx
import pytest

from test_mobile_api import H, _client, _key

API = "/api/engine/api/v1"
SI = API + "/spoolman/inventory"


class FakeEngine:
    """An OpenPrintHQ control plane + engine in Spoolman mode: spools, filaments, slot assignments, locations,
    nozzles, filament paths, what is loaded, the cover image and printer controls."""

    def __init__(self):
        self.calls = []
        self.printers = [{"id": 3, "name": "H2D", "model": "H2D", "connection_type": "bambu", "is_active": True,
                          "nozzle_count": 2},
                         {"id": 4, "name": "MK4S", "model": "MK4S", "connection_type": "prusa", "is_active": True,
                          "nozzle_count": 1}]
        self.status = {3: {"connected": True, "state": "RUNNING", "progress": 37.0, "remaining_time": 42,
                           "subtask_name": "Vase", "layer_num": 50, "total_layers": 200, "temperatures": {}},
                       4: {"connected": True, "state": "IDLE"}}
        self.vendors = {1: "Acme"}
        self.filaments = [{"id": 10, "name": "PLA Red", "material": "PLA", "color_hex": "FF0000", "weight": 1000,
                           "spool_weight": 250, "vendor": {"id": 1, "name": "Acme"}}]
        self.spools = {21: self._spool(21, 10, 600, location_id=101)}
        self.next_spool = 30
        self.assign = [{"printer_id": 3, "printer_name": "H2D", "ams_id": 0, "tray_id": 1, "spoolman_spool_id": 21,
                        "ams_label": None, "extruder_index": 0}]
        self.locations = [
            {"id": 100, "name": "Shelf", "kind": "storage", "parent_id": None, "printer_id": None, "ams_id": None,
             "tray_id": None, "code": "LSHELF1", "note": None, "spool_count": 0},
            {"id": 101, "name": "Dry box 1", "kind": "drybox", "parent_id": 100, "printer_id": None, "ams_id": None,
             "tray_id": None, "code": "LDRY001", "note": None, "spool_count": 1},
            {"id": 200, "name": "H2D", "kind": "printer", "parent_id": None, "printer_id": 3, "ams_id": None,
             "tray_id": None, "code": "LH2D000", "note": None, "spool_count": 0},
            {"id": 201, "name": "H2D - AMS A slot 2", "kind": "ams_slot", "parent_id": 200, "printer_id": 3, "ams_id": 0,
             "tray_id": 1, "code": "LSLOT02", "note": None, "spool_count": 0},
            {"id": 202, "name": "H2D - AMS A slot 3", "kind": "ams_slot", "parent_id": 200, "printer_id": 3, "ams_id": 0,
             "tray_id": 2, "code": "LSLOT03", "note": None, "spool_count": 0},
        ]
        self.slot_paths = {(0, 1): [0], (0, 2): [0, 1], (255, 0): [1], (255, 1): [0]}
        self.manual_nozzle = None

    def _spool(self, sid, fid, remaining, location_id=None, note=None):
        f = next(x for x in self.filaments if x["id"] == fid)
        return {"id": sid, "material": f["material"], "subtype": f["name"][len(f["material"]):].strip() or None,
                "brand": f["vendor"]["name"], "rgba": f["color_hex"] + "FF", "color_name": None,
                "label_weight": f["weight"], "weight_used": f["weight"] - remaining, "storage_location": None,
                "location_id": location_id, "archived_at": None, "note": note, "slicer_filament_name": "Generic PLA",
                "_fid": fid}

    def _find_or_create_filament(self, b):
        name = f"{b['material']} {b.get('subtype') or ''}".strip()
        color = (b.get("rgba") or "808080FF")[:6].upper()
        for f in self.filaments:
            if f["material"] == b["material"] and f["name"] == name and f["color_hex"] == color and \
                    (not b.get("brand") or f["vendor"]["name"] == b["brand"]):
                return f["id"]
        vid = next((k for k, v in self.vendors.items() if v == b.get("brand")), None)
        if b.get("brand") and vid is None:
            vid = max(self.vendors) + 1
            self.vendors[vid] = b["brand"]
        fid = max(f["id"] for f in self.filaments) + 1
        self.filaments.append({"id": fid, "name": name, "material": b["material"], "color_hex": color,
                               "weight": b.get("label_weight", 1000), "spool_weight": 250,
                               "vendor": {"id": vid, "name": b.get("brand")} if vid else None})
        return fid

    def handler(self, request: httpx.Request) -> httpx.Response:
        p, m = request.url.path, request.method
        body = json.loads(request.content) if request.content and "json" in request.headers.get("content-type", "") else None
        params = dict(request.url.params)
        self.calls.append((m, p, body, params))
        assert request.headers["authorization"] == "Bearer ophqk_test"
        J = lambda d, s=200: httpx.Response(s, json=d)  # noqa: E731
        if p == "/api/cameras":
            return J({"streams": [{"printer_id": 3, "camera_name": "Printer camera", "stream_name": "p3",
                                   "enabled": True, "state": "ONLINE"}]})
        if p == "/api/cameras/stream.m3u8":
            if params.get("src") != "p3":
                return J({"error": "no such camera"}, 404)
            return httpx.Response(200, content=b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=192000\nhls/playlist.m3u8?id=AbC123\n",
                                  headers={"content-type": "application/vnd.apple.mpegurl"})
        if p.startswith("/api/cameras/hls/"):
            if params.get("id") != "AbC123":
                return J({"error": "go2rtc HTTP 404"}, 404)
            f = p.rsplit("/", 1)[1]
            return httpx.Response(200, content=b"seg:" + f.encode() + b":" + params.get("n", "").encode(),
                                  headers={"content-type": "video/mp4"})
        if p == API + "/printers/":
            return J(self.printers)
        if p.endswith("/status") and p.startswith(API + "/printers/"):
            return J(self.status[int(p.split("/")[-2])])
        if p == SI + "/spools" and m == "GET":
            return J([{k: v for k, v in s.items() if k != "_fid"} for s in self.spools.values()
                      if params.get("include_archived") == "true" or not s["archived_at"]])
        if p == SI + "/spools" and m == "POST":
            return J(self._create(body))
        if p == SI + "/spools/bulk" and m == "POST":
            return J([self._create(body["spool"]) for _ in range(body["quantity"])])
        mt = __import__("re").match(SI + r"/spools/(\d+)(/archive)?$", p)
        if mt:
            sid = int(mt.group(1))
            s = self.spools.get(sid)
            if not s:
                return J({"detail": "Spool not found in Spoolman"}, 404)
            if mt.group(2):
                s["archived_at"] = "2026-10-23T00:00:00Z"
            elif m == "PATCH":
                for k in ("weight_used", "note", "storage_location", "location_id"):
                    if k in body:
                        s[k] = body[k]
            return J({k: v for k, v in s.items() if k != "_fid"})
        if p == SI + "/filaments":
            return J(self.filaments)
        if p == SI + "/slot-assignments/all":
            return J(self.assign)
        if p == SI + "/slot-assignments" and m == "POST":
            allowed = self.slot_paths.get((body["ams_id"], body["tray_id"]), [0])
            ex = body.get("extruder_index")
            if ex is not None and ex not in allowed:
                return J({"detail": f"extruder {ex} is not on this slot's path", "allowed": allowed}, 409)
            if ex is None and len(allowed) == 1:
                ex = allowed[0]
            self.assign = [a for a in self.assign if a["spoolman_spool_id"] != body["spoolman_spool_id"]]
            self.assign.append({"printer_id": body["printer_id"], "printer_name": "H2D", "ams_id": body["ams_id"],
                                "tray_id": body["tray_id"], "spoolman_spool_id": body["spoolman_spool_id"],
                                "ams_label": None, "extruder_index": ex})
            s = self.spools[body["spoolman_spool_id"]]
            return J({**{k: v for k, v in s.items() if k != "_fid"}, "extruder_index": ex, "slot_location_id": 201})
        if p.startswith(SI + "/slot-assignments/") and m == "DELETE":
            sid = int(p.rsplit("/", 1)[1])
            self.assign = [a for a in self.assign if a["spoolman_spool_id"] != sid]
            return J({"id": sid})
        if p == API + "/inventory/locations" and m == "GET":
            rows = self.locations
            if params.get("kind"):
                rows = [x for x in rows if x["kind"] == params["kind"]]
            return J(rows)
        if p == API + "/inventory/locations" and m == "POST":
            row = {"id": 300 + len(self.locations), "code": body.get("code") or "LNEW" + str(len(self.locations)).zfill(3),
                   "kind": "storage", "parent_id": None, "printer_id": None, "ams_id": None, "tray_id": None,
                   "note": None, "spool_count": 0, **body}
            self.locations.append(row)
            return J(row, 201)
        if p.startswith(API + "/inventory/locations/by-code/"):
            code = p.rsplit("/", 1)[1].upper()
            loc = next((x for x in self.locations if x["code"] == code), None)
            if not loc:
                return J({"detail": "Location not found"}, 404)
            return J({**loc, "spools": [s["id"] for s in self.spools.values() if s["location_id"] == loc["id"]],
                      "spool_source": "spoolman",
                      "printer_name": next((x["name"] for x in self.printers if x["id"] == loc["printer_id"]), None)})
        if p == API + "/inventory/locations/sync-printers":
            return J([x for x in self.locations if x["printer_id"]])
        mt = __import__("re").match(API + r"/inventory/locations/(\d+)$", p)
        if mt:
            loc = next((x for x in self.locations if x["id"] == int(mt.group(1))), None)
            if not loc:
                return J({"detail": "not found"}, 404)
            if m == "DELETE":
                self.locations.remove(loc)
                return J({"ok": True})
            loc.update(body)
            return J(loc)
        if p == API + "/printers/3/nozzles" and m == "GET":
            return J(self._nozzles())
        if p.startswith(API + "/printers/3/nozzles/"):
            if m == "PUT":
                if body["nozzle_type"] not in ("hardened_steel", "stainless_steel", "brass"):
                    return J({"detail": "bad nozzle_type"}, 422)
                self.manual_nozzle = body
            else:
                if not self.manual_nozzle:
                    return J({"detail": "no manual override"}, 404)
                self.manual_nozzle = None
            return J(self._nozzles())
        if p == API + "/printers/3/filament-paths":
            if m == "PUT":
                return J({"nozzle_count": 2, "track_switch": {"mode": body.get("track_switch_mode", "auto")}, "slots": []})
            return J({"nozzle_count": 2, "track_switch": {"installed": False, "source": "mqtt", "mode": "auto",
                                                          "inlets": {"A": None, "B": None}},
                      "slots": [{"ams_id": a, "tray_id": t, "extruders": e, "fixed": len(e) == 1} for (a, t), e in
                                self.slot_paths.items()]})
        if p == API + "/printers/3/loaded":
            return J({"printer_id": 3, "printer_name": "H2D", "nozzle_count": 2, "active_extruder": 0,
                      "slots": [{"ams_id": 0, "tray_id": 1, "label": "AMS A slot 2", "ams_type": "AMS", "extruders": [0],
                                 "fixed": True, "source": "mqtt", "extruder_index": 0,
                                 "assignment": {"source": "spoolman", "spool_id": 21, "extruder_index": 0,
                                                "material": "PLA", "color": "#FF0000", "brand": "Acme", "name": "Red",
                                                "remaining_g": 600.0, "slicer_preset": "GFL99",
                                                "slicer_preset_name": "Generic PLA"},
                                 "live": {"tray_type": "PLA"}},
                                {"ams_id": 0, "tray_id": 2, "label": "AMS A slot 3", "ams_type": "AMS",
                                 "extruders": [0, 1], "fixed": False, "source": "mqtt", "extruder_index": None,
                                 "assignment": None, "live": None}]})
        if p == API + "/printers/camera/stream-token":
            return J({"token": "tok123"})
        if p == API + "/printers/3/cover":
            if params.get("token") != "tok123":
                return J({"detail": "Valid camera stream token required"}, 401)
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nplate-" + (params.get("view") or "side").encode())
        if p == API + "/printers/4/cover":
            return J({"detail": "No subtask_name"}, 404)
        if p.startswith(API + "/printers/") and m == "POST":
            return J({"success": True})
        return J({"detail": f"unhandled {m} {p}"}, 404)

    def _create(self, b):
        fid = b.get("spoolman_filament_id") or self._find_or_create_filament(b)
        f = next(x for x in self.filaments if x["id"] == fid)
        sid = self.next_spool
        self.next_spool += 1
        s = self._spool(sid, fid, f["weight"] - float(b.get("weight_used") or 0), b.get("location_id"), b.get("note"))
        if b.get("storage_location"):
            s["storage_location"] = b["storage_location"]
        self.spools[sid] = s
        return {k: v for k, v in s.items() if k != "_fid"}

    def _nozzles(self):
        n0 = {"extruder_index": 0, "position": "right", "diameter_mm": 0.4, "nozzle_type": "hardened_steel",
              "volume_type": "Standard", "source": "mqtt", "live": None, "active": True}
        if self.manual_nozzle:
            n0 = {**n0, **self.manual_nozzle, "source": "manual"}
        return {"nozzle_count": 2, "nozzles": [n0, {"extruder_index": 1, "position": "left", "diameter_mm": 0.4,
                                                    "nozzle_type": "hardened_steel", "volume_type": "Standard",
                                                    "source": "default", "live": None, "active": False}], "rack": []}


@pytest.fixture
def farm(monkeypatch):
    """OpenPrintHQ as the farm, with the fake engine behind it."""
    from app import clients
    from app.clients.openprinthq_api import OpenPrintHQClient
    from app.services import spool_library
    fake = FakeEngine()
    c = OpenPrintHQClient("https://ophq.example", "ophqk_test")
    c.client = httpx.Client(base_url="https://ophq.example", transport=httpx.MockTransport(fake.handler),
                            headers=c.client.headers)
    monkeypatch.setattr(clients, "farm", lambda: c)
    monkeypatch.setattr(clients, "openprinthq", lambda: c)
    monkeypatch.setattr(spool_library, "farm_library", lambda team_id=None: True)
    return c, fake


@pytest.fixture
def key():
    return _key(["admin"])


def test_spool_list_from_the_farm_library_with_codes_slots_and_barcodes(farm, key):
    from app.db import session_scope
    from app.models import FilamentBarcode, SpoolCache
    c = _client()
    with session_scope() as db:
        db.add(FilamentBarcode(barcode="0000000000017", filament_id=10, vendor="Acme", name="PLA Red", material="PLA"))
    r = c.get("/api/mobile/v1/spools", headers=H(key))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "farm"
    s = next(x for x in d["spools"] if x["id"] == 21)
    assert s["code"] == "S21" and s["filament_id"] == 10 and s["remaining_g"] == 600 and s["color_hex"] == "#FF0000"
    assert s["location_code"] == "LDRY001" and s["barcode"] == "0000000000017"
    assert s["assigned"] == {"printer": "H2D", "printer_id": 3, "ams_id": 0, "tray_id": 1, "extruder_index": 0,
                             "slot_label": "AMS A slot 2"}
    assert c.get("/api/mobile/v1/spools?q=red acme", headers=H(key)).json()["spools"]
    assert c.get("/api/mobile/v1/spools?material=PETG", headers=H(key)).json()["spools"] == []
    assert c.get("/api/mobile/v1/spools?location=LDRY001", headers=H(key)).json()["spools"]
    one = c.get("/api/mobile/v1/spools/21", headers=H(key)).json()
    assert one["code"] == "S21" and one["slicer_preset"] == "Generic PLA"
    assert c.get("/api/mobile/v1/spools/999", headers=H(key)).status_code == 404
    # The hub's cache follows the farm library.
    from app.services.inventory import refresh_spools
    with session_scope() as db:
        assert refresh_spools(db)["source"] == "farm"
    with session_scope() as db:
        row = db.query(SpoolCache).filter(SpoolCache.spool_id == 21).one()
        assert row.filament_id == 10 and row.remaining_g == 600


def test_create_spools_learns_the_barcode_and_offers_labels(farm, key):
    c = _client()
    _, fake = farm
    code = "0" + str(_s.randbelow(10**11)).zfill(11)
    r = c.post("/api/mobile/v1/spools", headers=H(key), json={
        "filament": {"vendor": "Sunlu", "name": "PLA+ Klein Blue", "material": "PLA+", "color_hex": "264e9e",
                     "weight_g": 1000, "price": 20, "barcode": code},
        "quantity": 2, "remaining_g": 1000, "location_code": "lshelf1", "lot": "B7",
        "print_labels": {"format": "pdf_62x29"}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["spools"]) == 2 and all(x["code"].startswith("S") for x in d["spools"])
    assert d["filament"]["vendor"] == "Sunlu" and d["filament"]["material"] == "PLA+"
    bulk = [x for x in fake.calls if x[1] == SI + "/spools/bulk"][-1][2]
    assert bulk["quantity"] == 2 and bulk["spool"]["location_id"] == 100 and bulk["spool"]["subtype"] == "Klein Blue"
    assert bulk["spool"]["cost_per_kg"] == 20.0 and bulk["spool"]["note"] == "Lot B7" and bulk["spool"]["weight_used"] == 0
    assert d["labels"]["pdf_url"].startswith("/api/mobile/v1/labels/file?targets=spool%3A")
    pdf = c.get(d["labels"]["pdf_url"], headers=H(key))
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    look = c.get(f"/api/mobile/v1/filaments/lookup?barcode={code}", headers=H(key)).json()
    assert look["source"] == "learned" and look["match"]["id"] == d["filament"]["id"]
    # By filament id; quantity limits
    r = c.post("/api/mobile/v1/spools", headers=H(key), json={"filament_id": 10, "quantity": 1})
    assert r.status_code == 200 and r.json()["spools"][0]["filament_id"] == 10
    assert c.post("/api/mobile/v1/spools", headers=H(key), json={"filament_id": 10, "quantity": 51}).status_code == 422
    assert c.post("/api/mobile/v1/spools", headers=H(key), json={"quantity": 1}).status_code == 422


def test_assign_passes_the_allowed_extruders_and_unassign(farm, key):
    c = _client()
    _, fake = farm
    r = c.post("/api/mobile/v1/spools/21/assign", headers=H(key), json={"target_code": "LSLOT03", "extruder_index": 5})
    assert r.status_code == 409 and r.json() == {"detail": "extruder 5 is not on this slot's path", "allowed": [0, 1]}
    r = c.post("/api/mobile/v1/spools/21/assign", headers=H(key), json={"target_code": "lslot03", "extruder_index": 1})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["extruder_index"] == 1 and d["assigned"]["slot_label"] == "AMS A slot 3"
    r = c.post("/api/mobile/v1/spools/21/assign", headers=H(key), json={"printer": "H2D", "ams_id": 255, "tray_id": 0})
    assert r.status_code == 200 and r.json()["assigned"]["slot_label"] == "External left"
    assert r.json()["extruder_index"] == 1
    assert c.post("/api/mobile/v1/spools/21/assign", headers=H(key), json={"target_code": "LSHELF1"}).status_code == 422
    assert c.post("/api/mobile/v1/spools/21/assign", headers=H(key), json={"printer": "H2D"}).status_code == 422
    r = c.post("/api/mobile/v1/spools/21/unassign", headers=H(key))
    assert r.status_code == 200 and r.json()["assigned"] is None


def test_use_weigh_move_and_archive(farm, key):
    c = _client()
    _, fake = farm
    assert c.post("/api/mobile/v1/spools/21/use", headers=H(key), json={"grams": 25}).json() == {"ok": True}
    assert fake.spools[21]["weight_used"] == 425
    r = c.patch("/api/mobile/v1/spools/21", headers=H(key), json={"remaining_weight": 500, "comment": "weighed"})
    assert r.status_code == 200 and r.json()["remaining_g"] == 500 and fake.spools[21]["note"] == "weighed"
    r = c.post("/api/mobile/v1/spools/21/move", headers=H(key), json={"location_code": "LSHELF1"})
    assert r.status_code == 200 and r.json()["location_code"] == "LSHELF1"
    assert c.post("/api/mobile/v1/spools/21/move", headers=H(key), json={"location_code": "LNOPE00"}).status_code == 404
    assert c.delete("/api/mobile/v1/spools/21", headers=H(key)).json()["ok"] is True
    assert fake.spools[21]["archived_at"]
    assert all(x["id"] != 21 for x in c.get("/api/mobile/v1/spools", headers=H(key)).json()["spools"])


def test_inventory_permission_needed(farm):
    c = _client()
    viewer = _key(["read"])
    assert c.get("/api/mobile/v1/spools", headers=H(viewer)).status_code == 200
    assert c.post("/api/mobile/v1/spools/21/use", headers=H(viewer), json={"grams": 1}).status_code == 403
    assert c.get("/api/mobile/v1/scan/S21", headers=H(viewer)).status_code == 403
    assert c.post("/api/mobile/v1/labels/printers", headers=H(_key(["inventory"])),
                  json={"host": "192.0.2.9"}).status_code == 403


def test_filaments_with_catalog_hits(farm, key, monkeypatch):
    from app.services import filaments
    monkeypatch.setattr(filaments, "spoolmandb", lambda refresh=False: [
        {"id": "sunlu_pla+_white", "manufacturer": "Sunlu", "material": "PLA+", "name": "White", "color_hex": "FFFFFF",
         "weight": 1000, "spool_weight": 165, "diameter": 1.75},
        {"id": "acme_pla_red", "manufacturer": "Acme", "material": "PLA", "name": "Red", "color_hex": "FF0000",
         "weight": 1000, "diameter": 1.75}])
    c = _client()
    d = c.get("/api/mobile/v1/filaments?q=acme", headers=H(key)).json()["filaments"]
    assert d[0]["source"] == "farm" and d[0]["id"] == 10
    assert d[-1]["source"] == "catalog" and d[-1]["id"] is None and d[-1]["catalog_id"] == "acme_pla_red"
    d = c.get("/api/mobile/v1/filaments?q=sunlu&material=PLA%2B", headers=H(key)).json()["filaments"]
    assert [x["vendor"] for x in d if x["source"] == "catalog"] == ["Sunlu"]


def test_parse_text_rules():
    from app.services.barcodes import parse_text, valid_upc
    g = parse_text("SUNLU PLA+ Filament 1.75mm, 1KG Spool, Klein Blue")
    assert g["vendor"] == "Sunlu" and g["material"] == "PLA+" and g["weight_g"] == 1000 and g["diameter_mm"] == 1.75
    g = parse_text("Bambu Lab PETG-CF 1000g Black")
    assert g["vendor"] == "Bambu Lab" and g["material"] == "PETG-CF" and g["color_name"] == "Black"
    g = parse_text("eSun ABS+ 750 g 2.85 mm natural")
    assert g["material"] == "ABS" and g["weight_g"] == 750 and g["diameter_mm"] == 2.85
    assert parse_text("Polymaker Silk PLA Gold 1kg")["name"] == "Silk Gold"
    assert valid_upc("036000291452") and not valid_upc("036000291453") and valid_upc("4006381333931")


def test_upc_lookup_cached_and_never_fails(farm, key, monkeypatch):
    from app.services import barcodes
    hits = []

    def fake_get(url, params=None, timeout=None, headers=None):
        hits.append(params["upc"])
        if params["upc"] == "036000291452":
            return httpx.Response(200, json={"code": "OK", "items": [{"title": "Acme PLA Filament 1.75mm 1kg Red",
                                                                       "brand": "Acme", "description": "",
                                                                       "images": ["https://img.example/a.jpg"]}]})
        if params["upc"] == "4006381333931":
            raise httpx.ConnectTimeout("down")
        return httpx.Response(200, json={"code": "OK", "items": []})
    monkeypatch.setattr(barcodes.httpx, "get", fake_get)
    c = _client()
    d = c.get("/api/mobile/v1/filaments/lookup?barcode=036000291452", headers=H(key)).json()
    assert d["source"] == "upc" and d["product"]["brand"] == "Acme" and d["product"]["weight_g"] == 1000
    assert d["candidates"][0]["id"] == 10 and d["match"] is None
    d2 = c.get("/api/mobile/v1/filaments/lookup?barcode=036000291452", headers=H(key)).json()
    assert d2["source"] == "upc" and hits.count("036000291452") == 1          # cached
    down = c.get("/api/mobile/v1/filaments/lookup?barcode=4006381333931", headers=H(key))
    assert down.status_code == 200 and down.json()["source"] == "none"
    # learn it, then it is a known barcode
    r = c.post("/api/mobile/v1/filaments/barcodes", headers=H(key), json={"barcode": "036000291452", "filament_id": 10})
    assert r.status_code == 200, r.text
    assert c.get("/api/mobile/v1/filaments/lookup?barcode=036000291452", headers=H(key)).json()["source"] == "learned"
    assert c.post("/api/mobile/v1/filaments/barcodes", headers=H(key),
                  json={"barcode": "1", "filament_id": 99999}).status_code == 404


def test_identify_falls_back_to_rules_and_uses_the_model(farm, key, monkeypatch):
    from app.config import settings
    from app.services import barcodes
    # the hub has no vision server by default; this test brings its own (the HTTP calls are faked below)
    monkeypatch.setattr(settings(), "vision_url", "http://ollama.test:11434")

    def down(*a, **kw):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(barcodes.httpx, "post", down)
    c = _client()
    r = c.post("/api/mobile/v1/filaments/identify", headers=H(key), json={"text": "ACME PLA 1kg red 1.75mm"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["engine"] == "rules" and d["guess"]["material"] == "PLA" and d["candidates"][0]["id"] == 10
    sent = {}

    def up(url, json=None, timeout=None):
        sent.update(json)
        return httpx.Response(200, json={"message": {"content": '{"vendor": "Acme", "material": "pla", '
                                                                '"color_hex": "ff0000", "weight_g": "1000", "name": null}'}},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(barcodes.httpx, "post", up)
    r = c.post("/api/mobile/v1/filaments/identify", headers=H(key),
               files={"photo": ("p.jpg", b"\xff\xd8jpegdata", "image/jpeg")}, data={"text": "1.75mm"})
    d = r.json()
    assert d["engine"].startswith("ollama:") and d["guess"]["material"] == "PLA" and d["guess"]["color_hex"] == "#FF0000"
    assert d["guess"]["diameter_mm"] == 1.75 and sent["messages"][0]["images"] and sent["format"] == "json"
    assert c.post("/api/mobile/v1/filaments/identify", headers=H(key), json={}).status_code == 422


def test_locations_tree_crud_and_sync(farm, key):
    c = _client()
    tree = c.get("/api/mobile/v1/locations", headers=H(key)).json()["locations"]
    shelf = next(x for x in tree if x["code"] == "LSHELF1")
    assert [ch["code"] for ch in shelf["children"]] == ["LDRY001"]
    assert len(c.get("/api/mobile/v1/locations?kind=ams_slot", headers=H(key)).json()["locations"]) == 2
    r = c.post("/api/mobile/v1/locations", headers=H(key), json={"name": "Bin 4", "kind": "storage", "parent_id": 100})
    assert r.status_code == 200 and r.json()["code"]
    lid = r.json()["id"]
    assert c.patch(f"/api/mobile/v1/locations/{lid}", headers=H(key), json={"note": "top"}).json()["note"] == "top"
    assert c.delete(f"/api/mobile/v1/locations/{lid}", headers=H(key)).json() == {"ok": True}
    assert c.post("/api/mobile/v1/locations", headers=H(key), json={"kind": "storage"}).status_code == 422
    assert len(c.post("/api/mobile/v1/locations/sync-printers", headers=H(key)).json()["locations"]) == 3


def test_scan_resolves_every_kind(farm, key, monkeypatch):
    from app.db import session_scope
    from app.models import Order
    c = _client()
    d = c.get("/api/mobile/v1/scan/s21", headers=H(key)).json()
    assert d["kind"] == "spool" and d["spool"]["id"] == 21 and "assign" in d["actions"] and "unassign" in d["actions"]
    d = c.get("/api/mobile/v1/scan/https://hub.example/l/LSLOT02", headers=H(key)).json()
    assert d["kind"] == "ams_slot" and d["slot"]["extruders"] == [0] and d["slot"]["loaded"]["code"] == "S21"
    assert d["slot"]["label"] == "AMS A slot 2" and d["actions"][0] == "assign"
    d = c.get("/api/mobile/v1/scan/LSLOT03", headers=H(key)).json()
    assert d["slot"]["extruders"] == [0, 1] and d["slot"]["loaded"] is None
    assert c.get("/api/mobile/v1/scan/LH2D000", headers=H(key)).json()["kind"] == "printer"
    d = c.get("/api/mobile/v1/scan/LDRY001", headers=H(key)).json()
    assert d["kind"] == "location" and [x["code"] for x in d["location"]["spools"]] == ["S21"] and "move_here" in d["actions"]
    assert c.get("/api/mobile/v1/scan/LZZZZZZ", headers=H(key)).json()["kind"] == "unknown"
    assert c.get("/api/mobile/v1/scan/hello", headers=H(key)).json()["kind"] == "unknown"
    from app.services import barcodes
    monkeypatch.setattr(barcodes, "upc_lookup", lambda db, code: None)
    d = c.get("/api/mobile/v1/scan/012345678905", headers=H(key)).json()
    assert d["kind"] == "barcode" and d["lookup"]["source"] == "none"
    n = str(80000 + _s.randbelow(9999))
    with session_scope() as db:
        o = Order(woo_order_id=int(n), number=n, status="processing", customer="Ann")
        db.add(o)
        db.flush()
        oid = o.id
    d = c.get(f"/api/mobile/v1/scan/O{oid}", headers=H(key)).json()
    assert d["kind"] == "order" and d["order"]["number"] == n
    assert c.get("/api/mobile/v1/scan/P999999", headers=H(key)).json()["kind"] == "unknown"


def test_label_files_and_printers(farm, key, monkeypatch):
    from app.services import labels
    c = _client()
    for fmt in ("pdf_avery5160", "pdf_62x29", "pdf_62x100"):
        r = c.post("/api/mobile/v1/labels", headers=H(key), json={"targets": [{"kind": "spool", "id": 21},
                                                                              {"kind": "location", "id": "LSLOT02"}],
                                                                  "format": fmt})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
    r = c.post("/api/mobile/v1/labels", headers=H(key), json={"targets": [{"kind": "spool", "id": 21}], "format": "png_62x29"})
    assert r.headers["content-type"] == "image/png"
    from PIL import Image
    im = Image.open(io.BytesIO(r.content))
    assert im.size == (732, 343)
    r = c.post("/api/mobile/v1/labels", headers=H(key), json={"targets": [{"kind": "spool", "id": 21},
                                                                          {"kind": "location", "id": 201}],
                                                              "format": "png_40x30"})
    assert r.headers["content-type"] == "application/zip"
    assert len(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == 2
    assert c.post("/api/mobile/v1/labels", headers=H(key), json={"targets": [], "format": "pdf_62x29"}).status_code == 422
    assert c.post("/api/mobile/v1/labels", headers=H(key),
                  json={"targets": [{"kind": "spool", "id": 21}], "format": "bogus"}).status_code == 422
    # the QR holds the hub link
    item = labels.spool_item({"id": 21, "code": "S21", "name": "Red", "vendor": "Acme", "material": "PLA",
                              "color_hex": "#FF0000", "label_weight_g": 1000})
    assert labels.code_url(item["code"]) == "http://test/l/S21"
    # label printers: add (settings), list, print (Brother raster and ZPL), test, delete
    r = c.post("/api/mobile/v1/labels/printers", headers=H(key),
               json={"name": "QL", "kind": "brother_ql", "host": "192.0.2.10", "model": "QL-820NWB", "tape": "62x29"})
    assert r.status_code == 200, r.text
    ql = r.json()["id"]
    r = c.post("/api/mobile/v1/labels/printers", headers=H(key),
               json={"name": "Zebra", "kind": "zpl", "host": "192.0.2.11", "tape": "50x25"})
    zid = r.json()["id"]
    assert c.post("/api/mobile/v1/labels/printers", headers=H(key),
                  json={"kind": "brother_ql", "host": "192.0.2.12", "tape": "99x99"}).status_code == 422
    ps = c.get("/api/mobile/v1/labels/printers", headers=H(key)).json()["printers"]
    assert [x["kind"] for x in ps] == ["brother_ql", "zpl", "pdf"]
    sent = []
    monkeypatch.setattr(labels, "_send", lambda host, port, data, timeout=10.0: sent.append((host, port, data)))
    r = c.post("/api/mobile/v1/labels", headers=H(key), json={"targets": [{"kind": "spool", "id": 21}], "format": "printer",
                                                              "printer_id": ql, "copies": 2})
    assert r.json() == {"printed": 2} and sent[-1][0] == "192.0.2.10" and sent[-1][1] == 9100 and len(sent[-1][2]) > 1000
    r = c.post("/api/mobile/v1/labels", headers=H(key), json={"targets": [{"kind": "location", "id": "LSHELF1"}],
                                                              "format": "printer", "printer_id": zid})
    assert r.json() == {"printed": 1} and sent[-1][2].startswith(b"^XA^PW400^LL200^FO0,0^GFA,")
    assert c.post("/api/mobile/v1/labels", headers=H(key), json={"targets": [{"kind": "spool", "id": 21}],
                                                                 "format": "printer", "printer_id": 77}).status_code == 404
    monkeypatch.setattr(labels, "probe", lambda p: (False, "ConnectionRefusedError"))
    assert c.post(f"/api/mobile/v1/labels/printers/{zid}/test", headers=H(key)).json()["ok"] is False
    assert c.delete(f"/api/mobile/v1/labels/printers/{zid}", headers=H(key)).json() == {"ok": True}
    assert c.delete(f"/api/mobile/v1/labels/printers/{ql}", headers=H(key)).json() == {"ok": True}


def test_printer_detail_hardware_and_cover(farm, key):
    c = _client()
    d = c.get("/api/mobile/v1/printers/H2D", headers=H(key)).json()
    assert d["progress"] == 37.0 and d["layer"] == 50 and d["total_layers"] == 200 and d["job_name"] == "Vase"
    assert d["eta"] and d["cover_available"] is True
    assert d["nozzles"]["nozzle_count"] == 2 and d["paths"]["slots"] and d["loaded"]["slots"][0]["assignment"]["spool_id"] == 21
    r = c.get("/api/mobile/v1/printers/H2D/cover?view=top", headers=H(key))
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.endswith(b"plate-top")
    assert c.get("/api/mobile/v1/printers/MK4S/cover", headers=H(key)).status_code == 404
    assert c.get("/api/mobile/v1/printers/H2D/cover?view=iso", headers=H(key)).status_code == 422
    r = c.put("/api/mobile/v1/printers/H2D/nozzles/0", headers=H(key),
              json={"diameter_mm": 0.6, "nozzle_type": "hardened_steel", "volume_type": "High Flow"})
    assert r.status_code == 200 and r.json()["nozzles"][0]["source"] == "manual"
    assert c.put("/api/mobile/v1/printers/H2D/nozzles/0", headers=H(key),
                 json={"diameter_mm": 0.6, "nozzle_type": "gold", "volume_type": "High Flow"}).status_code == 422
    assert c.delete("/api/mobile/v1/printers/H2D/nozzles/0", headers=H(key)).json()["nozzles"][0]["source"] == "mqtt"
    assert c.delete("/api/mobile/v1/printers/H2D/nozzles/0", headers=H(key)).status_code == 404
    r = c.put("/api/mobile/v1/printers/H2D/filament-paths", headers=H(key), json={"track_switch_mode": "installed"})
    assert r.status_code == 200 and r.json()["track_switch"]["mode"] == "installed"
    assert c.get("/api/mobile/v1/printers/Nope/nozzles", headers=H(key)).status_code == 404
    assert c.get("/api/mobile/v1/printers/H2D/loaded", headers=H(key)).json()["printer_name"] == "H2D"


def test_live_video_relays_the_farms_hls_with_the_phone_key(farm, key):
    c = _client()
    r = c.get("/api/mobile/v1/printers/H2D/live.m3u8", headers=H(key))
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert b"hls/playlist.m3u8?id=AbC123" in r.content  # relative: the player comes back to /printers/H2D/hls/
    r = c.get("/api/mobile/v1/printers/H2D/hls/segment.m4s?id=AbC123&n=4", headers=H(key))
    assert r.status_code == 200 and r.content == b"seg:segment.m4s:4" and r.headers["content-type"] == "video/mp4"
    assert c.get("/api/mobile/v1/printers/H2D/hls/init.mp4?id=AbC123", headers=H(key)).status_code == 200
    # Only the live view's own files and parameters reach the farm.
    assert c.get("/api/mobile/v1/printers/H2D/hls/..%2Fstreams?id=AbC123", headers=H(key)).status_code == 404
    assert c.get("/api/mobile/v1/printers/H2D/hls/segment.m4s?id=a/b", headers=H(key)).status_code == 400
    assert c.get("/api/mobile/v1/printers/H2D/hls/segment.m4s", headers=H(key)).status_code == 400
    assert c.get("/api/mobile/v1/printers/H2D/hls/segment.m4s?id=Gone99", headers=H(key)).status_code == 404
    # A printer without a camera, and a key that is not the hub's.
    assert c.get("/api/mobile/v1/printers/MK4S/live.m3u8", headers=H(key)).status_code == 404
    bogus = {"Authorization": "Bearer ahk_00000000_notakeyatallnotakeyatall"}
    assert c.get("/api/mobile/v1/printers/H2D/live.m3u8", headers=bogus).status_code == 401
    assert c.get("/api/mobile/v1/printers/H2D/hls/segment.m4s?id=AbC123", headers=bogus).status_code == 401


def test_driver_maps_more_printer_commands(farm):
    from app.clients.farm_base import FarmUnsupported
    c, fake = farm
    c.command("H2D", "light_on")
    assert fake.calls[-1][1] == API + "/printers/3/chamber-light" and fake.calls[-1][3] == {"on": "true"}
    c.command("H2D", "home")
    assert fake.calls[-1][1] == API + "/printers/3/home-axes"
    c.command("H2D", "preheat", {"material": "PETG"})
    temps = [x for x in fake.calls if "/temperature/" in x[1]][-2:]
    assert temps[0][3] == {"target": "240", "nozzle": "0"} and temps[1][3] == {"target": "70"}
    c.command("H2D", "cooldown")
    assert [x[3]["target"] for x in fake.calls if "/temperature/" in x[1]][-3:] == ["0", "0", "0"]
    with pytest.raises(FarmUnsupported):
        c.command("H2D", "dance")


def test_store_from_the_phone(farm, key, monkeypatch):
    from app import sources as srcs
    from app.db import session_scope
    from app.models import Job, Model, Order, Product
    from app.clients.base import ApiError
    c = _client()
    monkeypatch.setattr(srcs, "search", lambda db, platform, q, lics, sort, offset, limit: {
        "total": 2, "items": [{"id": "1", "title": "Vase", "license_raw": "CC BY"},
                              {"id": "2", "title": "Cup", "license_raw": "CC BY-NC"}]})
    d = c.get("/api/mobile/v1/sources/search?platform=printables&q=vase", headers=H(key)).json()
    assert [x["title"] for x in d["items"]] == ["Vase"] and d["items"][0]["sellable"] is True
    assert len(c.get("/api/mobile/v1/sources/search?platform=printables&q=vase&sellable=false",
                     headers=H(key)).json()["items"]) == 2
    assert c.get("/api/mobile/v1/sources/search?platform=etsy", headers=H(key)).status_code == 422
    assert "printables" in c.get("/api/mobile/v1/sources/status", headers=H(key)).json()
    r = c.post("/api/mobile/v1/sources/import", headers=H(key), json={"url": "https://www.printables.com/model/123456-vase"})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    assert jid and r.json()["platform"] == "printables"
    j = c.get(f"/api/mobile/v1/jobs/{jid}", headers=H(key)).json()
    assert j["status"] == "queued" and j["result"] is None
    tag = _s.token_hex(3)
    with session_scope() as db:
        m = Model(code=f"AP-T{tag}", name=f"Phone vase {tag}", slug=f"phone-vase-{tag}", license_code="CC-BY-NC")
        db.add(m)
        db.flush()
        pr = Product(model_id=m.id, sku=f"PV-{tag}", title="Phone vase", price=20)
        db.add(pr)
        db.flush()
        pid = pr.id
        jj = db.get(Job, jid)
        jj.state, jj.result = "done", {"model_id": m.id, "product_id": pid, "files": 1}
        o = Order(woo_order_id=int(_s.randbelow(10**6)) + 10**6, number=f"N{tag}", status="processing")
        db.add(o)
        db.flush()
        oid = o.id
    j = c.get(f"/api/mobile/v1/jobs/{jid}", headers=H(key)).json()
    assert j["status"] == "done" and j["result"]["product_id"] == pid
    d = c.get(f"/api/mobile/v1/products/{pid}", headers=H(key)).json()
    assert d["title"] == "Phone vase" and d["license"]["problems"] and d["bom"]["filaments"] == 0
    r = c.patch(f"/api/mobile/v1/products/{pid}", headers=H(key),
                json={"title": "Phone vase XL", "price": 25, "sale_price": 19.5, "categories": [f"Vases {tag}"]})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Phone vase XL" and r.json()["category"] == f"Vases {tag}" and r.json()["sale_price"] == 19.5
    assert c.patch(f"/api/mobile/v1/products/{pid}", headers=H(key), json={"sale_price": 30}).status_code == 422
    # the model behind it: its fields come with the product and can be edited
    assert d["model"]["name"] == f"Phone vase {tag}" and d["model"]["tags"] == []
    mid = d["model_id"]
    r = c.patch(f"/api/mobile/v1/models/{mid}", headers=H(key),
                json={"name": "  Phone vase, tall  ", "summary": "A tall vase.", "tags": ["vase", " ", "home "],
                      "description": "Long text.\nSecond line."})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Phone vase, tall" and r.json()["tags"] == ["vase", "home"]
    assert c.get(f"/api/mobile/v1/products/{pid}", headers=H(key)).json()["model"]["description"] == "Long text.\nSecond line."
    assert c.patch(f"/api/mobile/v1/models/{mid}", headers=H(key), json={"name": " "}).status_code == 400
    assert f"Vases {tag}" in c.get("/api/mobile/v1/categories", headers=H(key)).json()["categories"]
    assert c.patch("/api/mobile/v1/models/999999999", headers=H(key), json={"summary": "x"}).status_code == 404
    assert c.post(f"/api/mobile/v1/products/{pid}/push", headers=H(key)).status_code == 400   # NC licence
    # order status through the store
    from app import clients

    class Woo:
        def update_order(self, oid_, data):
            assert data == {"status": "completed"}
            return {"id": oid_, "status": "completed"}
    monkeypatch.setattr(clients, "woo", lambda: Woo())
    r = c.post(f"/api/mobile/v1/orders/{oid}/status", headers=H(key), json={"status": "completed"})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert c.post(f"/api/mobile/v1/orders/{oid}/status", headers=H(key), json={"status": "refunded"}).status_code == 422

    class Down:
        def update_order(self, *a):
            raise ApiError("woo", 500, "boom")
    monkeypatch.setattr(clients, "woo", lambda: Down())
    assert c.post(f"/api/mobile/v1/orders/{oid}/status", headers=H(key), json={"status": "on-hold"}).status_code == 502


class FakeLanSpoolman:
    def __init__(self):
        self.fils = [{"id": 1, "name": "PLA Red", "material": "PLA", "color_hex": "ff0000", "weight": 1000, "price": 20,
                      "vendor": {"id": 1, "name": "Acme"}},
                     {"id": 2, "name": "PETG Clear", "material": "PETG", "color_hex": "eeeeee", "weight": 1000,
                      "vendor": {"id": 2, "name": "Zed"}},
                     {"id": 3, "name": "TPU Black", "material": "TPU", "color_hex": "000000", "weight": 500,
                      "vendor": {"id": 2, "name": "Zed"}}]
        self.sp = [{"id": 7, "filament": self.fils[0], "remaining_weight": 400, "location": "Shelf", "comment": "old",
                    "archived": False},
                   {"id": 8, "filament": self.fils[1], "remaining_weight": 900, "archived": False},
                   {"id": 9, "filament": self.fils[1], "remaining_weight": 0, "archived": True}]

    def spools(self, allow_archived=False):
        return [s for s in self.sp if allow_archived or not s["archived"]]

    def filaments(self):
        return self.fils


def test_migrate_to_farm_is_idempotent_and_turns_the_hub_spoolman_off(farm, monkeypatch):
    from app import clients
    from app.db import session_scope
    from app.models import Connection, Variant, Product, Model
    from app.services import connections, spool_library
    from app.tenant import unscoped
    _, fake = farm
    lan = FakeLanSpoolman()
    monkeypatch.setattr(clients, "spoolman", lambda: lan)
    tag = _s.token_hex(3)
    with session_scope() as db:
        connections.save(db, "spoolman", {"url": "http://192.0.2.5:7912"}, None, enabled=True)
        m = Model(code=f"AP-M{tag}", name=f"Mig {tag}", slug=f"mig-{tag}")
        db.add(m)
        db.flush()
        pr = Product(model_id=m.id, sku=f"MG-{tag}", title="Mig")
        db.add(pr)
        db.flush()
        v = Variant(product_id=pr.id, sku=f"MG-{tag}-R", color_name="Red", spoolman_filament_id=1)
        db.add(v)
        db.flush()
        vid = v.id
    with session_scope() as db:
        dry = spool_library.migrate_to_farm(db, dry_run=True)
    assert dry["dry_run"] and dry["spools_copied"] == 3
    before = len(fake.spools)
    with session_scope() as db:
        r = spool_library.migrate_to_farm(db)
    assert r["spools_copied"] == 3 and r["spools_failed"] == [] and r["spoolman_disabled"] is True
    assert len(fake.spools) == before + 3
    made = [s for s in fake.spools.values() if "(hub Spoolman spool #7)" in (s["note"] or "")]
    assert made and made[0]["weight_used"] == 600 and made[0]["storage_location"] == "Shelf"
    assert any(s["archived_at"] for s in fake.spools.values() if "#9)" in (s["note"] or ""))
    assert r["filaments_without_spools"] == [{"id": 3, "name": "TPU Black", "vendor": "Zed"}]
    with session_scope() as db:
        assert db.get(Variant, vid).spoolman_filament_id == 10      # Acme PLA Red is farm filament 10
        with unscoped():
            assert db.query(Connection).filter(Connection.kind == "spoolman", Connection.team_id == 1).one().enabled is False
    with session_scope() as db:
        again = spool_library.migrate_to_farm(db)
    assert again["spools_copied"] == 0 and again["spools_already"] == 3 and len(fake.spools) == before + 3
    with session_scope() as db:
        assert db.get(Variant, vid).spoolman_filament_id == 10
        with unscoped():
            db.query(Connection).filter(Connection.kind == "spoolman", Connection.team_id == 1).delete()
    connections.forget(1)


def test_migrate_job_needs_openprinthq(monkeypatch):
    from app.db import session_scope
    from app.services import spool_library
    monkeypatch.setattr(spool_library, "farm_library", lambda team_id=None: False)
    with session_scope() as db:
        with pytest.raises(spool_library.LibraryError):
            spool_library.migrate_to_farm(db)


def test_lan_spoolman_path_still_works(monkeypatch, key):
    from app.db import session_scope
    from app.models import SpoolCache
    from app.services import spool_library
    monkeypatch.setattr(spool_library, "farm_library", lambda team_id=None: False)
    with session_scope() as db:
        db.query(SpoolCache).delete()
        db.add(SpoolCache(spool_id=5, filament_id=2, vendor="Zed", name="Clear", material="PETG", remaining_g=321.04,
                          location="Rack"))
    d = _client().get("/api/mobile/v1/spools", headers=H(key)).json()
    assert d["source"] == "spoolman" and d["spools"][0]["code"] == "S5" and d["spools"][0]["remaining_g"] == 321.0
    assert _client().post("/api/mobile/v1/spools/5/move", headers=H(key), json={"location_code": "LX"}).status_code == 400
    with session_scope() as db:
        db.query(SpoolCache).delete()


def test_label_page_is_public_and_says_little():
    c = _client()
    r = c.get("/l/S42")
    assert r.status_code == 200 and "Filament spool S42" in r.text and "Get the app" in r.text
    assert "artisanhub://l/S42" in r.text and "intent://l/S42" in r.text
    assert c.get("/l/LAB2C3D").status_code == 200
    assert "can't be read" in c.get("/l/" + "x" * 40).text


# ---------------------------------------------------------------- follow-ups (2026-10-23 evening)

def test_media_with_the_phone_key_and_product_image_urls(farm, key, env):
    import hashlib
    from app.db import session_scope
    from app.models import Image, Model, Product
    from PIL import Image as PImage
    tag = _s.token_hex(3)
    rel = f"originals/t-{tag}.png"
    p = env / "data" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    PImage.new("RGB", (40, 30), (10, 200, 10)).save(p)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    with session_scope() as db:
        m = Model(code=f"AP-I{tag}", name=f"Img {tag}", slug=f"img-{tag}")
        db.add(m)
        db.flush()
        img = Image(model_id=m.id, sha256=sha, filename="a.png", role="hero", store_rel=rel)
        db.add(img)
        pr = Product(model_id=m.id, sku=f"IM-{tag}", title="Img")
        db.add(pr)
        db.flush()
        iid, pid = img.id, pr.id
    c = _client()
    d = c.get(f"/api/mobile/v1/products/{pid}", headers=H(key)).json()
    im = d["images"][0]
    assert im["thumb"].startswith(f"/api/mobile/v1/media/{iid}/thumb") and "/public/img/" in im["public"]
    r = c.get(f"/api/mobile/v1/media/{iid}/original", headers=H(key))
    assert r.status_code == 200 and r.content == p.read_bytes()
    assert c.get(f"/media/{iid}/original", headers=H(key)).status_code == 200      # the web route takes keys too
    assert c.get("/api/mobile/v1/media/999999/thumb", headers=H(key)).status_code == 404
    pub = c.get(im["public"].replace("http://test", ""))
    assert pub.status_code == 200 and pub.headers["content-type"] == "image/jpeg"


def test_locations_and_scan_list_spool_summaries(farm, key):
    c = _client()
    flat = c.get("/api/mobile/v1/locations?flat=true", headers=H(key)).json()["locations"]
    dry = next(x for x in flat if x["code"] == "LDRY001")
    assert dry["spools"] == [{"id": 21, "code": "S21", "vendor": "Acme", "name": "Red", "material": "PLA",
                              "color_hex": "#FF0000", "remaining_g": 600.0, "printer": "H2D", "slot_label": "AMS A slot 2"}]
    tree = c.get("/api/mobile/v1/locations", headers=H(key)).json()["locations"]
    assert next(x for x in tree if x["code"] == "LSHELF1")["children"][0]["spools"][0]["code"] == "S21"
    d = c.get("/api/mobile/v1/scan/LDRY001", headers=H(key)).json()
    assert d["location"]["spools"][0]["code"] == "S21" and d["location"]["spools"][0]["slot_label"] == "AMS A slot 2"


def test_offline_printers_and_list_fields(farm, key):
    _, fake = farm
    fake.status[4] = {"connected": False, "state": "unknown"}
    c = _client()
    ps = {x["name"]: x for x in c.get("/api/mobile/v1/printers", headers=H(key)).json()["printers"]}
    assert ps["MK4S"]["state"] == "offline" and ps["MK4S"]["online"] is False and ps["MK4S"]["busy"] is False
    assert ps["MK4S"]["cover_available"] is False and ps["MK4S"]["job_name"] is None and ps["MK4S"]["nozzle_count"] == 1
    assert ps["H2D"]["job_name"] == "Vase" and ps["H2D"]["cover_available"] is True and ps["H2D"]["nozzle_count"] == 2
    assert not [x for x in fake.calls if x[1].endswith("/cover")]     # the list never fetches images


def test_offline_printer_slot_still_named_and_assign_conflict_is_clean(farm, key):
    _, fake = farm
    fake.status[3] = {"connected": False, "state": "unknown"}
    # the printer reports no AMS: loaded has no slot rows, paths are unknown
    orig = fake.handler

    def handler(request):
        if request.url.path == API + "/printers/3/loaded":
            return httpx.Response(200, json={"printer_id": 3, "printer_name": "H2D", "nozzle_count": 2, "slots": []})
        if request.url.path == SI + "/slot-assignments" and request.method == "POST":
            return httpx.Response(409, json={"detail": "the slot's filament path is unknown while the printer is offline"})
        return orig(request)
    farm[0].client = httpx.Client(base_url="https://ophq.example", transport=httpx.MockTransport(handler),
                                  headers=farm[0].client.headers)
    fake.assign[0].update({"ams_id": 2, "tray_id": 3, "extruder_index": None})
    fake.locations.append({"id": 203, "name": "H2D - AMS C slot 4", "kind": "ams_slot", "parent_id": 200, "printer_id": 3,
                           "ams_id": 2, "tray_id": 3, "code": "LSLOTC4", "note": None, "spool_count": 0})
    c = _client()
    s = next(x for x in c.get("/api/mobile/v1/spools", headers=H(key)).json()["spools"] if x["id"] == 21)
    assert s["assigned"]["slot_label"] == "AMS C slot 4" and s["assigned"]["printer"] == "H2D"
    assert s["assigned"]["extruder_index"] is None
    d = c.get("/api/mobile/v1/scan/LSLOTC4", headers=H(key))
    assert d.status_code == 200, d.text
    slot = d.json()["slot"]
    assert slot["label"] == "AMS C slot 4" and slot["loaded"]["code"] == "S21" and "unassign" in d.json()["actions"]
    r = c.post("/api/mobile/v1/spools/21/assign", headers=H(key), json={"target_code": "LSLOTC4", "extruder_index": 1})
    assert r.status_code == 409 and r.json() == {"detail": "the slot's filament path is unknown while the printer is offline"}


def test_sync_printer_locations_job_runs_when_a_printer_comes_online(farm, key):
    from app.db import session_scope
    from app.kv import set_setting
    from app.services import spool_library
    _, fake = farm
    import time
    with session_scope() as db:
        set_setting(db, "ophq_online_printers", ["H2D", "MK4S"])
        set_setting(db, "ophq_locations_synced_at", time.time())
    calls = lambda: len([x for x in fake.calls if x[1].endswith("/locations/sync-printers")])  # noqa: E731
    with session_scope() as db:
        assert spool_library.sync_printer_locations(db)["skipped"] == "nothing new"
    fake.status[4] = {"connected": False}
    with session_scope() as db:
        spool_library.sync_printer_locations(db)
    n = calls()
    fake.status[4] = {"connected": True, "state": "IDLE"}
    with session_scope() as db:
        r = spool_library.sync_printer_locations(db)
    assert r["came_online"] == ["MK4S"] and calls() == n + 1
    with session_scope() as db:
        set_setting(db, "ophq_locations_synced_at", time.time() - 3600)
    with session_scope() as db:
        assert "locations" in spool_library.sync_printer_locations(db)
    from app.jobs import HANDLERS, PERIODIC
    assert "farm.sync_locations" in HANDLERS and any(k == "farm.sync_locations" for k, _, _ in PERIODIC)


def test_maintenance_hours_merge_names_by_case(farm, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.db import session_scope
    from app.models import FarmJob
    from app.services import maintenance
    t = datetime.now(timezone.utc) - timedelta(days=1)
    tag = _s.token_hex(3)
    with session_scope() as db:
        for i, n in enumerate((f"Voron{tag}", f"voron{tag}")):
            db.add(FarmJob(external_id=f"mh-{tag}-{i}", printer_name=n, status="completed", started_at=t,
                           completed_at=t + timedelta(hours=1), name="x"))
    monkeypatch.setattr(maintenance, "_farm_printer_names", lambda: [f"Voron{tag}"])
    with session_scope() as db:
        h = maintenance.merged_hours(db, [f"Voron{tag}", f"voron{tag}"])
    assert h == {f"Voron{tag}": 2.0}


def test_quotes_carry_the_customer(key):
    import uuid
    from app.db import session_scope
    from app.models import Order, Quote
    n = 3 * 10**6 + _s.randbelow(10**6)
    with session_scope() as db:
        o = Order(woo_order_id=n, number=str(n), status="processing", customer="Ann Lee",
                  raw={"billing": {"first_name": "Ann", "last_name": "Lee", "email": "ann@example.com"}})
        db.add(o)
        db.flush()
        qt = Quote(token=uuid.uuid4().hex[:20], status="ordered", filename="part.stl", stored="x", order_id=o.id)
        db.add(qt)
        db.flush()
        qid = qt.id
    try:
        qs = _client().get("/api/mobile/v1/quotes", headers=H(key)).json()["quotes"]
        q = next(x for x in qs if (x.get("order") or {}).get("number") == str(n))
        assert q["customer_name"] == "Ann Lee" and q["customer_email"] == "ann@example.com"
    finally:
        with session_scope() as db:      # other tests count the waiting quotes
            db.delete(db.get(Quote, qid))


def test_webhook_event_text_is_readable():
    from app.routers.ops import ophq_event_text
    t = ophq_event_text("test", {"title": "OpenPrintHQ Test", "message": "Hello from OpenPrintHQ", "extra": {"a": 1}})
    assert t == "Farm test message: OpenPrintHQ Test - Hello from OpenPrintHQ" and "{" not in t
    assert ophq_event_text("print_complete", {"printer": "H2D", "filename": "Vase.3mf"}) == \
        "Farm print complete: H2D - Vase.3mf"
    assert ophq_event_text("", {}) == "Farm event"


def test_apple_app_site_association_lists_label_links(monkeypatch):
    from app.config import settings
    monkeypatch.setenv("HUB_IOS_TEAM_ID", "ABCDE12345")
    monkeypatch.setenv("HUB_APP_ID", "com.example.hub")
    settings.cache_clear()
    try:
        d = _client().get("/.well-known/apple-app-site-association").json()
        comps = [x["/"] for x in d["applinks"]["details"][0]["components"]]
        assert "/l/*" in comps and any(x.startswith("/pair") for x in comps)
    finally:
        monkeypatch.delenv("HUB_IOS_TEAM_ID")
        monkeypatch.delenv("HUB_APP_ID")
        settings.cache_clear()
