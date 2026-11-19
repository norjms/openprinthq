"""Fetching URLs that come from people or from other sites' pages, without letting them reach inside the network.

Used for "add a model by URL" (any site), photos and files named by a model site's answer, and anything the browser
add-on hands over. The rules, in order:

- http or https only, no user:password@ part, port 80 or 443 (or the scheme's default);
- the host name is resolved here and EVERY address it resolves to must be a public one (not private, loopback,
  link-local, carrier-grade NAT, multicast, reserved or unspecified); names ending in .local, .lan, .internal,
  .home.arpa or localhost are refused before any lookup;
- the request then goes to the checked address itself (with the Host header and TLS server name set to the name),
  so a second DNS answer cannot swap in an inside address (DNS rebinding);
- redirects are followed by hand, at most 5, and each hop is checked the same way;
- bodies are read as a stream and cut off at a size limit; the content type must fit what was asked for.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from email.message import Message
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

import httpx

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 "
      "OpenPrintHQ/1.0 (+model import)")
MAX_HTML = 5 * 1024 * 1024
MAX_IMAGE = 25 * 1024 * 1024
MAX_FILE = 500 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
BLOCKED_SUFFIX = (".local", ".lan", ".internal", ".home.arpa", ".localdomain", ".localhost", ".intranet", ".corp")
MODEL_EXT = (".3mf", ".stl", ".step", ".stp", ".obj", ".zip", ".f3d", ".scad", ".gcode", ".bgcode")
FILE_TYPES = ("application/sla", "application/vnd.ms-pki.stl", "application/x-navistyle", "model/stl", "model/x.stl-binary",
              "model/x.stl-ascii", "model/3mf", "application/vnd.ms-package.3dmanufacturing-3dmodel+xml", "model/step",
              "application/step", "application/stp", "model/obj", "application/zip", "application/x-zip-compressed",
              "application/octet-stream", "binary/octet-stream", "application/x-binary", "text/plain", "application/x-tgif")


class FetchError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Fetched:
    url: str                    # final URL after redirects
    status: int
    content_type: str
    content: bytes | None
    filename: str | None
    size: int | None


def ip_is_public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    if a.version == 6 and a.sixtofour:
        a = a.sixtofour
    return bool(a.is_global) and not a.is_multicast and not a.is_reserved and not a.is_unspecified


def resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"{host} could not be found ({e.strerror or e})", 400) from None
    return list(dict.fromkeys(i[4][0] for i in infos))


def check_url(url: str, resolver=resolve) -> tuple[str, str, int, list[str]]:
    """(scheme, host, port, public addresses) or FetchError."""
    if not isinstance(url, str) or len(url) > 4000:
        raise FetchError("not a usable link")
    u = urlsplit(url.strip())
    scheme = (u.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise FetchError("only http and https links can be imported")
    if u.username or u.password or "@" in (u.netloc or ""):
        raise FetchError("links with a user name or password in them are refused")
    host = (u.hostname or "").rstrip(".").lower()
    if not host:
        raise FetchError("the link has no host")
    try:
        port = u.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise FetchError("the link's port is not valid") from None
    if port not in (80, 443):
        raise FetchError("only the standard web ports (80 and 443) are allowed")
    if host == "localhost" or host.endswith(BLOCKED_SUFFIX) or "." not in host and not _is_ip(host):
        raise FetchError("that address is inside a private network; only public sites can be imported from")
    if _is_ip(host):
        ips = [host]
    else:
        ips = resolver(host)
    if not ips:
        raise FetchError(f"{host} has no address")
    bad = [ip for ip in ips if not ip_is_public(ip)]
    if bad:
        raise FetchError("that address is inside a private network; only public sites can be imported from")
    return scheme, host, port, ips


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h.strip("[]"))
        return True
    except ValueError:
        return False


def _filename(resp: httpx.Response, url: str) -> str | None:
    cd = resp.headers.get("content-disposition")
    if cd:
        m = Message()
        m["content-disposition"] = cd
        name = m.get_param("filename*", header="content-disposition") or m.get_param("filename", header="content-disposition")
        if isinstance(name, tuple):
            name = name[2]
        if name:
            name = unquote(str(name)).strip().strip('"')
            if name.lower().startswith("utf-8''"):
                name = name[7:]
            return PurePosixPath(name.replace("\\", "/")).name or None
    return PurePosixPath(unquote(urlsplit(url).path)).name or None


def looks_like_model(name: str | None) -> bool:
    return bool(name) and name.lower().endswith(MODEL_EXT)


def fetch(url: str, kind: str = "file", max_bytes: int | None = None, headers: dict | None = None,
          resolver=resolve, transport: httpx.BaseTransport | None = None) -> Fetched:
    """kind: html (a web page), image, file (a model file) or page (html, or a model file's headers only:
    content None and the caller fetches it again as a file)."""
    limit = max_bytes or {"html": MAX_HTML, "page": MAX_HTML, "image": MAX_IMAGE}.get(kind, MAX_FILE)
    h = {"User-Agent": UA, "Accept": {"html": "text/html,application/xhtml+xml", "page": "text/html,*/*;q=0.8",
                                      "image": "image/avif,image/webp,image/*;q=0.9"}.get(kind, "*/*"),
         **(headers or {})}
    current = url
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False, verify=True, transport=transport) as c:
        for _hop in range(MAX_REDIRECTS + 1):
            scheme, host, port, ips = check_url(current, resolver)
            u = urlsplit(current)
            ip = ips[0]
            netloc = f"[{ip}]" if ":" in ip else ip
            if u.port:
                netloc += f":{u.port}"
            target = u._replace(netloc=netloc).geturl()
            hdrs = {**h, "Host": host + (f":{u.port}" if u.port else "")}
            req = c.build_request("GET", target, headers=hdrs,
                                  extensions={"sni_hostname": host} if scheme == "https" else {})
            try:
                resp = c.send(req, stream=True)
            except httpx.TimeoutException:
                raise FetchError(f"{host} did not answer in time", 504) from None
            except httpx.HTTPError as e:
                raise FetchError(f"could not reach {host}: {str(e)[:120]}", 502) from None
            try:
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    current = urljoin(current, resp.headers["location"])
                    continue
                if resp.status_code >= 400:
                    raise FetchError(f"{host} answered {resp.status_code}", 502 if resp.status_code >= 500 else 400)
                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                name = _filename(resp, current)
                size = int(resp.headers["content-length"]) if (resp.headers.get("content-length") or "").isdigit() else None
                is_html = ctype in ("text/html", "application/xhtml+xml")
                if kind == "page" and not is_html:
                    if ctype in FILE_TYPES or looks_like_model(name):
                        return Fetched(current, resp.status_code, ctype, None, name, size)
                    raise FetchError(f"that link is a {ctype or 'file of unknown type'}, not a web page or a model file")
                if kind == "html" and not is_html:
                    raise FetchError(f"expected a web page, got {ctype or 'unknown content'}")
                if kind == "image" and not ctype.startswith("image/"):
                    raise FetchError(f"expected an image, got {ctype or 'unknown content'}")
                if kind == "file":
                    if is_html or not (ctype in FILE_TYPES or looks_like_model(name) or ctype.startswith("model/")):
                        raise FetchError(f"expected a model file (STL, 3MF, STEP, OBJ, ZIP), got {ctype or 'unknown content'}"
                                         + (" - the site probably wants you signed in: use the browser add-on" if is_html else ""))
                if size is not None and size > limit:
                    raise FetchError(f"the file is {size // 1048576} MB; the limit is {limit // 1048576} MB", 413)
                buf = bytearray()
                for chunk in resp.iter_bytes(65536):
                    buf += chunk
                    if len(buf) > limit:
                        raise FetchError(f"the download passed the {limit // 1048576} MB limit and was stopped", 413)
                return Fetched(current, resp.status_code, ctype, bytes(buf), name, len(buf))
            finally:
                resp.close()
    raise FetchError("too many redirects")


def image_bytes(url: str) -> bytes:
    """For import_normalized(fetch_bytes=...): an image from a public host, at most 25 MB."""
    from ..clients.base import ApiError
    try:
        return fetch(url, "image").content or b""
    except FetchError as e:
        raise ApiError("photos", e.status, e.message) from None


def file_bytes(url: str) -> tuple[str, bytes]:
    from ..clients.base import ApiError
    try:
        f = fetch(url, "file")
    except FetchError as e:
        raise ApiError("download", e.status, e.message) from None
    return (f.filename or "model.stl"), (f.content or b"")


_TAG = re.compile(r"<[^>]+>")


def text(s: str | None, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", s or "")).strip()[:limit]
