"""Service clients for the current team. Each team has its own connection settings (services.connections);
clients are cached per team and rebuilt when the team changes its settings. The slicer is shared by all teams
unless a team connects its own."""
import threading
from functools import lru_cache

from ..config import settings

_clients: dict = {}
_lock = threading.Lock()


def _cfg(kind: str) -> dict:
    from ..services.connections import get
    return get(kind)


def _cached(kind: str, cfg: dict, build):
    from ..tenant import current_team_id
    key = (current_team_id(), kind)
    ver = cfg.get("_version")
    with _lock:
        hit = _clients.get(key)
        if hit and hit[0] == ver:
            return hit[1]
    c = build()
    with _lock:
        _clients[key] = (ver, c)
    return c


def woo():
    from .woo_api import WooClient
    c = _cfg("woo")
    return _cached("woo", c, lambda: WooClient(c.get("url"), c.get("key"), c.get("secret"), verify=c.get("verify_tls", True)))


def dam():
    from .resourcespace import ResourceSpaceClient
    c = _cfg("dam")
    return _cached("dam", c, lambda: ResourceSpaceClient(c.get("url"), c.get("host"), c.get("user"), c.get("key")))


def manyfold():
    from .manyfold_api import ManyfoldClient
    c = _cfg("manyfold")
    return _cached("manyfold", c, lambda: ManyfoldClient(c.get("url"), c.get("client_id"), c.get("client_secret"), c.get("public_url")))


def openprinthq():
    from .openprinthq_api import OpenPrintHQClient
    c = _cfg("openprinthq")
    return _cached("openprinthq", c, lambda: OpenPrintHQClient(c.get("url"), c.get("api_key"), c.get("target_group"),
                                                              verify=c.get("verify_tls", True)))


FARM_KINDS = ("openprinthq",)   # the one farm app: the farm (clients/farm_base.py)


def farm_kind(team_id: int | None = None) -> str:
    """The installation's print farm: always the farm. Raises FarmNotChosen until first-run
    setup has connected it."""
    from ..services import installation
    if installation.state()["farm"]:
        return "openprinthq"
    raise installation.FarmNotChosen()


def farm():
    """The team's print farm, as the farm interface (clients/farm_base.py)."""
    return openprinthq()


def farm_connected(team_id: int | None = None) -> bool:
    from ..services.connections import is_connected
    return is_connected("openprinthq", team_id)


def inventree():
    from .inventree_api import InvenTreeClient
    c = _cfg("inventree")
    return _cached("inventree", c, lambda: InvenTreeClient(c.get("url"), c.get("host"), c.get("token")))


def spoolman():
    from .spoolman_api import SpoolmanClient
    c = _cfg("spoolman")
    return _cached("spoolman", c, lambda: SpoolmanClient(c.get("url")))


def public_url(kind: str, field: str = "public_url") -> str | None:
    """A team's public link for a service (for links in the UI), or None."""
    from ..services.connections import get
    c = get(kind, required=False) or {}
    return c.get(field) or c.get("url")


@lru_cache
def _shared_slicer():
    from .slicer_api import SlicerClient
    s = settings()
    return SlicerClient(s.slicer_url, s.slicer_token)


def slicer():
    """The team's own slicer service when it set one up (Connections), else the hub's shared one (env)."""
    from .slicer_api import SlicerClient
    from ..tenant import current_team_id
    c = _cfg_optional("slicer") if current_team_id() else None
    if c and c.get("url"):
        return _cached("slicer", c, lambda: SlicerClient(c.get("url"), c.get("token") or ""))
    return _shared_slicer()


def slicer_configured() -> bool:
    from ..tenant import current_team_id
    c = _cfg_optional("slicer") if current_team_id() else None
    return bool((c and c.get("url")) or settings().slicer_url)


def _cfg_optional(kind: str) -> dict | None:
    from ..services.connections import get
    return get(kind, required=False)
