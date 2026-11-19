"""The hub's looks as data, for apps that draw their own screens (the phone apps).

The single source is the shared stylesheet the hub serves at /theme/apps.css (the same tokens as the hub's own UI and
the supporting apps). This module reads its token blocks and @font-face rules and resolves them per skin and mode,
so a change to apps.css reaches the phones without a second copy to keep in step.
"""
import hashlib
import json
import re
from pathlib import Path

SKINS = ("openprinthq", "classic")
MODES = ("light", "dark")
PREFS_MODES = ("system", "light", "dark")

_HERE = Path(__file__).resolve()
CSS_PATHS = (
    _HERE.parents[2] / "static" / "theme" / "apps.css",                               # in the container: /app/static
    _HERE.parents[3] / "frontend" / "static" / "theme" / "apps.css",                  # a source checkout
)

_BLOCK = re.compile(r'html((?:\[data-ah-(?:skin|mode)(?:="[a-z]+")?\])+)\s*\{([^{}]*)\}')
_ATTR = re.compile(r'\[data-ah-(skin|mode)(?:="([a-z]+)")?\]')
_DECL = re.compile(r'--ah-([a-z0-9-]+)\s*:\s*([^;]+);')
_FACE = re.compile(r'@font-face\s*\{([^{}]*)\}')
_COLOR = re.compile(r'^(#[0-9a-fA-F]{3,8}|rgba?\([^()]*\)|transparent)$')
_cache: dict = {}


def load_css() -> str | None:
    for p in CSS_PATHS:
        try:
            st = p.stat()
        except OSError:
            continue
        key = (str(p), st.st_mtime_ns)
        if _cache.get("key") != key:
            _cache.update(key=key, css=p.read_text(encoding="utf-8"), dir=p.parent)
        return _cache["css"]
    return None


def _matches(attrs: str, skin: str, mode: str) -> bool:
    for kind, val in _ATTR.findall(attrs):
        if val and val != (skin if kind == "skin" else mode):
            return False
    return True


def _resolve(raw: dict) -> dict:
    out = {}
    for k, v in raw.items():
        v = " ".join(v.split())
        for _ in range(4):                                        # var(--ah-x) chains
            m = re.fullmatch(r'var\(--ah-([a-z0-9-]+)\)', v)
            if not m or m.group(1) not in raw:
                break
            v = " ".join(raw[m.group(1)].split())
        out[k] = v
    return out


def tokens(css: str, skin: str, mode: str) -> dict:
    """Every --ah-* token for one skin and mode, in cascade order, references resolved."""
    raw: dict = {}
    for attrs, body in _BLOCK.findall(css):
        if _matches(attrs, skin, mode):
            raw.update(_DECL.findall(body))
    return _resolve(raw)


def _first_family(stack: str) -> str:
    m = re.match(r'\s*"([^"]+)"|\s*([^,]+)', stack or "")
    return (m.group(1) or m.group(2) or "").strip() if m else ""


def font_files(css: str, theme_dir: Path | None = None) -> dict:
    """{family: [{"weight": 400, "style": "normal", "url": "/theme/fonts/...woff2", "ttf": "/theme/fonts/...ttf"}]}
    from the @font-face rules. "ttf" is there when a TrueType copy sits next to the web font (phones need TrueType)."""
    out: dict = {}
    for body in _FACE.findall(css):
        fam = re.search(r'font-family\s*:\s*"([^"]+)"', body)
        url = re.search(r'url\(([^)]+)\)', body)
        if not fam or not url:
            continue
        w = re.search(r'font-weight\s*:\s*(\d+)', body)
        st = re.search(r'font-style\s*:\s*(\w+)', body)
        u = url.group(1).strip("'\" ")
        if not u.startswith(("/", "http")):
            u = "/theme/" + u
        item = {"weight": int(w.group(1)) if w else 400, "style": st.group(1) if st else "normal", "url": u}
        if theme_dir is not None and u.startswith("/theme/") and u.endswith(".woff2"):
            ttf = u[:-6] + ".ttf"
            if (theme_dir / ttf[len("/theme/"):]).is_file():
                item["ttf"] = ttf
        out.setdefault(fam.group(1), []).append(item)
    for v in out.values():
        v.sort(key=lambda f: f["weight"])
    return out


def palette(tok: dict) -> dict:
    """The colour tokens (plain colours only; gradients and colour-mix() stay web-only), snake_case keys."""
    return {k.replace("-", "_"): v for k, v in tok.items() if _COLOR.match(v)}


def skin_look(css: str, skin: str) -> dict:
    files = font_files(css, _cache.get("dir"))
    modes = {m: tokens(css, skin, m) for m in MODES}
    body, mono = _first_family(modes["dark"].get("font", "")), _first_family(modes["dark"].get("mono", ""))
    radius = modes["dark"].get("radius", "0px")
    return {
        "skin": skin,
        "colors": {m: palette(t) for m, t in modes.items()},
        "fonts": {"body": {"family": body, "files": files.get(body, [])},
                  "mono": {"family": mono, "files": files.get(mono, [])}},
        "web_radius_px": int(re.sub(r"\D", "", radius) or 0),
    }


def _wordmark() -> str:
    from ..config import settings
    return settings().wordmark or "/theme/openprinthq-wordmark.svg"


def build(skin: str, mode: str, all_skins: bool = False) -> dict | None:
    """The look for the phone: the chosen skin with both modes, the person's light/dark choice, and optionally every
    skin (for a picker). None when the stylesheet is missing."""
    css = load_css()
    if css is None:
        return None
    skin = skin if skin in SKINS else "openprinthq"
    mode = mode if mode in PREFS_MODES else "system"
    out = {"version": 1, "skin": skin, "mode": mode, **{k: v for k, v in skin_look(css, skin).items() if k != "skin"},
           "wordmark": _wordmark(), "skins": list(SKINS),
           "notes": "URLs are paths on this hub; phones load the 'ttf' file of each font. mode 'system' means follow the phone. web_radius_px is the web "
                    "skin's corner radius; the Metro layout keeps square corners."}
    if all_skins:
        out["all"] = {s: skin_look(css, s) for s in SKINS}
    return out


def etag(doc: dict) -> str:
    return '"' + hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:24] + '"'
