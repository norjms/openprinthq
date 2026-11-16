"""The print farm: ArtisanHub Farm (OpenPrintHQ).

The hub talks to its farm only through this interface; clients/openprinthq_api.py is the driver. The driver lists
the capabilities it has; a hub feature that needs one checks `farm().has(...)` (or catches FarmUnsupported) and
hides or adapts itself instead of failing, since an older engine may not have every feature yet.
"""
from .base import ApiError

# What a farm can do. A driver lists the ones it has in CAPABILITIES.
CAPABILITIES = {
    "files": "Upload print files and list them",
    "jobs": "Submit print jobs and read them",
    "queue": "Read the farm queue",
    "priority": "Change a queued job's priority",
    "cancel": "Cancel a job, queued or printing",
    "printers": "List printers with status and capabilities",
    "commands": "Send printer commands (pause, resume, ready, ...)",
    "snapshot": "A camera frame per printer",
    "filament": "What each printer has loaded, slot by slot",
    "spools": "The farm's own spool list",
    "webhooks": "Push job and printer events to the hub",
    "maintenance": "Maintenance schedules: read them and mark them done",
    "out_of_service": "Take a printer out of service (no new jobs) and list the ones that are",
    "maintenance_log": "Log maintenance that has no schedule",
    "library_folders": "Folders for print files in the farm's library",
    "preflight": "Say why a queued job isn't printing, printer by printer",
    "queue_stats": "Queue throughput: success rate, wait and print times",
    "retries": "Requeue a failed print automatically (max_retries)",
    "spool_library": "The farm keeps the filament library: spools, filaments and slot assignments",
    "locations": "Storage and printer locations with scan codes",
    "nozzles": "Nozzles per extruder, filament paths and what each slot has loaded",
    "pin_printer": "Send a job to one named printer (Print now on a model's Print tab)",
    "alarms": "The farm's alarm feed: what every printer reports right now, with acknowledgement",
}

# Printer commands the hub knows. A printer dict may carry "commands", the ones its connection supports; a
# printer without the key supports all of them (older engines).
ALL_COMMANDS = ("set_ready", "clear_ready", "pause", "resume", "cancel", "stop", "home", "light_on", "light_off",
                "set_temperature", "preheat", "cooldown")


def printer_allows(printer: dict, command: str) -> bool:
    """Whether a printer (as the driver shapes it) accepts this command."""
    cmds = (printer or {}).get("commands")
    return cmds is None or command in cmds


class FarmUnsupported(ApiError):
    """The connected farm app cannot do this. Callers that already catch ApiError or Exception keep working."""

    def __init__(self, kind: str, capability: str):
        label = CAPABILITIES.get(capability, capability)
        super().__init__(kind, None, f"not supported by this farm: {label}")
        self.capability = capability


class FarmBackend:
    """What the hub needs from a print farm. Drivers override what they support."""

    kind = ""                     # "openprinthq"
    label = ""                    # shown in the UI
    CAPABILITIES: frozenset = frozenset()

    def has(self, capability: str) -> bool:
        return capability in self.CAPABILITIES

    def _no(self, capability: str):
        raise FarmUnsupported(self.kind, capability)

    def describe(self) -> dict:
        """For the UI and the phone app: which farm this is and what it can do."""
        return {"kind": self.kind, "label": self.label, "capabilities": sorted(self.CAPABILITIES)}

    # ---- connection ----
    def ping(self):
        self._no("printers")

    # ---- files and jobs ----
    def list_files(self):
        self._no("files")

    def upload_file(self, path, filename=None):
        self._no("files")

    def submit_job(self, file_ref=None, quantity=1, job_name=None, priority=None, max_retries=None,
                   printer=None, start=False):
        """Queue a library file. printer pins it to one farm printer; start (with printer) asks the farm to start
        it there now when that printer is free and its bed is clear."""
        self._no("jobs")

    def preflight(self, job_id) -> dict:
        self._no("preflight")

    def queue_stats(self, days: int = 30) -> dict:
        self._no("queue_stats")

    def job(self, job_id):
        self._no("jobs")

    def queue(self, statuses=None, limit: int = 200, offset: int = 0, sort: str | None = None):
        self._no("queue")

    def set_priority(self, job_id, priority: int):
        self._no("priority")

    def cancel_job(self, job_id):
        """Stop a job that is printing."""
        self._no("cancel")

    def cancel_queued(self, job_id, reason: str):
        """Cancel a job that has not started."""
        self._no("cancel")

    # ---- printers ----
    def printers(self):
        self._no("printers")

    def printer(self, name: str):
        self._no("printers")

    def printers_full(self):
        """Printers with capabilities (build volume, tools, AMS units, address)."""
        self._no("printers")

    def command(self, printer: str, command: str, args: dict | None = None, confirm: bool = False):
        self._no("commands")

    def snapshot(self, printer: str) -> bytes | None:
        self._no("snapshot")

    def camera_sources(self, printer: str) -> list[str]:
        """The printer's live camera streams, a chamber camera first. A farm without named cameras has none."""
        return []

    def frame(self, source: str, timeout: float = 15) -> bytes | None:
        """One JPEG from one of camera_sources()."""
        return None

    def live_playlist(self, source: str) -> bytes | None:
        """Live video of one of camera_sources() as an HLS playlist (fragmented MP4), or None without it."""
        return None

    def live_part(self, file: str, params: dict) -> tuple[bytes, str] | None:
        """One part of a live view started by live_playlist(): its playlist, init or media segment."""
        return None

    def filament_assignments(self):
        self._no("filament")

    # ---- spools ----
    def spools(self):
        self._no("spools")

    def create_spool(self, data):
        self._no("spools")

    def patch_spool(self, spool_id, data):
        self._no("spools")

    # ---- events ----
    def webhooks(self):
        self._no("webhooks")

    def add_webhook(self, url, events, secret):
        self._no("webhooks")

    def delete_webhook(self, hook_id):
        self._no("webhooks")

    # ---- library folders ----
    def library_folder(self, name: str, parent=None) -> dict:
        """Find or make a folder; answers the folder (with its id)."""
        self._no("library_folders")

    def move_library_file(self, file_id, folder_id):
        self._no("library_folders")

    def delete_library_file(self, file_id):
        self._no("files")

    def delete_library_folder(self, folder_id):
        self._no("library_folders")

    # ---- maintenance ----
    def maint_schedules(self):
        self._no("maintenance")

    def maint_complete(self, schedule_id: int, performed_by: str, notes: str = "", minutes: int | None = None):
        self._no("maintenance")

    def maint_log(self, printer: str, task: str, category: str, performed_by: str, notes: str = "",
                  minutes: int | None = None):
        self._no("maintenance")

    def maint_down(self) -> list:
        self._no("maintenance")

    def set_down(self, printer: str, down: bool, reason: str = ""):
        self._no("maintenance")

    # ---- alarm feed ----
    def alarms(self, state: str = "active", printer: str | None = None, severity: list | None = None,
               unacknowledged: bool = False, limit: int = 200) -> list:
        self._no("alarms")

    def alarm_summary(self) -> dict:
        self._no("alarms")

    def ack_alarm(self, alarm_id: int, by: str | None = None) -> dict:
        self._no("alarms")

    def ack_printer_alarms(self, printer: str, by: str | None = None) -> dict:
        self._no("alarms")
