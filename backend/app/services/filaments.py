"""Requested filaments and their Bambu Lab replacements.

Each model documents the filament its designer asked for (brand, material, colour name and colour code), read from
the recommendations recorded with the model and from the filament list saved in the designer's project file. Colour
codes come first from FilamentColors.xyz (colours measured with a colorimeter from printed swatches, CC BY 4.0), then
SpoolmanDB (the brands' published codes, via the open database Spoolman uses), else the project file, else the
colour name. When the requested colour was measured, it is compared with the measured Bambu colour where
FilamentColors has one, so like is compared with like.

A team that prints with one brand (for example Bambu Lab) then gets the closest filament from that brand's
official catalog (Bambu Studio's filaments_color_codes.json): the same finish when the brand has one (Silk, Matte,
Metal, Galaxy, Marble, Wood...), and the nearest colour by CIEDE2000 colour difference. The choice goes into the
product's materials list, which drives slicing (preset, colour, filament id), stock and cost. The original request
is kept on the model and is never overwritten by matching.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from ..config import settings

log = logging.getLogger("hub.filaments")

BAMBU_URL = "https://raw.githubusercontent.com/bambulab/BambuStudio/master/resources/profiles/BBL/filament/filaments_color_codes.json"
SPOOLMANDB_URL = "https://donkie.github.io/SpoolmanDB/filaments.json"
FILAMENTCOLORS_URL = "https://filamentcolors.xyz/api/swatch/?format=json&page_size=100"
FILAMENTCOLORS_SWATCH = "https://filamentcolors.xyz/swatch/"
USER_AGENT = "OpenPrintHQ/1.0 (filament colour matching)"
MAX_AGE = 7 * 86400

BRANDS = {   # how notes write a brand -> SpoolmanDB manufacturer (None: no colour data)
    "bambu lab": "Bambu Lab", "bambulab": "Bambu Lab", "bambu": "Bambu Lab",
    "prusament": "Prusament", "prusa": "Prusament",
    "sunlu": "Sunlu", "anycubic": "ANYCUBIC", "polyterra": "Polymaker", "polymaker": "Polymaker",
    "polylite": "Polymaker", "azurefilm": "AzureFilm", "azure film": "AzureFilm", "esun": "eSun", "elegoo": "ELEGOO",
    "geeetech": "GEEETECH", "geetech": "GEEETECH", "kexcelled": None, "generic": None, "overture": "Overture",
    "hatchbox": "Hatchbox", "eryone": "Eryone", "jayo": "JAYO", "creality": "Creality", "inland": "Inland",
}
FINISHES = ["silk", "matte", "metal", "galaxy", "marble", "wood", "sparkle", "glow", "translucent", "dual", "pearl"]
FAMILY_BY_FINISH = {"silk": ["PLA Silk+", "PLA Silk"], "matte": ["PLA Matte"], "metal": ["PLA Metal"],
                    "galaxy": ["PLA Galaxy", "PLA Sparkle"], "marble": ["PLA Marble"], "wood": ["PLA Wood"], "sparkle": ["PLA Sparkle"],
                    "glow": ["PLA Glow"], "translucent": ["PLA Translucent"]}
FAMILY_BY_MATERIAL = {"PLA": ["PLA Basic"], "PETG": ["PETG HF", "PETG Basic"], "ABS": ["ABS"], "ASA": ["ASA"],
                      "TPU": ["TPU for AMS", "TPU 95A HF"], "PLA-CF": ["PLA-CF"], "PETG-CF": ["PETG-CF"], "PC": ["PC"]}
# colour names without a catalog entry, for an approximate code
NAMED = {
    "white": "#FFFFFF", "black": "#000000", "grey": "#808080", "gray": "#808080", "silver": "#C0C0C0", "gold": "#D4AF37",
    "red": "#C8102E", "blue": "#0A3D91", "green": "#2E8B3D", "brown": "#7B4A2D", "beige": "#E8D8B8", "tan": "#D2B48C",
    "pink": "#F4A7B9", "orange": "#FF7F32", "yellow": "#F4E04D", "purple": "#6A3FA0", "mint": "#A8E6CF",
    "sage": "#9CAF88", "bordeaux": "#6D1A36", "oak": "#B58B5A", "chocolate": "#5A3825", "latte": "#D3B7A7",
    "desert tan": "#E8DBB7", "peach": "#FFBE98", "copper": "#B87333", "champagne": "#E8D4A9", "cream": "#F5ECD7",
    "ivory": "#FFFFF0", "off white": "#F4F1EA", "bone white": "#E3DAC9", "malt": "#C9A66B", "candy": "#F7A1C4",
    "peanut": "#C8A26B", "earth brown": "#6B4E35", "wood brown": "#8B5A2B", "brick red": "#9C3A2E", "teal": "#008080",
    "navy": "#1F2A44", "olive": "#6B7A3A", "lavender": "#B9A7D6", "anthracite": "#3F4647", "charcoal": "#36454F",
}


# ---------- catalogs ----------

_mem: dict = {}


def _cache_path(name: str) -> Path:
    p = Path(settings().data_dir) / "catalogs"
    p.mkdir(parents=True, exist_ok=True)
    return p / name


def _load(name: str, url: str, refresh: bool = False):
    hit = _mem.get(name)
    if hit and not refresh and time.time() - hit[0] < 3600:
        return hit[1]
    path = _cache_path(name)
    data = None
    stale = not path.exists() or time.time() - path.stat().st_mtime > MAX_AGE
    if refresh or stale:
        try:
            r = httpx.get(url, timeout=60, follow_redirects=True)
            r.raise_for_status()
            data = r.json()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        except Exception as e:  # noqa: BLE001
            log.warning("could not refresh %s: %s", name, e)
    if data is None and path.exists():
        data = json.loads(path.read_text())
    _mem[name] = (time.time(), data)
    return data


def bambu_catalog(refresh: bool = False) -> list[dict]:
    """Bambu Lab's colours: [{family, fila_id, name, code, hex, multi}]"""
    raw = _load("bambu_colors.json", BAMBU_URL, refresh) or {}
    hit = _mem.get("bambu_parsed")
    if hit and hit[0] is raw:
        return hit[1]
    out = []
    for x in raw.get("data") or []:
        cols = x.get("fila_color") or []
        if not cols:
            continue
        h = cols[0][:7].upper()
        alpha = cols[0][7:9].upper() if len(cols[0]) >= 9 else "FF"
        out.append({"family": x.get("fila_type"), "fila_id": x.get("fila_id"),
                    "name": (x.get("fila_color_name") or {}).get("en"), "code": x.get("fila_color_code"),
                    "hex": h, "multi": len(cols) > 1 or x.get("fila_color_type") != "单色", "clear": alpha == "00",
                    "colors": [c[:7].upper() for c in cols]})
    _mem["bambu_parsed"] = (raw, out)
    return out


def spoolmandb(refresh: bool = False) -> list[dict]:
    return _load("spoolmandb.json", SPOOLMANDB_URL, refresh) or []


def _fetch_filamentcolors() -> list[dict]:
    """Every published swatch, slimmed down (about 23 pages of 100)."""
    out, url = [], FILAMENTCOLORS_URL
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
        for _ in range(200):
            if not url:
                break
            r = c.get(url)
            r.raise_for_status()
            d = r.json()
            for x in d.get("results") or []:
                h = norm_hex(x.get("hex_color"))
                if not h:
                    continue
                ft = x.get("filament_type") or {}
                out.append({"brand": (x.get("manufacturer") or {}).get("name"), "name": x.get("color_name"),
                            "type": ft.get("name"), "parent": (ft.get("parent_type") or {}).get("name"),
                            "hex": h, "td": x.get("td"), "slug": x.get("slug")})
            url = d.get("next")
            time.sleep(0.3)
    return out


def filamentcolors(refresh: bool = False) -> list[dict]:
    """Measured filament colours from FilamentColors.xyz: [{brand, name, type, parent, hex, td, slug}]"""
    name = "filamentcolors.json"
    hit = _mem.get(name)
    if hit and not refresh and time.time() - hit[0] < 3600:
        return hit[1]
    path = _cache_path(name)
    data = None
    if refresh or not path.exists() or time.time() - path.stat().st_mtime > MAX_AGE:
        try:
            data = _fetch_filamentcolors()
            if len(data) < 100:
                raise ValueError(f"only {len(data)} swatches")
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        except Exception as e:  # noqa: BLE001
            log.warning("could not refresh FilamentColors: %s", e)
            data = None
    if data is None and path.exists():
        data = json.loads(path.read_text())
    data = data or []
    _mem[name] = (time.time(), data)
    return data


def _brand_key(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_FC_FINISH = {"silk": "silk", "matte": "matte", "metal": "metal", "metallic": "metal", "galaxy": "galaxy",
              "marble": "marble", "wood": "wood", "sparkle": "sparkle", "glow": "glow", "translucent": "translucent",
              "transparent": "translucent", "glass": "translucent", "clear": "translucent", "dual": "dual",
              "tri": "dual", "pearl": "pearl", "starlight": "sparkle", "glitter": "sparkle"}


def _finishes_in(text: str | None) -> set:
    return {_FC_FINISH[w] for w in re.findall(r"[a-z]+", (text or "").lower()) if w in _FC_FINISH}


def _fc_material(x: dict) -> str:
    from .slicing import material_type
    t = (x.get("type") or "").upper()
    if "CARBON" in t or "-CF" in t or t.endswith(" CF") or "/CF" in (x.get("name") or "").upper():
        base = "PETG" if "PETG" in t else "PLA" if "PLA" in t else t
        return base + "-CF"
    for m in ("PETG", "PLA", "ABS", "ASA", "TPU", "PCTG", "PC", "PA"):
        if re.search(rf"\b{m}\b", t) or t.startswith(m):
            return material_type(m)
    return material_type(x.get("parent") or t)


def lookup_measured(brand: str | None, material: str | None, name: str | None, finish: list | None = None) -> dict | None:
    """The requested colour as measured by FilamentColors.xyz (same brand, same colour name, same finish)."""
    manu = BRANDS.get((brand or "").lower(), brand)
    if not manu or not name:
        return None
    want, bk = _norm_name(name), _brand_key(manu)
    if not want or not bk:
        return None
    from .slicing import material_type
    mat = material_type(material) if material else None
    fin_req = set(finish or []) | _finishes_in(name)
    best, score = None, 0.0
    for x in filamentcolors():
        xb = _brand_key(x.get("brand"))
        if not xb or not (xb == bk or xb.startswith(bk) or bk.startswith(xb)):
            continue
        got = _norm_name(re.sub(r"(?i)\b(silk|matte|metallic|metal|galaxy|marble|sparkle|glow|translucent)\b", " ", x.get("name") or ""))
        want_core = _norm_name(re.sub(r"(?i)\b(silk|matte|metallic|metal|galaxy|marble|sparkle|glow|translucent)\b", " ", name))
        if not got or not want_core:
            continue
        s = SequenceMatcher(None, want_core, got).ratio()
        if set(want_core.split()) == set(got.split()):
            s = max(s, 0.95)
        fin_x = _finishes_in(f"{x.get('type')} {x.get('name')}")
        if fin_req != fin_x:              # a silk or matte swatch is a different colour from the plain one
            continue
        xm = _fc_material(x)
        if mat and xm != mat:
            s -= 0.2
        elif mat and (material or "").upper() in (x.get("type") or "").upper():
            s += 0.03
        if s > score:
            best, score = x, s
    if best and score >= 0.85:
        return {"hex": best["hex"], "td": best.get("td"), "url": FILAMENTCOLORS_SWATCH + best["slug"] if best.get("slug") else None,
                "matched": f"{best['brand']} {best['type']} {best['name']}", "score": round(score, 2)}
    return None


def _bambu_measured(cat: list[dict]) -> dict:
    """(family, name) -> measured Bambu Lab swatch from FilamentColors.xyz."""
    out: dict = {}
    sw = [x for x in filamentcolors() if _brand_key(x.get("brand")) == "bambulab"]
    for x in sw:
        full = f"{x.get('type')} {x.get('name')}"
        toks = set(_norm_name(full.replace("Grey", "Gray").replace("grey", "gray")).split())
        fin_x = _finishes_in(full)
        xm = _fc_material(x)
        best, bscore = None, 0
        for c in cat:
            if c["multi"]:
                continue
            if _finishes_in(c["family"]) != fin_x or _fc_material({"type": c["family"]}) != xm:
                continue
            ctoks = set(_norm_name((c["name"] or "").replace("Grey", "Gray")).split()) - {"matte", "silk", "metallic"}
            if not ctoks or not ctoks <= toks:
                continue
            extra = len(toks - ctoks - set(_norm_name(c["family"]).split()) - {"matte", "silk", "metallic", "metal", "pla", "petg", "hf", "basic"})
            sc = len(ctoks) * 10 - extra
            if sc > bscore:
                best, bscore = c, sc
        if best and (best["family"], best["name"]) not in out:
            out[(best["family"], best["name"])] = {"hex": x["hex"], "td": x.get("td"),
                                                    "url": FILAMENTCOLORS_SWATCH + x["slug"] if x.get("slug") else None}
    return out


def with_measured(cat: list[dict]) -> list[dict]:
    """The Bambu catalog with each colour's measured code added where FilamentColors.xyz has it."""
    key = ("with_measured", id(cat), len(cat), id(filamentcolors()))
    hit = _mem.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    out = _with_measured(cat)
    _mem[key] = (time.time(), out)
    return out


def _with_measured(cat: list[dict]) -> list[dict]:
    try:
        meas = _bambu_measured(cat)
    except Exception as e:  # noqa: BLE001
        log.warning("measured Bambu colours unavailable: %s", e)
        meas = {}
    out = []
    for c in cat:
        m = meas.get((c["family"], c["name"]))
        out.append({**c, "measured_hex": m["hex"], "measured_url": m["url"]} if m else dict(c))
    return out


# ---------- colour maths ----------

def _lab(hexc: str) -> tuple[float, float, float]:
    h = hexc.lstrip("#")[:6]
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92 for c in rgb]
    x = (lin[0] * 0.4124 + lin[1] * 0.3576 + lin[2] * 0.1805) / 0.95047
    y = (lin[0] * 0.2126 + lin[1] * 0.7152 + lin[2] * 0.0722) / 1.0
    z = (lin[0] * 0.0193 + lin[1] * 0.1192 + lin[2] * 0.9505) / 1.08883
    f = [t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in (x, y, z)]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def delta_e(h1: str, h2: str) -> float:
    """CIEDE2000 colour difference between two #RRGGBB colours (about 1 = just noticeable, 10+ = clearly different)."""
    L1, a1, b1 = _lab(h1)
    L2, a2, b2 = _lab(h2)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dL, dC = L2 - L1, C2p - C1p
    dh = 0 if C1p * C2p == 0 else (h2p - h1p if abs(h2p - h1p) <= 180 else h2p - h1p - 360 if h2p > h1p else h2p - h1p + 360)
    dH = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dh / 2))
    Lb, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hb = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hb = (h1p + h2p) / 2
    else:
        hb = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hb - 30)) + 0.24 * math.cos(math.radians(2 * hb))
         + 0.32 * math.cos(math.radians(3 * hb + 6)) - 0.20 * math.cos(math.radians(4 * hb - 63)))
    SL = 1 + 0.015 * (Lb - 50) ** 2 / math.sqrt(20 + (Lb - 50) ** 2)
    SC, SH = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    RT = -2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) * math.sin(math.radians(60 * math.exp(-((hb - 275) / 25) ** 2)))
    return math.sqrt((dL / SL) ** 2 + (dC / SC) ** 2 + (dH / SH) ** 2 + RT * (dC / SC) * (dH / SH))


def norm_hex(h: str | None) -> str | None:
    if not h:
        return None
    m = re.fullmatch(r"#?([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?", h.strip())
    return "#" + m.group(1).upper() if m else None


# ---------- reading what a model asks for ----------

def _clean(s: str) -> str:
    s = re.sub(r"[“”„″]", '"', s or "")
    s = re.sub(r"^\s*(filament(s)? used|filament)\s*[:–-]\s*", "", s, flags=re.I)
    return s.strip()


def _split_outside_quotes(s: str) -> list[str]:
    parts, cur, q = [], "", False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == '"':
            q = not q
            cur += ch
            i += 1
            continue
        if not q:
            m = re.match(r"\s*(?:&|,|\band\b|\s[–-]\s)\s*", s[i:])
            if m and m.group(0).strip():
                parts.append(cur)
                cur = ""
                i += len(m.group(0))
                continue
        cur += ch
        i += 1
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _brand_of(text: str) -> tuple[str | None, str]:
    low = text.lower()
    for alias in sorted(BRANDS, key=len, reverse=True):
        if low.startswith(alias + " ") or low == alias:
            return alias, text[len(alias):].strip()
    return None, text


MAT_RX = re.compile(r"(?<![\w])(PLA\+|PLA|PETG|ABS|ASA|TPU|PC|HS|Silk|Matte|Metal|Galaxy|Marble|Wood|Sparkle|Glow|Pearl|Dual Color|Filament|Basic|Pro|High Speed)(?![\w+])", re.I)
PLAIN = {"white": "#FFFFFF", "bright white": "#FFFFFF", "pure white": "#FFFFFF", "snow white": "#FFFFFF",
         "black": "#000000", "jet black": "#101010", "pure black": "#000000"}


def parse_note(note: str | None) -> list[dict]:
    """'Sunlu PLA+ "Bone White" & "Latte Brown"' -> [{brand, material, finish, name, hex}, ...]"""
    out = []
    note = _clean(note or "")
    if not note or note.lower() in ("view filament", "pla", "generic pla", "petg"):
        return out
    brand = material = None
    finishes: set = set()
    for part in _split_outside_quotes(note):
        alias, rest = _brand_of(part)
        if alias:
            brand, finishes, material = alias, set(), None
        mats = MAT_RX.findall(rest.split('"')[0])
        for m in mats:
            mu = m.upper()
            if mu in ("PLA+", "PLA", "PETG", "ABS", "ASA", "TPU", "PC"):
                material = mu
            elif m.lower() in FINISHES:
                finishes = finishes | {m.lower()}
        hexm = re.search(r"#([0-9A-Fa-f]{6})\b", rest)
        quoted = re.findall(r'"([^"]+)"', rest)
        if quoted:
            names = quoted
        else:
            name = MAT_RX.sub(" ", re.sub(r"#[0-9A-Fa-f]{6}", " ", rest))
            name = re.sub(r"[()]", " ", re.sub(r"\s+", " ", name)).strip(" -–")
            names = [name] if name else []
        for nm in names or ([None] if hexm else []):
            f = set(finishes) | {w for w in FINISHES if nm and re.search(rf"\b{w}\b", nm, re.I)}
            out.append({"brand": alias or brand, "material": material or "PLA", "finish": sorted(f),
                        "name": nm.strip() if nm else None, "hex": norm_hex(hexm.group(0)) if hexm else None})
    return out


def _norm_name(s: str) -> str:
    s = re.sub(r"[™®]", "", s or "").lower()
    s = re.sub(r"\b(pla\+?|petg|polyterra|polylite|panchroma|basic|original|filament|pla-basic|high speed|hs|formerly)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def lookup_brand_color(brand: str | None, material: str | None, name: str | None) -> dict | None:
    """The brand's own colour code for a named colour, from SpoolmanDB."""
    manu = BRANDS.get((brand or "").lower())
    if not manu or not name:
        return None
    want = _norm_name(name)
    if not want:
        return None
    best, score = None, 0.0
    for x in spoolmandb():
        if x.get("manufacturer", "").lower() != manu.lower() or not x.get("color_hex"):
            continue
        got = _norm_name(x.get("name"))
        if not got:
            continue
        s = SequenceMatcher(None, want, got).ratio()
        if set(want.split()) <= set(got.split()):
            s = max(s, 0.9 if len(got.split()) - len(want.split()) <= 1 else 0.8)
        mat = (x.get("material") or "").upper()
        if material and mat == (material or "").upper():
            s += 0.05
        elif material and mat.startswith("PLA") and (material or "").startswith("PLA"):
            s += 0.02
        if s > score:
            best, score = x, s
    if best and score >= 0.82:
        return {"hex": norm_hex(best["color_hex"][:6]), "matched": f"{best['manufacturer']} {best['material']} {best['name']}",
                "score": round(score, 2)}
    return None


def approx_color(name: str | None) -> str | None:
    if not name:
        return None
    low = name.lower()
    for k in sorted(NAMED, key=len, reverse=True):
        if re.search(rf"\b{re.escape(k)}\b", low):
            return NAMED[k]
    return None


def bambu_by_name(name: str | None, material: str | None = None, finish: list | None = None) -> dict | None:
    """Bambu Lab's colour with exactly this name (Latte Brown, Bone White, Desert Tan...): within the requested
    finish's line when there is one, else the plain lines first (Basic, Matte), same material."""
    nm = _norm_name(name)
    if not nm:
        return None
    from .slicing import material_type
    mat = material_type(material)
    fams = []
    for f in finish or []:
        fams += FAMILY_BY_FINISH.get(f, [])
    if not fams:
        fams = FAMILY_BY_MATERIAL.get(mat, []) + (["PLA Matte"] if mat == "PLA" else [])
    cat = bambu_catalog()
    for fam in fams:
        for c in cat:
            if c["family"] == fam and _norm_name(c["name"]) == nm and not c["multi"]:
                return c
    return None


def requested_for(model, project_slots: list[dict] | None = None) -> dict:
    """What the designer asked for, slot by slot, with a colour code and where it came from."""
    ps = model.print_settings or {}
    note, link = ps.get("filament"), ps.get("filament_link")
    named = parse_note(note)
    proj = [s for s in (project_slots or []) if s.get("used")]
    photo = (getattr(model, "extra", None) or {}).get("filament_photo")
    if not named and not proj and photo and photo.get("main"):
        # nothing recorded by the designer: the colour read from the product photos (reviewed and approved)
        mn = photo["main"]
        return {"note": note, "link": link, "photo": {k: photo.get(k) for k in ("approved_by", "approved_at", "method")},
                "slots": [{"slot": 1, "brand": None, "material": "PLA", "finish": list(mn.get("finish") or []),
                           "name": mn.get("name"), "hex": norm_hex(mn.get("hex")), "hex_source": "product photo",
                           "photo_accents": photo.get("accents") or []}]}
    n = max(len(named), len(proj), 1 if (note or proj) else 0)
    slots = []
    for i in range(n):
        req = dict(named[i]) if i < len(named) else {"brand": None, "material": None, "finish": [], "name": None, "hex": None}
        p = proj[i] if i < len(proj) else None
        if p:
            req["project_preset"] = p.get("preset")
            req["project_hex"] = norm_hex(p.get("hex"))
            if not req.get("material") and p.get("type"):
                req["material"] = p.get("type")
            if not req.get("brand") and p.get("vendor") and p.get("vendor").lower() not in ("generic",):
                req["brand"] = p.get("vendor").split()[0].lower()
        src = None
        meas = None if req.get("hex") else lookup_measured(req.get("brand"), req.get("material"), req.get("name"), req.get("finish"))
        if req.get("hex"):
            src = "note"
        elif meas:
            req["hex"], req["catalog_match"], req["catalog_url"], src = meas["hex"], meas["matched"], meas["url"], "measured"
            if meas.get("td") is not None:
                req["td"] = meas["td"]
        elif _norm_name(req.get("name")) in PLAIN and not [f for f in req.get("finish") or [] if f in FAMILY_BY_FINISH]:
            # plain white or black: brands' published codes for these vary a lot (some "whites" are grey)
            req["hex"], src = PLAIN[_norm_name(req.get("name"))], "colour name"
        else:
            bc = lookup_brand_color(req.get("brand"), req.get("material"), req.get("name"))
            if bc:
                req["hex"], req["catalog_match"], src = bc["hex"], bc["matched"], "SpoolmanDB"
            elif bambu_by_name(req.get("name"), req.get("material"), req.get("finish")):
                b = bambu_by_name(req.get("name"), req.get("material"), req.get("finish"))
                req["hex"], req["catalog_match"], src = b["hex"], f"Bambu Lab {b['family']} {b['name']}", "Bambu colour of the same name"
            elif req.get("project_hex") and not (req.get("name") and req["project_hex"] in ("#000000", "#FFFFFF")
                                                 and approx_color(req.get("name")) not in (req["project_hex"], None)):
                req["hex"], src = req["project_hex"], "project file"
            elif approx_color(req.get("name")):
                req["hex"], src = approx_color(req.get("name")), "approximate (colour name)"
            elif req.get("project_hex"):
                req["hex"], src = req["project_hex"], "project file"
        req["hex_source"] = src
        req["slot"] = i + 1
        if req.get("brand"):
            req["brand_label"] = BRANDS.get(req["brand"].lower()) or req["brand"].title()
        slots.append(req)
    return {"note": note, "link": link, "slots": slots}


# ---------- matching to a brand ----------

def match_bambu(req: dict, catalog: list[dict] | None = None) -> dict | None:
    """The closest Bambu Lab filament: the same finish when Bambu has it, the nearest colour by CIEDE2000."""
    catalog = catalog if catalog is not None else bambu_catalog()
    if not catalog:
        return None
    from .slicing import material_type
    mat = material_type(req.get("material"))
    fin = [f for f in (req.get("finish") or []) if f in FAMILY_BY_FINISH]
    families = []
    for f in fin:
        families += FAMILY_BY_FINISH[f]
    families += FAMILY_BY_MATERIAL.get(mat, ["PLA Basic"])
    if mat == "PLA" and not fin:
        families += ["PLA Matte"]          # earthy and pastel tones Bambu only makes in Matte
    want_multi = "dual" in (req.get("finish") or []) or bool(re.search(r"\b\w+\s*[–-]\s*\w+\b", req.get("name") or ""))
    # exact name within a Bambu request ("Bambu Lab PLA Matte Desert Tan")
    if (req.get("brand") or "").lower() in ("bambu", "bambu lab", "bambulab") and req.get("name"):
        nm = _norm_name(req["name"])
        for fam in families[:1]:            # only within the product line asked for
            for c in catalog:
                if c["family"] == fam and _norm_name(c["name"]) == nm:
                    return {**c, "delta_e": 0.0, "why": "same Bambu Lab colour"}
    # the same colour name in an allowed Bambu line wins outright (Latte Brown -> PLA Matte Latte Brown)
    nm = _norm_name(req.get("name"))
    if nm:
        for fam in (families[:len([f for f in fin for _ in FAMILY_BY_FINISH[f]])] if fin else families):
            for c in catalog:
                if c["family"] == fam and _norm_name(c["name"]) == nm and not c["multi"]:
                    exact_hex = req.get("hex") and not str(req.get("hex_source") or "").startswith("approx")
                    cmp = (c.get("measured_hex") or c["hex"]) if req.get("hex_source") == "measured" else c["hex"]
                    return {**c, "delta_e": round(delta_e(req["hex"], cmp), 1) if exact_hex else 0.0,
                            "why": f"same colour name in {c['family']}"}
    target = req.get("hex")
    measured = req.get("hex_source") == "measured"

    def chex(c):                         # the Bambu colour code to compare against
        return (c.get("measured_hex") or c["hex"]) if measured else c["hex"]
    if not target:
        c = next((c for c in catalog if c["family"] == families[0] and c["name"] in ("Black", "Jade White")), None)
        return {**c, "delta_e": None, "why": "no colour requested"} if c else None
    def nearest(fams, penalty=2):         # a later line has to be clearly closer to win
        best = None
        for rank, fam in enumerate(fams):
            for c in catalog:
                if c["family"] != fam or c["clear"] or (c["multi"] and not want_multi):
                    continue
                d = delta_e(target, chex(c))
                cost = d + penalty * rank
                if best is None or cost < best[0]:
                    best = (cost, d, c)
        return best
    fin_fams = [f for f in families if any(f in FAMILY_BY_FINISH[x] for x in fin)]
    best = nearest(fin_fams, 2) if fin_fams else None
    if not best or best[1] > 12:        # the finish only when Bambu has a genuinely close colour in it
        plain = nearest([f for f in families if f not in fin_fams])
        if plain and (not best or plain[1] < best[1]):
            best = plain
    if not best:
        return None
    cost, d, c = best
    why = f"closest {c['family']} colour (difference {d:.1f}{', measured' if measured and c.get('measured_hex') else ''})"
    return {**c, "delta_e": round(d, 1), "why": why}


def resolve(model, project_slots: list[dict] | None = None) -> dict:
    req = requested_for(model, project_slots)
    cat = with_measured(bambu_catalog())
    for s in req["slots"]:
        m = match_bambu(s, cat)
        s["bambu"] = {k: m.get(k) for k in ("family", "fila_id", "name", "code", "hex", "measured_hex", "measured_url",
                                            "delta_e", "why") if m.get(k) is not None or k in ("delta_e",)} if m else None
    return req


def project_slots_of(path: Path) -> list[dict]:
    """The filament list saved in a Bambu Studio / OrcaSlicer project, with which slots objects use."""
    import zipfile
    try:
        z = zipfile.ZipFile(path)
        st = json.loads(z.read("Metadata/project_settings.config"))
    except Exception:  # noqa: BLE001
        return []

    def lst(k):
        v = st.get(k)
        return v if isinstance(v, list) else ([v] if v else [])
    ids, ven, typ, col = lst("filament_settings_id"), lst("filament_vendor"), lst("filament_type"), lst("filament_colour")
    used = set()
    try:
        ms = z.read("Metadata/model_settings.config").decode(errors="ignore")
        used = {int(m.group(1)) for m in re.finditer(r'key="extruder" value="(\d+)"', ms)}
        try:
            for m in re.finditer(r'paint_color="([0-9A-F]+)"', z.read("3D/3dmodel.model").decode(errors="ignore")[:5_000_000]):
                pass
        except KeyError:
            pass
    except KeyError:
        pass
    n = max(len(ids), len(typ), len(col))
    return [{"slot": i + 1, "preset": ids[i] if i < len(ids) else None, "vendor": ven[i] if i < len(ven) else None,
             "type": typ[i] if i < len(typ) else None, "hex": col[i] if i < len(col) else None,
             "used": (i + 1) in used if used else i == 0} for i in range(n)]



# ---------- applying it to a model ----------

def preset_family(family: str | None) -> str | None:
    """Bambu catalog family -> the start of its slicer preset name ("PLA Matte" -> "Bambu PLA Matte")."""
    return f"Bambu {family}" if family else None


def document(db, model, refresh: bool = False) -> dict:
    """Work out (or reuse) what the model's designer asked for, and keep it on the model."""
    from ..config import settings as _s
    extra = dict(model.extra or {})
    if extra.get("filament_request") and not refresh:
        return extra["filament_request"]
    lib = Path(_s().library_root)
    ps: list = []
    src_file = None
    for f in model.files:
        if f.library_rel and f.filename.lower().endswith(".3mf") and f.kind != "3mf_sliced":
            ps = project_slots_of(lib / f.library_rel)
            if ps:
                src_file = f.filename
                break
    req = resolve(model, ps)
    req["project_file"] = src_file
    req["documented_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    extra["filament_request"] = req
    model.extra = extra
    return req


def apply_brand(db, model, brand: str = "Bambu Lab", refresh: bool = False) -> dict:
    """Put the brand's closest filament for each requested colour into the materials list (slots in order).
    The designer's original request stays in model.extra["filament_request"]; only the materials list changes."""
    from ..models import BomFilament
    from . import importer
    req = document(db, model, refresh=refresh)
    from .slicing import production_result
    pr = production_result(db, model)
    if pr is not None and pr.source == "file":
        # a file chosen for the default printer prints its own colours; they are the materials list (sync_bom)
        return {"changed": 0, "reason": "the production file sets the colours", "production_file": True}
    if not req["slots"]:
        return {"changed": 0, "reason": "no filament recorded for this model"}
    prod = model.product or importer.ensure_product(db, model)
    changed = 0
    for s in req["slots"]:
        b = s.get("bambu")
        if not b:
            continue
        row = next((x for x in prod.filaments if x.slot == s["slot"]), None)
        if row is None:
            row = BomFilament(slot=s["slot"], label=f"Color {s['slot']}", waste_pct=5)
            prod.filaments.append(row)
        if row.follows_variant:          # the colour comes from the customer's choice; only the line changes
            new = {"material": b["family"]}
        else:
            new = {"material": b["family"], "color_name": b["name"], "color_hex": b["hex"]}
        if any(getattr(row, k) != v for k, v in new.items()):
            for k, v in new.items():
                setattr(row, k, v)
            row.spoolman_filament_id = None if not row.follows_variant else row.spoolman_filament_id
            changed += 1
    extra = dict(model.extra or {})
    extra["filament_applied"] = {"brand": brand, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "slots": [{"slot": s["slot"], **(s.get("bambu") or {})} for s in req["slots"]]}
    model.extra = extra
    db.flush()
    return {"changed": changed, "slots": len(req["slots"])}
