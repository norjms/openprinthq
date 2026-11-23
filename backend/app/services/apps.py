"""Desktop apps: the slicers and design apps the installer runs as desktops in a browser tab (docs/apps.md).

The installer tells the hub which it runs (PUT /api/apps/v1), as it does the crafts. Each has an id, a title, the
craft it belongs to and its path, /apps/<id>. The hub adds each to its craft's sidebar group, and People gives
access per app through the app's group in the hub's user directory, <id>-users. The hub needs no code for a new
app: what an app is comes from the installer.
"""

import re

from . import crafts

_ID = re.compile(r"^[a-z][a-z0-9]{1,30}$")
MAX_APPS = 30


def normalise(values) -> list[dict]:
    """The apps as the installer sent them, checked: each id once, a known craft, its path /apps/<id>. Anything
    else is refused (ValueError) rather than half-kept."""
    out, seen = [], set()
    for v in list(values or ())[: MAX_APPS + 1]:
        if not isinstance(v, dict):
            raise ValueError("each app is an object")
        app_id = str(v.get("id") or "")
        if not _ID.match(app_id):
            raise ValueError(f"not an app id: {app_id!r}")
        if app_id in seen:
            raise ValueError(f"{app_id} is named twice")
        seen.add(app_id)
        title = " ".join(str(v.get("title") or "").split())[:60]
        if not title:
            raise ValueError(f"{app_id} has no title")
        craft = str(v.get("craft") or "")
        if craft not in crafts.CRAFTS:
            raise ValueError(f"{app_id}: unknown craft {craft!r}")
        path = f"/apps/{app_id}"
        if str(v.get("path") or path).rstrip("/") != path:
            raise ValueError(f"{app_id}: its path is {path}")
        out.append({"id": app_id, "title": title, "craft": craft, "path": path,
                    "experimental": bool(v.get("experimental"))})
    if len(out) > MAX_APPS:
        raise ValueError(f"at most {MAX_APPS} apps")
    return out


def registered() -> list[dict]:
    """The apps this installation runs (cached with the installation's state). Anything stored that no longer
    checks out is left out rather than shown."""
    from .installation import state
    try:
        return normalise(state().get("apps"))
    except ValueError:
        return []


def group(app_id: str) -> str:
    """The app's group in the hub's user directory: who besides the hub's owners may open it."""
    return f"{app_id}-users"
