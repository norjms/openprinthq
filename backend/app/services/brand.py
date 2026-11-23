"""The shop's own logo (docs/brand.md): a wordmark the owner uploads in Settings, kept in the hub's data folder and
served at /api/brand/v1/wordmark, so it survives updates and needs nothing from whoever installed the hub.

The phone apps draw it (services.look); without one they draw HUB_WORDMARK, the OpenPrintHQ wordmark by default.
SVG or PNG, at most 256 KB. An SVG is refused when it could run anything or load anything: scripts, event
handlers, foreignObject, or links and styles that reach outside the file. It is also served with a policy that
lets it run and load nothing, in case a browser opens it on its own.
"""

import hashlib
import re
from pathlib import Path

from ..config import settings

MAX_BYTES = 256 * 1024
TYPES = {"image/svg+xml": "svg", "image/png": "png"}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SVG_BAD = [
    (re.compile(rb"<\s*script", re.I), "scripts"),
    (re.compile(rb"\son[a-z]+\s*=", re.I), "event handlers"),
    (re.compile(rb"<\s*foreignObject", re.I), "foreignObject"),
    (re.compile(rb"(?:xlink:)?href\s*=\s*[\"']\s*(?!#)", re.I), "links to anything outside the file"),
    (re.compile(rb"url\(\s*[\"']?\s*(?!#)", re.I), "styles that load from outside the file"),
    (re.compile(rb"@import", re.I), "styles that load from outside the file"),
    (re.compile(rb"<!ENTITY", re.I), "entities"),
]


class BadLogo(ValueError):
    pass


def folder() -> Path:
    return Path(settings().data_dir) / "brand"


def current() -> tuple[Path, str] | None:
    """(file, media type) of the uploaded wordmark, or None."""
    for media, ext in TYPES.items():
        p = folder() / f"wordmark.{ext}"
        if p.is_file():
            return p, media
    return None


def version() -> str | None:
    found = current()
    return hashlib.sha256(found[0].read_bytes()).hexdigest()[:12] if found else None


def check(data: bytes, media: str) -> str:
    """The file's extension when it is an acceptable wordmark; BadLogo otherwise."""
    if media not in TYPES:
        raise BadLogo("the logo must be an SVG or a PNG file")
    if not data:
        raise BadLogo("the file is empty")
    if len(data) > MAX_BYTES:
        raise BadLogo(f"the logo must be at most {MAX_BYTES // 1024} KB")
    if media == "image/png":
        if not data.startswith(PNG_MAGIC):
            raise BadLogo("that file is not a PNG")
        return "png"
    head = data.lstrip()[:512].lower()
    if not (head.startswith(b"<svg") or head.startswith(b"<?xml")) or b"<svg" not in data[:4096].lower():
        raise BadLogo("that file is not an SVG")
    for pattern, what in _SVG_BAD:
        if pattern.search(data):
            raise BadLogo(f"an SVG logo cannot contain {what}")
    return "svg"


def save(data: bytes, media: str) -> str:
    ext = check(data, media)
    folder().mkdir(parents=True, exist_ok=True)
    for other in TYPES.values():
        (folder() / f"wordmark.{other}").unlink(missing_ok=True)
    tmp = folder() / f".wordmark.{ext}.tmp"
    tmp.write_bytes(data)
    tmp.replace(folder() / f"wordmark.{ext}")
    return version()


def remove() -> None:
    for ext in TYPES.values():
        (folder() / f"wordmark.{ext}").unlink(missing_ok=True)


def url() -> str | None:
    """Where the phone apps load the uploaded wordmark, with its version so a new one is fetched; None without one."""
    v = version()
    return f"/api/brand/v1/wordmark?v={v}" if v else None
