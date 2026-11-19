"""OpenPrintHQ driver of the farm interface (clients/farm_base.py), 2026-10-22.

Talks to an OpenPrintHQ control plane with a scoped API key (`ophqk_...`, scope `control` for everything below)
and reaches the engine API through it: /api/engine/api/v1/... Answers are mapped to the shapes the rest of the hub reads.

What maps how:
- Jobs are the engine's queue items. Status: pending -> queued (waiting_filament / held when the engine says why),
  printing, completed, failed, cancelled and skipped -> cancelled. A job with quantity n is n queue items.
- Files are library files; a file ref is the library file id as a string.
- "set_ready" is the engine's clear-plate; pause, resume and stop are the print controls.
- Webhooks are a webhook notification provider, signed (X-OPHQ-Signature).
- Maintenance: the engine's per-printer maintenance items (print-hour or day intervals) are the schedules; marking
  one done is the engine's "perform". Free-form entries go to the engine's maintenance log
  (/maintenance/printers/{id}/log) and out of service is the engine's per-printer flag
  (/printers/{id}/out-of-service). Engines older than that feature have neither: the driver probes once
  (cached for ENGINE_PROBE_TTL) and only then reports the maintenance_log and out_of_service capabilities.
- Priority: engines with the same feature take a 0-100 priority per queue item (25 low,
  50 normal, 75 high, 100 urgent; higher dispatches first). Older engines get the old behaviour: 75 and up goes
  in at the top of the queue.
- Finished prints: the engine's print webhooks carry no queue id, so last_finished() finds the queue item that
  just ended on that printer, for the hub's per-model print history.
- The hub decides which file prints for an order and sends file jobs.
- Spools (2026-10-23): the engine tenant's Spoolman is the one filament library. The lib_* methods use the engine's
  /spoolman/inventory routes; services/spool_library.py decides when the hub reads them instead of a LAN Spoolman.
- Commands: set_ready/clear_plate, pause, resume, stop/cancel, light_on/off (chamber light), home, set_temperature
  ({nozzle, bed, chamber, extruder_index}), preheat ({material} or {nozzle, bed}) and cooldown.
"""
import os
import time
from urllib.parse import quote

from .base import ApiError, BaseClient
from .farm_base import ALL_COMMANDS, FarmBackend, FarmUnsupported, printer_allows

API = "/api/engine/api/v1"
ENGINE_PROBE_TTL = 600.0  # seconds a definite answer about the engine's features is kept
LOG_CATEGORIES = {"inspection", "cleaning", "lubrication", "calibration", "consumable", "repair", "upgrade", "other"}
BUSY = {"RUNNING", "PREPARE", "SLICING", "PAUSE", "PRINTING", "PAUSED", "HEATING"}
STATUS = {"printing": "printing", "completed": "completed", "failed": "failed", "cancelled": "cancelled",
          "skipped": "cancelled"}
# Engine webhook events the hub wants (notification provider flags).
EVENT_FLAGS = ("on_print_start", "on_print_complete", "on_print_failed", "on_print_stopped", "on_printer_offline",
               "on_printer_error", "on_ai_failure_detection", "on_plate_not_empty", "on_filament_low",
               "on_maintenance_due", "on_queue_job_added", "on_queue_job_assigned", "on_queue_job_started",
               "on_queue_job_waiting", "on_queue_job_skipped", "on_queue_job_failed", "on_queue_completed")
COMMANDS = {"set_ready": "clear-plate", "clear_plate": "clear-plate", "pause": "print/pause",
            "resume": "print/resume", "stop": "print/stop", "cancel": "print/stop"}
# Commands that take query parameters on the engine: command -> (route, params from args)
PREHEAT = {"PLA": (215, 60), "PETG": (240, 70), "ABS": (250, 100), "ASA": (255, 100), "TPU": (225, 45), "PC": (270, 110)}


def hub_status(item: dict) -> str:
    st = item.get("status") or "pending"
    if st != "pending":
        return STATUS.get(st, st)
    why = str(item.get("waiting_reason") or "").lower()
    if "filament" in why or item.get("filament_short"):
        return "waiting_filament"
    if item.get("manual_start"):
        return "held"
    return "assigned" if item.get("printer_id") else "queued"


def _name(item: dict) -> str | None:
    return item.get("library_file_name") or item.get("archive_name")


def _priority(value) -> int:
    return max(0, min(100, int(value)))


class OpenPrintHQClient(BaseClient, FarmBackend):
    service = "openprinthq"
    kind = "openprinthq"
    label = "Farm"
    BASE_CAPABILITIES = frozenset({"files", "jobs", "queue", "priority", "cancel", "printers", "commands", "snapshot",
                                   "filament", "webhooks", "library_folders", "maintenance", "preflight",
                                   "queue_stats", "retries", "spool_library", "locations", "nozzles", "pin_printer"})
    # Present only when the engine has the out-of-service / maintenance log / priority feature.
    PARITY_CAPABILITIES = frozenset({"out_of_service", "maintenance_log"})

    @property
    def CAPABILITIES(self):  # noqa: N802 - same name as the class attribute on FarmBackend
        caps = self.BASE_CAPABILITIES | (self.PARITY_CAPABILITIES if self.engine_parity() else frozenset())
        caps = caps | ({"alarms"} if self.engine_alarms() else frozenset())
        return caps | ({"filament_matching"} if self.engine_filament_matching() else frozenset())

    def engine_filament_matching(self) -> bool:
        """Whether the engine checks filament against jobs and takes mappings (GET /filament-matching/settings).
        Cached like engine_alarms()."""
        now = time.monotonic()
        if self._fmatch is not None and now - self._fmatch_at < ENGINE_PROBE_TTL:
            return self._fmatch
        try:
            self.e("GET", "/filament-matching/settings", retries=1)
            self._fmatch, self._fmatch_at = True, now
        except ApiError as e:
            if e.status in (404, 405, 422):
                self._fmatch, self._fmatch_at = False, now
            else:
                return False
        return self._fmatch

    def engine_alarms(self) -> bool:
        """Whether the engine has the alarm feed (GET /alarms/summary). Cached like engine_parity()."""
        now = time.monotonic()
        if self._alarms is not None and now - self._alarms_at < ENGINE_PROBE_TTL:
            return self._alarms
        try:
            self.e("GET", "/alarms/summary", retries=1)
            self._alarms, self._alarms_at = True, now
        except ApiError as e:
            if e.status in (404, 405, 422):
                self._alarms, self._alarms_at = False, now
            else:
                return False
        return self._alarms

    def engine_parity(self) -> bool:
        """Whether the engine has out of service, the free-form maintenance log and numeric priority (they shipped
        together). Probed with GET /printers/out-of-service: an older engine answers 404, or 422 because it reads
        "out-of-service" as a printer id. A definite answer is cached; a network error is not, and counts as no."""
        now = time.monotonic()
        if self._parity is not None and now - self._parity_at < ENGINE_PROBE_TTL:
            return self._parity
        try:
            self.e("GET", "/printers/out-of-service", retries=1)
            self._parity, self._parity_at = True, now
        except ApiError as e:
            if e.status in (404, 405, 422):
                self._parity, self._parity_at = False, now
            else:
                return False
        return self._parity

    def __init__(self, url, api_key, target_group: str | None = None, verify: bool = True):
        super().__init__(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                         verify=verify, timeout=300.0)
        self.target_group = (target_group or "").strip()
        self._groups = None
        self._parity: bool | None = None
        self._parity_at = 0.0
        self._alarms: bool | None = None
        self._alarms_at = 0.0
        self._fmatch: bool | None = None
        self._fmatch_at = 0.0

    def e(self, method, path, **kw):
        """One engine call through the control plane."""
        return self.request(method, API + path, **kw)

    def push_printer_access(self, body: dict):
        """Who may see and send to which printer, for the farm in team mode (services/printer_access.py)."""
        return self.request("PUT", "/api/team/printer-access", json=body, retries=2, expect_json=False)

    # ---- connection ----
    def ping(self):
        me = self.request("GET", "/api/me")
        if not (me or {}).get("apiKey"):
            raise ApiError(self.service, None, "the key was not accepted as an API key")
        self.e("GET", "/printers/")
        return me

    # ---- printers ----
    def _printers_raw(self) -> list:
        return self.e("GET", "/printers/") or []

    def _status(self, pid) -> dict:
        try:
            return self.e("GET", f"/printers/{int(pid)}/status", retries=1) or {}
        except ApiError:
            return {}

    def _by_name(self, name: str) -> dict:
        """A farm printer by name; an exact match wins, else one that differs only in case ("voron01" in a hub
        profile, "Voron01" on the farm)."""
        printers = self._printers_raw()
        for p in printers:
            if p.get("name") == name:
                return p
        want = (name or "").strip().lower()
        loose = [p for p in printers if str(p.get("name") or "").strip().lower() == want]
        if len(loose) == 1:
            return loose[0]
        raise ApiError(self.service, 404, f"no printer named {name}")

    @staticmethod
    def _commands(p: dict) -> list:
        """The hub commands this printer's connection supports, from the engine's `controls` (engines from
        2026-11-07 on). PrusaLink has no home, temperature or G-code over the network; the chamber light is
        driven only on Bambu and Klipper printers. Without `controls` every command is offered."""
        controls = p.get("controls") or {}
        family = str(p.get("connection_type") or "bambu").lower()
        if not controls:
            return list(ALL_COMMANDS)
        out = ["set_ready", "clear_ready"]
        if controls.get("pause_resume_stop", True):
            out += ["pause", "resume", "cancel", "stop"]
        if controls.get("home", True):
            out.append("home")
        if controls.get("temperature", True):
            out += ["set_temperature", "preheat", "cooldown"]
        if family in ("bambu", "klipper"):
            out += ["light_on", "light_off"]
        return out

    @staticmethod
    def _shape(p: dict, s: dict) -> dict:
        state = str(s.get("state") or "").upper()
        online = bool(s.get("connected"))
        busy = state in BUSY
        temps = s.get("temperatures") or {}
        rem = s.get("remaining_time")
        errs = s.get("hms_errors") or []
        return {"id": p.get("id"), "name": p.get("name"), "model": p.get("model"), "family": p.get("connection_type"),
                "state": (state.lower() or None) if online else "offline", "online": online, "busy": busy and online,
                "nozzle_count": int(p.get("nozzle_count") or 1),
                "commands": OpenPrintHQClient._commands(p),
                "ready": online and not busy and not s.get("awaiting_plate_clear") and state not in ("FINISH", "FAILED"),
                "progress": s.get("progress"), "eta_minutes": int(rem) if isinstance(rem, (int, float)) else None,
                "file": s.get("subtask_name") or s.get("gcode_file"), "job": s.get("subtask_name") or s.get("current_print"),
                "layer": s.get("layer_num"), "total_layers": s.get("total_layers"), "temps": temps,
                "nozzle_temp": temps.get("nozzle"), "bed_temp": temps.get("bed"),
                "error": (errs[0].get("full_code") or errs[0].get("code")) if errs else None}

    def printers(self):
        return {"printers": [self._shape(p, self._status(p["id"])) for p in self._printers_raw() if p.get("is_active", True)]}

    def printer(self, name: str):
        p = self._by_name(name)
        return self._shape(p, self._status(p["id"]))

    def printers_full(self):
        out = []
        for p in self._printers_raw():
            s = self._status(p["id"])
            out.append({"id": p.get("id"), "name": p.get("name"), "model": p.get("model"),
                        "ip_address": p.get("ip_address"), "connection_type": p.get("connection_type"),
                        "capabilities": {"nozzle_count": p.get("nozzle_count", 1),
                                         "ams_units": len(s.get("ams") or []), "location": p.get("location")}})
        return out

    def filament_assignments(self):
        """What each printer has loaded, slot by slot, from the AMS and external trays."""
        out = {}
        for p in self._printers_raw():
            s = self._status(p["id"])
            slots = []
            for unit in s.get("ams") or []:
                for t in unit.get("tray") or unit.get("trays") or []:
                    slots.append(self._slot(t, unit.get("id")))
            for t in s.get("vt_tray") or []:
                slots.append(self._slot(t, "external"))
            out[p.get("id")] = {"assignments": [x for x in slots if x["material_type"]]}
        return {"printers": out}

    @staticmethod
    def _slot(t: dict, unit) -> dict:
        color = str(t.get("tray_color") or "")
        return {"spool_id": None, "unit": unit, "slot": t.get("id"), "material_type": t.get("tray_type"),
                "spool_color_hex": ("#" + color[:6]) if color else None, "name": t.get("tray_sub_brands"),
                "remain_pct": t.get("remain")}

    def command(self, printer: str, command: str, args: dict | None = None, confirm: bool = False):
        args = dict(args or {})
        p = self._by_name(printer)
        pid = int(p["id"])
        if not printer_allows({"commands": self._commands(p)}, command) and command in ALL_COMMANDS:
            raise ApiError(self.service, 400, f"{printer} cannot {command.replace('_', ' ')} over the network")
        route = COMMANDS.get(command)
        if route:
            return self.e("POST", f"/printers/{pid}/{route}", json=args, retries=1) or {"ok": True}
        if command in ("light_on", "light_off"):
            return self.e("POST", f"/printers/{pid}/chamber-light", params={"on": str(command == "light_on").lower()},
                          retries=1) or {"ok": True}
        if command == "home":
            return self.e("POST", f"/printers/{pid}/home-axes", params={"axes": args.get("axes") or "all"},
                          retries=1) or {"ok": True}
        if command in ("set_temperature", "preheat", "cooldown"):
            if command == "cooldown":
                targets = {"nozzle": 0, "bed": 0}
            elif command == "preheat":
                n, b = PREHEAT.get(str(args.get("material") or "PLA").upper(), PREHEAT["PLA"])
                targets = {"nozzle": int(args.get("nozzle", n)), "bed": int(args.get("bed", b))}
            else:
                targets = {k: int(args[k]) for k in ("nozzle", "bed", "chamber") if args.get(k) is not None}
                if not targets:
                    raise ApiError(self.service, 400, "set_temperature needs nozzle, bed or chamber")
            out = {}
            for heater, t in targets.items():
                params = {"target": t}
                if heater == "nozzle":
                    params["nozzle"] = int(args.get("extruder_index") or 0)
                out[heater] = self.e("POST", f"/printers/{pid}/temperature/{heater}", params=params, retries=1)
                if heater == "nozzle" and command == "cooldown" and int(p.get("nozzle_count") or 1) > 1:
                    self.e("POST", f"/printers/{pid}/temperature/nozzle", params={"target": 0, "nozzle": 1}, retries=1)
            return {"ok": True, "targets": targets}
        raise FarmUnsupported(self.kind, "commands")

    def _cameras(self) -> list:
        """Every printer's named cameras, chamber cameras first (OpenPrintHQ's GET /api/cameras), or [] from an
        OpenPrintHQ without them."""
        try:
            return (self.request("GET", "/api/cameras", retries=1) or {}).get("streams") or []
        except ApiError:
            return []

    def camera_sources(self, printer: str) -> list[str]:
        """The printer's live camera streams, a chamber camera first: one list the hub's pages and the app
        read. Cameras switched off or known to be offline are left out."""
        p = self._by_name(printer)
        mine = [c for c in self._cameras() if c.get("printer_id") == p.get("id") and c.get("enabled", True)
                and str(c.get("state", "")).upper() not in ("OFFLINE", "DISABLED") and c.get("stream_name")]
        mine.sort(key=lambda c: 0 if "chamber" in str(c.get("camera_name", "")).lower() else 1)
        return [c["stream_name"] for c in mine]

    def frame(self, source: str, timeout: float = 15) -> bytes | None:
        """One JPEG from one of the farm's camera streams, through OpenPrintHQ (never from the printer directly)."""
        try:
            r = self.request("GET", "/api/cameras/frame.jpeg", params={"src": source}, retries=1, expect_json=False,
                             timeout=timeout)
        except ApiError:
            return None
        return r.content if r.content[:2] == b"\xff\xd8" else None

    def live_playlist(self, source: str) -> bytes | None:
        """Live video from one of the farm's camera streams as HLS in fragmented MP4 (OpenPrintHQ's
        GET /api/cameras/stream.m3u8): go2rtc holds one connection to the camera while it is watched and passes
        the camera's own H.264 through. None from an OpenPrintHQ without it, or when the camera did not start."""
        try:
            r = self.request("GET", "/api/cameras/stream.m3u8", params={"src": source}, retries=1, expect_json=False,
                             timeout=25)
        except ApiError:
            return None
        return r.content if r.content.startswith(b"#EXTM3U") else None

    def live_part(self, file: str, params: dict) -> tuple[bytes, str] | None:
        """A playlist, init segment or media segment of a live view (OpenPrintHQ's GET /api/cameras/hls/<file>)."""
        try:
            r = self.request("GET", f"/api/cameras/hls/{file}", params=params, retries=1, expect_json=False,
                             timeout=25)
        except ApiError:
            return None
        return r.content, r.headers.get("content-type") or "application/octet-stream"

    def snapshot(self, printer: str) -> bytes | None:
        """One camera frame for a printer, preferring a chamber camera; the engine's own snapshot when OpenPrintHQ
        has no named cameras for it, or none of them answered."""
        for src in self.camera_sources(printer)[:2]:
            data = self.frame(src, timeout=20)
            if data:
                return data
        p = self._by_name(printer)
        try:
            r = self.request("GET", f"{API}/printers/{int(p['id'])}/camera/snapshot", retries=1, expect_json=False)
        except ApiError:
            return None
        return r.content if r.content[:2] == b"\xff\xd8" else None

    # ---- files ----
    def list_files(self):
        return [{"uuid": str(f.get("id")), "filename": f.get("filename"), "size": f.get("file_size")}
                for f in (self.e("GET", "/library/files") or [])]

    def upload_file(self, path, filename=None):
        filename = filename or os.path.basename(path)
        with open(path, "rb") as fh:
            r = self.e("POST", "/library/files?generate_stl_thumbnails=false",
                       files={"file": (filename, fh, "application/octet-stream")}, retries=2) or {}
        fid = r.get("duplicate_of") or r.get("id")
        return {"uuid": str(fid), "id": fid, "filename": r.get("filename"), "metadata": r.get("metadata")}

    def delete_library_file(self, file_id):
        return self.e("DELETE", f"/library/files/{int(file_id)}")

    def _folders(self) -> list:
        flat = []

        def walk(items, parent):
            for f in items or []:
                flat.append({"id": f.get("id"), "name": f.get("name"), "parent_id": f.get("parent_id", parent)})
                walk(f.get("children"), f.get("id"))
        walk(self.e("GET", "/library/folders") or [], None)
        return flat

    def library_folder(self, name: str, parent=None) -> dict:
        name = name[:120]
        for f in self._folders():
            if f["name"] == name and (f["parent_id"] or None) == (parent or None):
                return {"folder": f, **f}
        f = self.e("POST", "/library/folders", json={"name": name, "parent_id": parent}) or {}
        return {"folder": f, **f}

    def move_library_file(self, file_id, folder_id):
        return self.e("POST", "/library/files/move", json={"file_ids": [int(file_id)], "folder_id": folder_id})

    def delete_library_folder(self, folder_id):
        return self.e("DELETE", f"/library/folders/{int(folder_id)}")

    # ---- jobs and queue ----
    def _group_id(self):
        if not self.target_group:
            return None
        if self.target_group.isdigit():
            return int(self.target_group)
        if self._groups is None:
            self._groups = {g.get("name"): g.get("id") for g in (self.e("GET", "/printer-groups/") or [])}
        gid = self._groups.get(self.target_group)
        if gid is None:
            raise ApiError(self.service, 404, f"no printer group named {self.target_group}")
        return gid

    def submit_job(self, file_ref=None, quantity=1, job_name=None, priority=None, max_retries=None,
                   printer=None, start=False):
        """Queue a library file. With printer (a farm printer name) the job is pinned to it and waits for it if
        it is busy; otherwise the farm picks: the connection's printer group if set, else any printer of the model
        the file was sliced for."""
        fid = int(file_ref)
        if priority is not None and self.engine_parity():
            body = {"library_file_id": fid, "priority": _priority(priority)}
        else:
            body = {"library_file_id": fid, "insert_at_top": bool(priority and int(priority) >= 75)}
        if max_retries:
            body["max_retries"] = max(0, min(10, int(max_retries)))
        gid = None if printer else self._group_id()
        if printer:
            body["printer_id"] = self.printer_id(printer)
        elif gid is not None:
            body["target_group_id"] = gid
        else:
            meta = (self.e("GET", f"/library/files/{fid}") or {}).get("metadata") or {}
            model = meta.get("sliced_for_model") or meta.get("printer_model")
            if model:
                body["target_model"] = model
        ids = []
        for _ in range(max(1, int(quantity or 1))):
            r = self.e("POST", "/queue/", json=body, retries=1) or {}
            if r.get("id") is not None:
                ids.append(r["id"])
        return {"job_ids": ids, "jobs": ids}

    def preflight(self, job_id) -> dict:
        """Why a queued item isn't printing: the engine's waiting reason plus, per candidate printer, whether it is
        eligible and why not (offline, busy, plate not cleared, missing filament, needs a nozzle or plate)."""
        return self.e("GET", f"/queue/{int(job_id)}/preflight") or {}

    def swap_job_file(self, job_id, file_ref, printer_id: int):
        """Point a pending queue item at another library file and pin it to one printer (the plate reslicer's
        swap; the item keeps its id, position, priority and retries)."""
        return self.e("PATCH", f"/queue/{int(job_id)}", retries=1,
                      json={"library_file_id": int(file_ref), "printer_id": int(printer_id), "target_model": None})

    def move_job(self, job_id, printer_id: int):
        """Pin a waiting queue item to another printer (the engine rechecks its filament there)."""
        return self.e("PATCH", f"/queue/{int(job_id)}", retries=1,
                      json={"printer_id": int(printer_id), "target_model": None})

    def configure_slot(self, printer: str, ams_id: int, tray_id: int, material: str, color_hex: str,
                       temp_min: int, temp_max: int):
        """Tell a Bambu printer what is loaded in an AMS slot (as if set on its screen). The engine picks the
        generic filament preset for the material."""
        return self.e("POST", f"/printers/{self.printer_id(printer)}/slots/{int(ams_id)}/{int(tray_id)}/configure",
                      retries=1, params={"tray_info_idx": "", "tray_type": material, "tray_sub_brands": material,
                                         "tray_color": f"{color_hex}FF", "nozzle_temp_min": temp_min,
                                         "nozzle_temp_max": temp_max})

    def set_installed_plate(self, printer: str, slicer_type: str | None):
        """The engine's record of the plate on a printer (its plate check compares jobs against it)."""
        return self.e("PATCH", f"/printers/{self.printer_id(printer)}", json={"installed_plate": slicer_type},
                      retries=1)

    def set_plate_detection(self, printer: str, enabled: bool):
        """Bambu build plate marker detection on the printer itself: off for plates without Bambu's marker."""
        return self.e("POST", f"/printers/{self.printer_id(printer)}/print-options", retries=1,
                      params={"module_name": "buildplate_marker_detector", "enabled": "true" if enabled else "false"})

    def queue_stats(self, days: int = 30) -> dict:
        return self.e("GET", "/queue/stats", params={"days": max(1, min(365, int(days)))}) or {}

    def _private(self, item: dict) -> dict:
        """A queue item in the shape services.farm.fields_private reads."""
        st = hub_status(item)
        md = {"quantity_required": 1, "file_uuid": str(item["library_file_id"]) if item.get("library_file_id") else None,
              "print_time_seconds": item.get("print_time_seconds"), "filament_used_g": item.get("filament_used_grams"),
              "soft_assign_printer": item.get("printer_name"), "source": "openprinthq"}
        if st == "waiting_filament":
            md["filament_rejection"] = item.get("waiting_reason")
        return {"id": item.get("id"), "name": _name(item), "status": st, "printer_id": item.get("printer_name"),
                "priority": item.get("priority"), "metadata": md, "source": "openprinthq", "linked_orders": [],
                "material_type": item.get("filament_type"), "progress": None, "error_message": item.get("error_message"),
                "created_at": item.get("created_at"), "started_at": item.get("started_at"),
                "completed_at": item.get("completed_at")}

    def queue(self, statuses=None, limit: int = 200, offset: int = 0, sort: str | None = None):
        items = [self._private(i) for i in (self.e("GET", "/queue/") or [])]
        if statuses:
            want = set(statuses)
            items = [i for i in items if i["status"] in want]
        items.sort(key=lambda i: i["id"] or 0, reverse=sort == "job_id_desc")
        return {"jobs": items[offset:offset + limit], "count": len(items[offset:offset + limit]), "total": len(items)}

    def job(self, job_id):
        i = self.e("GET", f"/queue/{int(job_id)}") or {}
        return {"id": i.get("id"), "name": _name(i), "status": hub_status(i), "assigned_printer": i.get("printer_name"),
                "priority": i.get("priority"), "progress": None, "error_message": i.get("error_message"),
                "created_at": i.get("created_at"), "started_at": i.get("started_at"), "completed_at": i.get("completed_at")}

    def set_priority(self, job_id, priority: int):
        """The engine's 0-100 priority when it has one; otherwise urgent and high (75 and up) move to the front of
        the queue and lower priorities keep their place."""
        if self.engine_parity():
            return self.e("PATCH", f"/queue/{int(job_id)}", json={"priority": _priority(priority)}, retries=1)
        if int(priority) >= 75:
            return self.e("POST", "/queue/reorder", json={"items": [{"id": int(job_id), "position": 0}]}, retries=1)
        return {"ok": True, "unchanged": True}

    def cancel_queued(self, job_id, reason: str):
        return self.e("POST", f"/queue/{int(job_id)}/cancel", retries=1)

    def cancel_job(self, job_id):
        return self.e("POST", f"/queue/{int(job_id)}/stop", retries=1)

    def last_finished(self, printer: str | None, within_s: int = 1800):
        """The queue item that most recently ended (completed, failed, cancelled) on this printer, in the shape of
        job(), or None. Only items that ended in the last within_s seconds count, so a direct print (not from the
        queue) is not mistaken for an older queued one."""
        from datetime import datetime, timezone
        if not printer:
            return None
        now = datetime.now(timezone.utc)
        best, best_t = None, None
        for i in self.e("GET", "/queue/") or []:
            if i.get("printer_name") != printer or hub_status(i) not in ("completed", "failed", "cancelled"):
                continue
            raw = i.get("completed_at")
            try:
                t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if (now - t).total_seconds() > within_s:
                continue
            if best_t is None or t > best_t:
                best, best_t = i, t
        if not best:
            return None
        return {"id": best.get("id"), "name": _name(best), "status": hub_status(best),
                "assigned_printer": best.get("printer_name"), "error_message": best.get("error_message"),
                "created_at": best.get("created_at"), "started_at": best.get("started_at"),
                "completed_at": best.get("completed_at")}

    # ---- maintenance (the engine's per-printer maintenance items) ----
    def maint_schedules(self):
        """One schedule per enabled-or-not maintenance item, in the shape services/maintenance.py reads."""
        out = []
        for pr in self.e("GET", "/maintenance/overview") or []:
            for m in pr.get("maintenance_items") or []:
                days = m.get("interval_type") == "days"
                status = "due" if m.get("is_due") else ("warning" if m.get("is_warning") else "ok")
                out.append({"id": m.get("id"), "printer_name": m.get("printer_name") or pr.get("printer_name"),
                            "task_name": m.get("maintenance_type_name"), "enabled": bool(m.get("enabled", True)),
                            "interval_hours": None if days else m.get("interval_hours"),
                            "interval_days": m.get("interval_hours") if days else None,
                            "hours_since_last": m.get("hours_since_maintenance"),
                            "days_since_last": m.get("days_since_maintenance"),
                            "status": status, "last_completed_at": m.get("last_performed_at"),
                            "last_completed_by": None, "category": None, "description": None,
                            "wiki_url": m.get("maintenance_type_wiki_url")})
        return out

    def maint_complete(self, schedule_id: int, performed_by: str, notes: str = "", minutes: int | None = None):
        text = "; ".join(x for x in [notes, f"by {performed_by}" if performed_by else None,
                                     f"{minutes} min" if minutes else None] if x)
        body = {"notes": text or None}
        if performed_by and self.engine_parity():
            body = {"notes": "; ".join(x for x in [notes, f"{minutes} min" if minutes else None] if x) or None,
                    "performed_by": performed_by[:100]}
        return self.e("POST", f"/maintenance/items/{int(schedule_id)}/perform", json=body, retries=1)

    def maint_log(self, printer: str, task: str, category: str, performed_by: str, notes: str = "",
                  minutes: int | None = None):
        """Maintenance with no schedule, in the engine's per-printer maintenance log."""
        if not self.engine_parity():
            self._no("maintenance_log")
        text = "; ".join(x for x in [notes, f"{minutes} min" if minutes else None] if x)
        cat = (category or "other").strip().lower()
        body = {"title": (task or "Maintenance")[:200], "category": cat if cat in LOG_CATEGORIES else "other",
                "notes": text or None, "performed_by": (performed_by or "")[:100] or None}
        return self.e("POST", f"/maintenance/printers/{self.printer_id(printer)}/log", json=body, retries=1)

    def maint_down(self) -> list:
        """Printers out of service, as {printer_name, reason, since}."""
        if not self.engine_parity():
            return []
        return [{"printer_name": d.get("name"), "reason": d.get("reason"), "since": d.get("since")}
                for d in self.e("GET", "/printers/out-of-service") or []]

    def set_down(self, printer: str, down: bool, reason: str = ""):
        """Out of service: the engine sends no new jobs to it; a print in progress keeps running."""
        if not self.engine_parity():
            self._no("out_of_service")
        pid = self.printer_id(printer)
        if down:
            return self.e("PUT", f"/printers/{pid}/out-of-service", json={"reason": (reason or "")[:200] or None},
                          retries=1)
        return self.e("DELETE", f"/printers/{pid}/out-of-service", retries=1)

    # ---- alarm feed (engine GET /alarms) ----
    def alarms(self, state: str = "active", printer: str | None = None, severity: list | None = None,
               unacknowledged: bool = False, limit: int = 200) -> list:
        if not self.engine_alarms():
            self._no("alarms")
        params = [("state", state), ("limit", str(int(limit)))]
        if printer:
            params.append(("printer_id", str(self.printer_id(printer))))
        for s in severity or []:
            params.append(("severity", s))
        if unacknowledged:
            params.append(("unacknowledged", "true"))
        return self.e("GET", "/alarms", params=params) or []

    def alarm_summary(self) -> dict:
        if not self.engine_alarms():
            self._no("alarms")
        out = dict(self.e("GET", "/alarms/summary") or {})
        names = {p.get("id"): p.get("name") for p in self._printers_raw()}
        out["printer_names"] = [names.get(i) or f"printer {i}" for i in out.get("printers") or []]
        return out

    def ack_alarm(self, alarm_id: int, by: str | None = None) -> dict:
        if not self.engine_alarms():
            self._no("alarms")
        return self.e("POST", f"/alarms/{int(alarm_id)}/ack", json={"by": (by or None)}, retries=1) or {}

    def ack_printer_alarms(self, printer: str, by: str | None = None) -> dict:
        if not self.engine_alarms():
            self._no("alarms")
        pid = self.printer_id(printer)
        return self.e("POST", f"/printers/{pid}/alarms/ack", json={"by": (by or None)}, retries=1) or {}

    def alarm(self, alarm_id: int) -> dict:
        if not self.engine_alarms():
            self._no("alarms")
        return self.e("GET", f"/alarms/{int(alarm_id)}") or {}

    def dismiss_alarm(self, alarm_id: int, by: str | None = None) -> dict:
        """Take an alarm out of the active list; it comes back as a new one if it clears and recurs."""
        if not self.engine_alarms():
            self._no("alarms")
        return self.e("POST", f"/alarms/{int(alarm_id)}/dismiss", json={"by": (by or None)}, retries=1) or {}

    def suppress_alarm(self, alarm_id: int, by: str | None = None) -> dict:
        """No more alarms or notifications for this alarm's code on its printer (engine alarm-suppressions)."""
        if not self.engine_alarms():
            self._no("alarms")
        return self.e("POST", f"/alarms/{int(alarm_id)}/suppress", json={"by": (by or None)}, retries=1) or {}

    def alarm_suppressions(self, printer: str | None = None) -> list:
        if not self.engine_alarms():
            self._no("alarms")
        params = [("printer_id", str(self.printer_id(printer)))] if printer else []
        return self.e("GET", "/alarm-suppressions", params=params) or []

    def unsuppress_alarm(self, suppression_id: int) -> dict:
        if not self.engine_alarms():
            self._no("alarms")
        return self.e("DELETE", f"/alarm-suppressions/{int(suppression_id)}", retries=1) or {}

    # ---- filament matching (engine /filament-matching) ----
    def _fm(self):
        if not self.engine_filament_matching():
            self._no("filament_matching")

    def filament_settings(self) -> dict:
        self._fm()
        return self.e("GET", "/filament-matching/settings") or {}

    def set_filament_settings(self, level: str | None = None, material_groups: dict | None = None) -> dict:
        self._fm()
        body = {k: v for k, v in {"level": level, "material_groups": material_groups}.items() if v is not None}
        return self.e("PUT", "/filament-matching/settings", json=body, retries=1) or {}

    def filament_unmatched(self) -> list:
        """Waiting jobs that can't start because of filament, with why on each printer (engine ids and names)."""
        self._fm()
        return self.e("GET", "/filament-matching/unmatched") or []

    def filament_options(self, job_id, all_printers: bool = False) -> dict:
        """What a job needs and, per printer, what is loaded and the best mapping."""
        self._fm()
        params = {"all_printers": "true"} if all_printers else None
        return self.e("GET", f"/filament-matching/queue/{int(job_id)}", params=params) or {}

    def filament_map(self, job_id, printer_id: int, slots: list[dict], remember: bool = True,
                     file_ref=None, by: str | None = None) -> dict:
        """Pin a job to a printer with the chosen filament mapping (and a resliced file when given)."""
        self._fm()
        body = {"printer_id": int(printer_id), "remember": bool(remember), "by": (by or None),
                "slots": [{"slot_id": int(s["slot_id"]), "global_tray_id": int(s["global_tray_id"])} for s in slots]}
        if file_ref is not None:
            body["library_file_id"] = int(file_ref)
        return self.e("POST", f"/filament-matching/queue/{int(job_id)}/mapping", json=body, retries=1) or {}

    def filament_aliases(self) -> list:
        self._fm()
        return self.e("GET", "/filament-matching/aliases") or []

    def delete_filament_alias(self, alias_id: int) -> dict:
        self._fm()
        return self.e("DELETE", f"/filament-matching/aliases/{int(alias_id)}", retries=1) or {}

    def set_job_match_level(self, job_id, level: str | None) -> dict:
        return self.e("PATCH", f"/queue/{int(job_id)}", json={"match_level": level}, retries=1) or {}

    # ---- webhooks (a signed webhook notification provider) ----
    def webhooks(self):
        out = []
        for p in self.e("GET", "/notifications/") or []:
            if p.get("provider_type") != "webhook":
                continue
            cfg = p.get("config") or {}
            out.append({"id": p.get("id"), "url": cfg.get("webhook_url"), "events": cfg.get("hub_events") or [],
                        "active": bool(p.get("enabled", True))})
        return out

    def add_webhook(self, url, events, secret):
        body = {"name": "OpenPrintHQ", "provider_type": "webhook", "enabled": True,
                "config": {"webhook_url": url, "signing_secret": secret, "payload_format": "generic",
                           "source_name": "OpenPrintHQ", "hub_events": list(events or [])}}
        body.update({f: True for f in EVENT_FLAGS})
        return self.e("POST", "/notifications/", json=body, retries=1)

    def delete_webhook(self, hook_id):
        return self.e("DELETE", f"/notifications/{int(hook_id)}")

    # ---- the farm spool library (the engine tenant's Spoolman, 2026-10-23) ----
    # Every route below is the engine's Spoolman-mode inventory (/spoolman/inventory/*). Spools come back in the
    # engine's InventorySpool shape: id (the Spoolman spool id), material, subtype, brand, color_name, rgba,
    # label_weight, weight_used, storage_location, location_id, archived_at, note, ...
    SI = "/spoolman/inventory"

    def lib_spools(self, include_archived: bool = False) -> list:
        return self.e("GET", f"{self.SI}/spools", params={"include_archived": str(bool(include_archived)).lower()}) or []

    def lib_spool(self, spool_id: int) -> dict:
        return self.e("GET", f"{self.SI}/spools/{int(spool_id)}", retries=1) or {}

    def lib_create_spools(self, spool: dict, quantity: int = 1) -> list:
        """Create quantity identical spools. spool is the engine's create body: spoolman_filament_id, or material
        (+ subtype, brand, rgba, color_name, label_weight) to find or make the filament; weight_used, note,
        cost_per_kg, location_id."""
        quantity = max(1, min(50, int(quantity or 1)))
        if quantity == 1:
            return [self.e("POST", f"{self.SI}/spools", json=spool, retries=1)]
        r = self.e("POST", f"{self.SI}/spools/bulk", json={"spool": spool, "quantity": quantity}, retries=1)
        return r.get("created", []) if isinstance(r, dict) else (r or [])

    def lib_patch_spool(self, spool_id: int, data: dict) -> dict:
        return self.e("PATCH", f"{self.SI}/spools/{int(spool_id)}", json=data, retries=1) or {}

    def lib_use(self, spool_id: int, grams: float) -> dict:
        """Filament used from a spool. The engine has no use call in Spoolman mode, so this moves weight_used on."""
        cur = self.lib_spool(spool_id)
        used = float(cur.get("weight_used") or 0) + float(grams)
        used = max(0.0, min(used, float(cur.get("label_weight") or used)))
        return self.lib_patch_spool(spool_id, {"weight_used": round(used, 2)})

    def lib_archive(self, spool_id: int) -> dict:
        return self.e("POST", f"{self.SI}/spools/{int(spool_id)}/archive", retries=1) or {}

    def lib_filaments(self) -> list:
        return self.e("GET", f"{self.SI}/filaments") or []

    def lib_assignments(self) -> list:
        return self.e("GET", f"{self.SI}/slot-assignments/all") or []

    def lib_assign(self, spool_id: int, printer_id: int, ams_id: int, tray_id: int,
                   extruder_index: int | None = None) -> dict:
        """Put a spool in a slot. A 409 (extruder not on the slot's path) raises ApiError; its message is the
        engine's JSON {"detail", "allowed"}."""
        body = {"spoolman_spool_id": int(spool_id), "printer_id": int(printer_id), "ams_id": int(ams_id),
                "tray_id": int(tray_id)}
        if extruder_index is not None:
            body["extruder_index"] = int(extruder_index)
        return self.e("POST", f"{self.SI}/slot-assignments", json=body, retries=1) or {}

    def lib_unassign(self, spool_id: int) -> dict:
        return self.e("DELETE", f"{self.SI}/slot-assignments/{int(spool_id)}", retries=1) or {}

    # ---- locations (storage, printers, AMS units and slots; scan codes L + 6) ----
    def locations(self, kind: str | None = None, printer_id: int | None = None) -> list:
        params = {k: v for k, v in (("kind", kind), ("printer_id", printer_id)) if v is not None}
        return self.e("GET", "/inventory/locations", params=params) or []

    def location_by_code(self, code: str) -> dict:
        return self.e("GET", f"/inventory/locations/by-code/{quote(str(code), safe='')}", retries=1) or {}

    def create_location(self, data: dict) -> dict:
        return self.e("POST", "/inventory/locations", json=data, retries=1) or {}

    def patch_location(self, location_id: int, data: dict) -> dict:
        return self.e("PATCH", f"/inventory/locations/{int(location_id)}", json=data, retries=1) or {}

    def delete_location(self, location_id: int):
        return self.e("DELETE", f"/inventory/locations/{int(location_id)}", retries=1)

    def sync_printer_locations(self) -> list:
        return self.e("POST", "/inventory/locations/sync-printers", retries=1) or []

    # ---- printer hardware: nozzles, filament paths, what is loaded, the job's plate image ----
    def printer_id(self, name: str) -> int:
        return int(self._by_name(name)["id"])

    def nozzles(self, printer_id: int) -> dict:
        return self.e("GET", f"/printers/{int(printer_id)}/nozzles", retries=1) or {}

    def set_nozzle(self, printer_id: int, extruder_index: int, data: dict) -> dict:
        return self.e("PUT", f"/printers/{int(printer_id)}/nozzles/{int(extruder_index)}", json=data, retries=1) or {}

    def clear_nozzle(self, printer_id: int, extruder_index: int) -> dict:
        return self.e("DELETE", f"/printers/{int(printer_id)}/nozzles/{int(extruder_index)}", retries=1) or {}

    def filament_paths(self, printer_id: int) -> dict:
        return self.e("GET", f"/printers/{int(printer_id)}/filament-paths", retries=1) or {}

    def set_filament_paths(self, printer_id: int, data: dict) -> dict:
        return self.e("PUT", f"/printers/{int(printer_id)}/filament-paths", json=data, retries=1) or {}

    def loaded(self, printer_id: int) -> dict:
        return self.e("GET", f"/printers/{int(printer_id)}/loaded", retries=1) or {}

    _stream_token: tuple | None = None

    def _camera_token(self) -> str | None:
        """The engine's image routes (cover, camera) want a short-lived stream token in the query string."""
        import time
        tok = self._stream_token
        if tok and tok[1] > time.time():
            return tok[0]
        try:
            r = self.e("POST", "/printers/camera/stream-token", retries=1) or {}
        except ApiError:
            return None
        if r.get("token"):
            self._stream_token = (r["token"], time.time() + 50 * 60)
        return r.get("token")

    def cover(self, printer_id: int, view: str | None = None) -> bytes | None:
        """The sliced plate image of the job printing now (PNG), or None when idle or unknown."""
        params = {"view": view} if view else {}
        tok = self._camera_token()
        if tok:
            params["token"] = tok
        try:
            r = self.request("GET", f"{API}/printers/{int(printer_id)}/cover", params=params, retries=1, expect_json=False)
        except ApiError as e:
            if e.status in (404, 400):
                return None
            raise
        return r.content or None
