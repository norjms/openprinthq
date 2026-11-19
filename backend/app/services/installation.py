"""The installation: its print farm and first-run setup.

The print farm is the farm, the only farm the hub supports. First-run setup records it once
The farm is connected. Invariants 3 and 4: the hub is complete without the installer, and the setup API lives in the
hub. First-run setup goes through /api/setup/v1, used both by the hub's own /setup page and by the installer from
outside.

An installation set up with a farm the hub no longer supports refuses to start (UnsupportedFarm), so it keeps
running its previous version until it has moved to the farm. Nothing about it is changed or guessed.
"""

import hashlib
import hmac
import logging
import secrets
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..tenant import unscoped
from .connections import NotConnected

log = logging.getLogger("hub.installation")
FARM_KINDS = ("openprinthq",)
TTL = 20.0   # web and worker are separate processes; a short cache, as for connections
_cache: dict = {}
_lock = threading.Lock()


class UnsupportedFarm(RuntimeError):
    """The installation was set up with a farm this version no longer supports."""

    def __init__(self, farm: str):
        super().__init__(
            f"This installation's print farm is {farm!r}, which this version of OpenPrintHQ no longer supports. "
            "Nothing was changed. Keep running the previous version until the installation has moved to "
            "The farm (docs/farm.md, Moving from another farm), then upgrade.")
        self.farm = farm


class FarmNotChosen(NotConnected):
    def __init__(self):
        RuntimeError.__init__(self, "No print farm has been chosen for this installation yet. Finish first-run setup at /setup.")
        self.kind = "farm"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def state() -> dict:
    """{"farm", "farm_source", "setup_complete"} for the installation, cached for a few seconds."""
    t = time.time()
    with _lock:
        hit = _cache.get("state")
    if hit and t - hit[0] < TTL:
        return hit[1]
    from ..db import SessionLocal
    from ..models import Installation
    s = SessionLocal()
    try:
        row = s.get(Installation, 1)
        st = {"farm": row.farm if row else None, "farm_source": row.farm_source if row else None,
              "setup_complete": bool(row and row.setup_completed_at)}
    finally:
        s.close()
    # Cache only settled states. "Not chosen yet" ends at setup, which runs in one process; the others must
    # see the change at once rather than up to TTL later, so that state is always read fresh.
    if st["farm"] or st["farm_source"] == "legacy":
        with _lock:
            _cache["state"] = (t, st)
    return st


def forget() -> None:
    with _lock:
        _cache.clear()


def chosen_farm() -> str | None:
    return state()["farm"]


def decide(connected: bool, fresh: bool) -> tuple[str | None, str | None]:
    """(farm, farm_source) for an installation seen for the first time: OpenPrintHQ when it is already connected
    (an installation older than the explicit choice), otherwise nothing until first-run setup."""
    if connected:
        return "openprinthq", "migrated"
    return None, (None if fresh else "legacy")


def bootstrap(db: Session, fresh: bool) -> None:
    """At startup: make sure the installation row exists, migrate an older installation once, and on a new
    installation that has no setup token from the environment, generate one and log it once. An installation whose
    farm is no longer supported stops here (UnsupportedFarm)."""
    from ..models import Connection, Installation, User
    row = db.get(Installation, 1)
    if row is not None and row.farm and row.farm not in FARM_KINDS:
        raise UnsupportedFarm(row.farm)
    if row is None:
        row = Installation(id=1)
        db.add(row)
        db.flush()
        with unscoped():   # every team's connections, across teams
            connected = any((c.config or {}).get("url") and c.enabled for c in
                            db.execute(select(Connection).where(Connection.kind == "openprinthq")).scalars())
        row.farm, row.farm_source = decide(connected, fresh)
        if row.farm_source == "migrated":
            log.info("Installation farm set to openprinthq from its existing connection")
        if not fresh and db.execute(select(func.count()).select_from(User)).scalar():
            row.setup_completed_at = _now()   # an installation already in use never runs first-run setup
    if row.setup_completed_at is None and not settings().setup_token and not row.setup_token_hash:
        token = secrets.token_urlsafe(24)
        row.setup_token_hash = _hash(token)
        log.warning("First-run setup: open %s/setup and enter this setup token: %s",
                    settings().public_url.rstrip("/"), token)
    db.flush()
    forget()


def record_farm(db: Session) -> None:
    """An installation that has no farm yet (one older than the explicit choice) gets the farm once it is
    connected."""
    from ..models import Installation
    row = db.get(Installation, 1)
    if row is not None and not row.farm:
        row.farm, row.farm_source = "openprinthq", "connected"
        db.flush()
        forget()


def token_ok(row, token: str) -> bool:
    expected = settings().setup_token
    if expected:
        return hmac.compare_digest(token, expected)
    return bool(row.setup_token_hash) and hmac.compare_digest(_hash(token), row.setup_token_hash)


def connect_allowed(kind: str, existing: bool) -> None:
    """Connecting the farm needs first-run setup to have run (it records the farm)."""
    if kind not in FARM_KINDS or existing:
        return
    st = state()
    if not st["farm"] and st["farm_source"] != "legacy":
        raise ValueError("Finish first-run setup at /setup first: it connects the print farm.")
