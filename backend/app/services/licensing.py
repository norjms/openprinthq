"""License rules, designer credit, and license evidence records."""
import hashlib
import io
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Model, LicenseEvidence

log = logging.getLogger("hub.license")

CC = "https://creativecommons.org/licenses"
LICENSES = {
    "OWN":        {"label": "Own design", "sell": True, "credit": False, "derivatives": True, "url": None},
    "COMMERCIAL": {"label": "Commercial license (purchased or membership)", "sell": True, "credit": False,
                   "derivatives": True, "url": None},
    "CC0":        {"label": "Public Domain (CC0)", "sell": True, "credit": False, "derivatives": True,
                   "url": "https://creativecommons.org/publicdomain/zero/1.0/"},
    "BY":         {"label": "CC BY 4.0", "sell": True, "credit": True, "derivatives": True, "url": f"{CC}/by/4.0/"},
    "BY-SA":      {"label": "CC BY-SA 4.0", "sell": True, "credit": True, "derivatives": True, "share_alike": True,
                   "url": f"{CC}/by-sa/4.0/"},
    "BY-ND":      {"label": "CC BY-ND 4.0", "sell": True, "credit": True, "derivatives": False, "url": f"{CC}/by-nd/4.0/"},
    "BY-NC":      {"label": "CC BY-NC 4.0", "sell": False, "credit": True, "derivatives": True, "url": f"{CC}/by-nc/4.0/"},
    "BY-NC-SA":   {"label": "CC BY-NC-SA 4.0", "sell": False, "credit": True, "derivatives": True,
                   "url": f"{CC}/by-nc-sa/4.0/"},
    "BY-NC-ND":   {"label": "CC BY-NC-ND 4.0", "sell": False, "credit": True, "derivatives": False,
                   "url": f"{CC}/by-nc-nd/4.0/"},
    "SDFL":       {"label": "MakerWorld Standard Digital File License", "sell": False, "credit": False,
                   "derivatives": False, "url": None},
    "MW-EXCLUSIVE": {"label": "MakerWorld Exclusive License", "sell": False, "credit": False, "derivatives": False,
                     "url": None},
    "PR-COMMERCIAL": {"label": "Printables Commercial Use", "sell": True, "credit": False, "derivatives": True, "url": None},
    "PR-COMMERCIAL-ND": {"label": "Printables Commercial Use - No Derivative", "sell": True, "credit": False,
                         "derivatives": False, "url": None},
    "GPL":        {"label": "GNU GPL", "sell": True, "credit": True, "derivatives": True, "share_alike": True,
                   "url": "https://www.gnu.org/licenses/gpl-3.0.html"},
    "LGPL":       {"label": "GNU LGPL", "sell": True, "credit": True, "derivatives": True, "share_alike": True,
                   "url": "https://www.gnu.org/licenses/lgpl-3.0.html"},
    "BSD":        {"label": "BSD License", "sell": True, "credit": True, "derivatives": True,
                   "url": "https://opensource.org/license/bsd-3-clause"},
    "APACHE":     {"label": "Apache License 2.0", "sell": True, "credit": True, "derivatives": True,
                   "url": "https://www.apache.org/licenses/LICENSE-2.0"},
    "CERN-OHL":   {"label": "CERN Open Hardware Licence v2", "sell": True, "credit": True, "derivatives": True,
                   "share_alike": True, "url": "https://ohwr.org/cern_ohl_s_v2.txt"},
    "OCL":        {"label": "Open Community License (no commercial reselling)", "sell": False, "credit": False,
                   "derivatives": True, "url": "https://github.com/prusa3d/Open-Community-License"},
    "PR-SDFL":    {"label": "Printables Standard Digital File License", "sell": False, "credit": False,
                   "derivatives": False, "url": None},
    "CXY-SL":     {"label": "Creality Cloud Standard License", "sell": False, "credit": False, "derivatives": False,
                   "url": None},
    "STORE-PERSONAL": {"label": "Store purchase or download, personal use only (MyMiniFactory, Cults3D, Thangs)",
                       "sell": False, "credit": False, "derivatives": False, "url": None},
    "DESIGNER-CUSTOM": {"label": "Designer's own license document (read it before selling)", "sell": False,
                        "credit": True, "derivatives": False, "url": None},
    "ARR":        {"label": "All rights reserved", "sell": False, "credit": False, "derivatives": False, "url": None},
    "UNKNOWN":    {"label": "Unknown / not recorded", "sell": False, "credit": False, "derivatives": False, "url": None},
}
SELLABLE = [k for k, v in LICENSES.items() if v["sell"] and k not in ("OWN", "COMMERCIAL")]
SELLABLE_MAKERWORLD = ["CC0", "BY", "BY-SA", "BY-ND"]

# A CC license from the designer can't grant rights to someone else's characters, logos or brands.
IP_TERMS = [
    "pokemon", "pokémon", "pikachu", "nintendo", "mario", "zelda", "kirby", "disney", "pixar", "marvel", "avengers",
    "spider-man", "spiderman", "batman", "superman", "dc comics", "star wars", "mandalorian", "baby yoda", "grogu",
    "darth vader", "harry potter", "hogwarts", "lord of the rings", "lotr", "game of thrones", "minecraft", "fortnite",
    "roblox", "among us", "sonic", "sanrio", "hello kitty", "mickey", "lego", "hot wheels",
    "barbie", "transformers", "warhammer", "dungeons & dragons", "d&d", "dnd", "genshin", "zelda", "bluey",
    "paw patrol", "peppa", "simpsons", "studio ghibli", "totoro", "one piece", "naruto", "dragon ball", "demon slayer",
    "jujutsu", "my hero academia", "attack on titan", "sailor moon", "hello kitty", "taco bell", "starbucks",
    "coca-cola", "coca cola", "nike", "adidas", "apple logo", "tesla", "ferrari", "porsche", "lamborghini",
    "nfl", "nba", "mlb", "nhl", "fifa", "premier league", "playstation", "xbox", "gameboy", "game boy", "tcg",
    "yu-gi-oh", "yugioh", "magic the gathering", "mtg", "stanley cup", "f1", "formula 1", "hulk", "deadpool",
    "wednesday addams", "squid game", "stranger things", "doctor who", "star trek", "ghostbusters", "jurassic",
    "godzilla", "labubu", "milwaukee", "dewalt", "makita", "ryobi", "festool", "stanley", "yeti", "hydro flask", "owala", "groot", "yoda", "grinch", "snoopy", "garfield", "shrek", "minion", "minions", "pac-man", "pacman", "tetris", "zelda", "bowser", "yoshi", "luigi", "eevee", "charizard", "gengar", "totoro", "baby groot", "venom", "thanos", "mjolnir", "captain america", "iron man", "wakanda", "jedi", "sith", "lightsaber", "stormtrooper", "r2-d2", "bb-8", "hogwarts", "gryffindor", "slytherin", "hufflepuff", "ravenclaw", "pokeball", "poke ball", "skyrim", "master chief", "overwatch", "destiny 2", "elden ring", "hollow knight", "cuphead", "bluey", "spongebob", "scooby", "looney", "bugs bunny", "tom and jerry", "winnie the pooh", "toy story", "buzz lightyear", "cars movie", "lightning mcqueen", "moana", "encanto", "coraline", "nightmare before christmas", "jack skellington", "chucky", "freddy krueger", "jason voorhees", "pennywise", "ghostface", "alien xenomorph", "predator mask", "terminator", "batmobile", "gotham", "joker", "harley quinn", "wonder woman", "the flash", "aquaman", "funko", "squishmallow", "bambu lab logo",
]
_IP_RX = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in sorted(set(IP_TERMS), key=len, reverse=True)) + r")(?![a-z0-9])")


def ip_flags(*texts) -> list[str]:
    """Brand / character names found in titles, tags or descriptions (a prompt for a human check, not a verdict)."""
    hay = " ".join(str(t) for t in texts if t).lower()
    return sorted(set(_IP_RX.findall(hay)))


def normalize(text: str | None, platform: str | None = None) -> str:
    """Map a license string from MakerWorld, Printables, Thingiverse, Creality Cloud, Thangs, MyMiniFactory, Cults3D
    or a web page to a hub code. The raw text is always stored next to the code (license_raw / evidence)."""
    if not text:
        return "UNKNOWN"
    raw = text.strip()
    t = raw.upper()
    if t in LICENSES:
        return t
    if t.startswith("THANGS DESIGNER LICENSE"):
        return "DESIGNER-CUSTOM"
    if platform == "myminifactory" and t.startswith("MYMINIFACTORY") and "STORE" not in t:
        # MyMiniFactory's own icons: Credit (attribution), Remix (derivatives allowed), Noncommercial / Commercial.
        nc = "NONCOMMERCIAL" in t.replace("-", "").replace(" ", "") or "NON COMMERCIAL" in t
        nd = "NO REMIX" in t or "NOREMIX" in t.replace(" ", "") or ("REMIX" not in t)
        return "BY" + ("-NC" if nc else "") + ("-ND" if nd else "")
    if platform in ("myminifactory", "cults3d", "thangs") and ("STORE LICENSE" in t or "STANDARD DIGITAL" in t
                                                               or "PRIVATE USE" in t or "PERSONAL USE" in t):
        return "STORE-PERSONAL"
    if platform == "cults3d" and t.startswith("CULTS"):
        return "STORE-PERSONAL" if ("PRIVATE" in t or "PERSONAL" in t or "STANDARD" in t) else "UNKNOWN"
    if "STANDARD DIGITAL" in t:
        return "PR-SDFL" if platform == "printables" else "SDFL"
    if "EXCLUSIVE" in t:
        return "MW-EXCLUSIVE"
    if t in ("CXY-SL",) or "CREALITY" in t and "STANDARD" in t:
        return "CXY-SL"
    if "OCL" in t or "OPEN COMMUNITY" in t:
        return "OCL"
    if t.startswith("COMM") and ("DERIV" in t or "ND" in t.split()):
        return "PR-COMMERCIAL-ND"
    if t.startswith("COMMERCIAL"):
        return "PR-COMMERCIAL"
    if "LGPL" in t or "LESSER GENERAL" in t:
        return "LGPL"
    if "GPL" in t or "GENERAL PUBLIC" in t:
        return "GPL"
    if t.startswith("BSD"):
        return "BSD"
    if "APACHE" in t:
        return "APACHE"
    if "CERN" in t:
        return "CERN-OHL"
    if "ALL RIGHTS" in t:
        return "ARR"
    if t in ("CC0", "PUBLIC DOMAIN", "PD", "CC0 1.0") or "PUBLIC DOMAIN" in t or "CC0" in t:
        return "CC0"
    # Creative Commons, any spelling: "CC-BY-NC-SA", "CC BY-SA 4.0", "Creative Commons - Attribution - Non-Commercial"
    words = t.replace("CREATIVE COMMONS", "CC")
    if "ATTRIBUTION" in words or words.startswith("CC") or words.startswith("BY"):
        nc = "NON-COMMERCIAL" in words or "NONCOMMERCIAL" in words or re.search(r"(^|[^A-Z])NC([^A-Z]|$)", words)
        nd = "NO DERIV" in words or "NODERIV" in words or re.search(r"(^|[^A-Z])ND([^A-Z]|$)", words)
        sa = "SHARE ALIKE" in words or "SHAREALIKE" in words or re.search(r"(^|[^A-Z])SA([^A-Z]|$)", words)
        if "ATTRIBUTION" in words or re.search(r"(^|[^A-Z])BY([^A-Z]|$)", words):
            code = "BY" + ("-NC" if nc else "") + ("-SA" if sa else "") + ("-ND" if nd else "")
            return code if code in LICENSES else "UNKNOWN"
    return "UNKNOWN"


def info(code: str | None) -> dict:
    return {"code": code or "UNKNOWN", **LICENSES.get(code or "UNKNOWN", LICENSES["UNKNOWN"])}


def apply(model: Model, code: str):
    model.license_code = code
    model.license_ok = LICENSES.get(code, LICENSES["UNKNOWN"])["sell"]


def needs_credit(model: Model) -> bool:
    lic = LICENSES.get(model.license_code or "UNKNOWN", LICENSES["UNKNOWN"])
    return bool(lic["credit"] or (model.designer and model.designer.always_credit))


def credit_line(model: Model) -> str | None:
    """Plain-text attribution for the store listing, or None when nothing is required."""
    if model.credit_override:
        return model.credit_override
    if not needs_credit(model) or not model.designer:
        return None
    d = model.designer
    lic = info(model.license_code)
    tpl = d.credit_template or "Design by {designer}{profile}. {license_part}"
    return tpl.format(designer=d.name, profile=f" ({d.url})" if d.url else "",
                      license_part=f"Licensed under {lic['label']}." if lic["credit"] else "",
                      license=lic["label"], title=model.name, source=model.source_url or "").strip()


def credit_html(model: Model) -> str:
    line = credit_line(model)
    if not line:
        return ""
    import html
    lic = info(model.license_code)
    parts = [html.escape(line)]
    links = []
    if model.source_url:
        links.append(f'<a href="{html.escape(model.source_url)}" rel="nofollow noopener" target="_blank">original design</a>')
    if lic.get("url") and lic["credit"]:
        links.append(f'<a href="{lic["url"]}" rel="license noopener" target="_blank">{html.escape(lic["label"])}</a>')
    if links:
        parts.append(" (" + ", ".join(links) + ")")
    return '<p class="artisan-credit"><em>' + "".join(parts) + "</em></p>"


def grant_status(g, today=None) -> dict:
    """active | expiring (<=14 days) | expired | not_started, with days left."""
    from datetime import date
    today = today or date.today()
    if g.starts_on and g.starts_on > today:
        return {"state": "not_started", "days_left": None}
    if g.expires_on is None:
        return {"state": "active", "days_left": None}
    left = (g.expires_on - today).days
    if left < 0:
        return {"state": "expired", "days_left": left}
    return {"state": "expiring" if left <= 14 else "active", "days_left": left}


def grants_for(model: Model) -> list:
    d = model.designer
    if not d:
        return []
    return [g for g in d.grants if g.covers_all or model.code in (g.model_codes or [])]


def commercial_cover(model: Model, today=None) -> dict:
    """For COMMERCIAL models: which recorded grant covers it and whether it is in force."""
    gs = grants_for(model)
    if not gs:
        return {"recorded": False, "active": None, "grant": None}
    st = [(g, grant_status(g, today)) for g in gs]
    live = [x for x in st if x[1]["state"] in ("active", "expiring")]
    if live:
        open_ended = [x for x in live if x[0].expires_on is None]
        g, s = open_ended[0] if open_ended else max(live, key=lambda x: x[0].expires_on)
        return {"recorded": True, "active": True, "grant": g, "status": s}
    perpetual = [x for x in st if not x[0].sales_need_active and x[1]["state"] == "expired"]
    if perpetual:      # e.g. a purchase whose support period ended but the right to sell stays
        return {"recorded": True, "active": True, "grant": perpetual[0][0], "status": perpetual[0][1]}
    from datetime import date
    g, s = max(st, key=lambda x: x[0].expires_on or x[0].starts_on or date.min)
    return {"recorded": True, "active": False, "grant": g, "status": s}


def sale_problems(model: Model) -> list[str]:
    code = model.license_code or ("COMMERCIAL" if model.license_ok else "UNKNOWN")
    lic = LICENSES.get(code, LICENSES["UNKNOWN"])
    if not lic["sell"]:
        return [f"license {lic['label']} does not allow selling prints (record a commercial license to override)"]
    if lic["credit"] and not model.designer and not model.credit_override:
        return ["license requires credit but no designer is set"]
    if code == "COMMERCIAL" and model.designer:
        cov = commercial_cover(model)
        if cov["recorded"] and not cov["active"]:
            g = cov["grant"]
            when = f" on {g.expires_on.isoformat()}" if g.expires_on else ""
            return [f"commercial license '{g.name}' from {model.designer.name} ended{when}; renew it and update the end date"]
    return []


# ---------- evidence ----------
def evidence_dir(model_id: int) -> Path:
    p = Path(settings().data_dir) / "evidence" / str(model_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def wayback(url: str) -> str | None:
    """Ask the Internet Archive to snapshot a URL; returns the archived URL (independent timestamp)."""
    import httpx
    try:
        r = httpx.get(f"https://web.archive.org/save/{url}", timeout=120, follow_redirects=False,
                      headers={"User-Agent": "OpenPrintHQ/1.0 (license record)"})
        loc = r.headers.get("content-location") or r.headers.get("location")
        if loc:
            return loc if loc.startswith("http") else f"https://web.archive.org{loc}"
        if r.status_code in (200, 302):
            return f"https://web.archive.org/web/{datetime.now(timezone.utc):%Y%m%d%H%M%S}/{url}"
    except Exception as e:  # noqa: BLE001
        log.warning("wayback failed for %s: %s", url, e)
    return None


SUMMARIES = {
    "CC0": "Public domain dedication. You may copy, modify and sell prints for any purpose without asking permission or giving credit.",
    "BY": "Creative Commons Attribution 4.0. You may share and adapt the design for any purpose, including commercially, as long as you "
          "give appropriate credit, link to the license, and indicate if changes were made.",
    "BY-SA": "Creative Commons Attribution-ShareAlike 4.0. Commercial use allowed with credit. If you remix or build on the design, "
             "you must distribute your contributions under the same license.",
    "BY-ND": "Creative Commons Attribution-NoDerivatives 4.0. Commercial use allowed with credit. You may not distribute modified "
             "versions of the design.",
    "BY-NC": "Creative Commons Attribution-NonCommercial 4.0. Credit required. Commercial use, including selling prints, is not allowed.",
    "BY-NC-SA": "Creative Commons Attribution-NonCommercial-ShareAlike 4.0. Credit required, no commercial use, remixes under the same license.",
    "BY-NC-ND": "Creative Commons Attribution-NonCommercial-NoDerivatives 4.0. Credit required, no commercial use, no modified versions.",
    "SDFL": "MakerWorld Standard Digital File License: personal use only; selling prints requires separate permission from the designer.",
    "MW-EXCLUSIVE": "MakerWorld Exclusive License: personal use only; selling prints requires separate permission.",
    "PR-COMMERCIAL": "Printables Commercial Use: you may print, sell and distribute physical prints, including modified ones. "
                     "The digital files may not be shared or resold.",
    "PR-COMMERCIAL-ND": "Printables Commercial Use - No Derivative: you may sell physical prints of the model as published, "
                        "not of modified versions. The digital files may not be shared or resold.",
    "GPL": "GNU General Public License: commercial use allowed. Keep the license and copyright notice with the design; "
           "modified designs you distribute must be released under the GPL with their source.",
    "LGPL": "GNU Lesser General Public License: commercial use allowed with the license and copyright notice.",
    "BSD": "BSD License: commercial use allowed; keep the copyright notice and license text.",
    "APACHE": "Apache License 2.0: commercial use allowed; keep the license and notices and mark changes.",
    "CERN-OHL": "CERN Open Hardware Licence v2: products may be made and sold; modified designs you distribute must "
                "be released under the same licence with their source.",
    "OCL": "Open Community License: internal and personal use and remixing are allowed, but commercial reselling of the "
           "design or its prints is not granted. Ask the creator for a business license.",
    "PR-SDFL": "Printables Standard Digital File License: personal use only. No selling of prints, no remixes, no sharing.",
    "CXY-SL": "Creality Cloud Standard License: all rights reserved. Any use beyond personal use needs written permission "
              "from the creator.",
    "ARR": "All rights reserved: selling prints needs written permission from the designer.",
}


def _thumb(cover: bytes, px: int = 520) -> bytes | None:
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(cover))
        im = im.convert("RGB")
        im.thumbnail((px, px))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _plain(t: str | None) -> str:
    if not t:
        return ""
    if "<" in t:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(t, "html.parser")
            for br in soup.find_all("br"):
                br.replace_with("\n")
            for el in soup.find_all(["p", "li", "div", "h1", "h2", "h3", "h4"]):
                el.append("\n\n")
            t = soup.get_text()
        except Exception:  # noqa: BLE001
            t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _team_name(model: Model) -> str:
    """The model's team name for the record's heading, or a plain fallback."""
    try:
        from sqlalchemy.orm import object_session
        from ..models import Team
        t = object_session(model).get(Team, model.team_id)
        return t.name if t else "OpenPrintHQ"
    except Exception:  # noqa: BLE001
        return "OpenPrintHQ"


def render_pdf(path: Path, model: Model, ev: dict, cover: bytes | None):
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, Frame

    esc = lambda v: str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    c = canvas.Canvas(str(path), pagesize=letter)
    W, H = letter
    L = 0.8 * inch
    c.setTitle(f"License record {model.code}")
    c.setAuthor("OpenPrintHQ")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(L, H - 0.9 * inch, f"{_team_name(model)}: license record")
    c.setFont("Helvetica", 9)
    c.drawString(L, H - 1.1 * inch, f"Captured {ev['captured_at']} by OpenPrintHQ")
    top = H - 1.45 * inch
    img_w = 1.7 * inch
    if cover and (tb := _thumb(cover)):
        try:
            img = ImageReader(io.BytesIO(tb))
            iw, ih = img.getSize()
            h = min(img_w * ih / iw, 2.3 * inch)
            w = h * iw / ih
            c.drawImage(img, W - L - w, top - h + 10, w, h)
        except Exception:  # noqa: BLE001
            pass
    lic = info(ev.get("license_code"))
    rows = [("Model", model.name), ("Hub code", model.code), ("Designer", ev.get("designer") or ""),
            ("Designer profile", (model.designer.url if model.designer else "") or ""),
            ("Source", ev.get("source_url") or ""), ("License (as published)", ev.get("license_raw") or ""),
            ("License (normalized)", lic["label"] + (f"  {lic['url']}" if lic.get("url") else "")),
            ("Sale of prints allowed", "Yes" if lic["sell"] else "No"),
            ("Credit required", "Yes" if lic["credit"] else "No"),
            ("Credit used on listings", credit_line(model) or "none"),
            ("Design last updated", ev.get("design_updated") or ""),
            ("Archived copy", ev.get("wayback_url") or "not available"),
            ("Raw data SHA-256", ev.get("json_sha256") or ""), ("Page HTML SHA-256", ev.get("html_sha256") or "")]
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    body.fontSize = 9
    body.leading = 12
    y = top
    val_w = W - 2 * L - 1.6 * inch - img_w - 0.2 * inch
    for k, v in rows:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(L, y, k)
        p = Paragraph(esc(v), body)
        _, ph = p.wrap(val_w, 3 * inch)
        p.drawOn(c, L + 1.6 * inch, y - ph + 9)
        y -= max(ph, 12) + 5
    y -= 12
    c.setFont("Helvetica-Bold", 10)
    c.drawString(L, y, "License terms")
    paras = []
    if lic["code"] in SUMMARIES:
        paras.append(Paragraph("<b>Summary:</b> " + esc(SUMMARIES[lic["code"]]), body))
    page_text = _plain(ev.get("license_text"))
    title = (ev.get("license_title") or "").strip()
    if title or page_text:
        paras.append(Paragraph("<b>Text shown on the model page:</b>", body))
        if title:
            paras.append(Paragraph(esc(title), body))
        paras += [Paragraph(esc(t).replace("\n", "<br/>"), body) for t in page_text.split("\n\n") if t.strip()]
    f = Frame(L, 0.8 * inch, W - 2 * L, y - 0.95 * inch, showBoundary=0)
    f.addFromList(paras, c)
    c.setFont("Helvetica", 7)
    c.drawString(L, 0.58 * inch, "The raw API response and page HTML are stored alongside this PDF; their SHA-256 hashes above show they are unchanged.")
    c.drawString(L, 0.46 * inch, "The archived copy is held independently by the Internet Archive and carries its own capture timestamp.")
    c.showPage()
    c.save()


def capture_makerworld(db: Session, model: Model, design: dict, page_html: str | None, cover: bytes | None,
                       do_wayback: bool = True) -> LicenseEvidence:
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    d = evidence_dir(model.id)
    raw = json.dumps(design, ensure_ascii=False, indent=1).encode()
    (d / f"{stamp}_makerworld.json").write_bytes(raw)
    html_sha = None
    if page_html:
        hb = page_html.encode()
        (d / f"{stamp}_page.html").write_bytes(hb)
        html_sha = _sha(hb)
    lic_raw = design.get("license")
    code = normalize(lic_raw)
    li = design.get("licenseDescriptionInfo") or {}
    api_url = f"https://makerworld.com/api/v1/design-service/design/{design['id']}"
    wb = wayback(api_url) if do_wayback else None
    ev = {"captured_at": now.isoformat(timespec="seconds"), "source_url": model.source_url, "license_raw": lic_raw,
          "license_code": code, "license_title": li.get("title"), "license_text": li.get("content"),
          "designer": (design.get("designCreator") or {}).get("name"), "design_updated": design.get("updateTime"),
          "wayback_url": wb, "json_sha256": _sha(raw), "html_sha256": html_sha}
    pdf = d / f"{stamp}_license-record.pdf"
    render_pdf(pdf, model, ev, cover)
    row = LicenseEvidence(model_id=model.id, kind="makerworld_snapshot", source_url=model.source_url,
                          license_code=code, license_raw=lic_raw, license_text=(li.get("title") or "") + "\n" + (li.get("content") or ""),
                          json_rel=str((d / f"{stamp}_makerworld.json").relative_to(settings().data_dir)),
                          html_rel=str((d / f"{stamp}_page.html").relative_to(settings().data_dir)) if page_html else None,
                          pdf_rel=str(pdf.relative_to(settings().data_dir)), wayback_url=wb,
                          sha256=_sha(pdf.read_bytes()), captured_at=now)
    db.add(row)
    db.flush()
    return row


def add_uploaded(db: Session, model: Model, filename: str, data: bytes, note: str | None, code: str | None) -> LicenseEvidence:
    now = datetime.now(timezone.utc)
    d = evidence_dir(model.id)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[-120:] or "evidence.pdf"
    p = d / f"{now:%Y%m%dT%H%M%SZ}_upload_{safe}"
    p.write_bytes(data)
    row = LicenseEvidence(model_id=model.id, kind="upload", source_url=model.source_url, license_code=code or model.license_code,
                          license_text=note, pdf_rel=str(p.relative_to(settings().data_dir)), sha256=_sha(data),
                          captured_at=now, note=note)
    db.add(row)
    db.flush()
    return row


def capture_generic(db: Session, model: Model, platform: str, raw: dict, page_html: str | None, cover: bytes | None,
                    license_raw: str | None, license_text: str | None, api_url: str | None, page_url: str | None,
                    designer: str | None = None, design_updated: str | None = None, do_wayback: bool = True) -> LicenseEvidence:
    """Same evidence set as MakerWorld: raw data + page HTML with hashes, a PDF record, an Internet Archive copy."""
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    d = evidence_dir(model.id)
    rawb = json.dumps(raw, ensure_ascii=False, indent=1, default=str).encode()
    jp = d / f"{stamp}_{platform}.json"
    jp.write_bytes(rawb)
    hp, html_sha = None, None
    if page_html:
        hb = page_html.encode()
        hp = d / f"{stamp}_page.html"
        hp.write_bytes(hb)
        html_sha = _sha(hb)
    code = normalize(license_raw, platform)
    wb = None
    if do_wayback:
        wb = wayback(page_url or api_url) if (page_url or api_url) else None
    ev = {"captured_at": now.isoformat(timespec="seconds"), "source_url": model.source_url, "license_raw": license_raw,
          "license_code": code, "license_title": license_raw, "license_text": license_text, "designer": designer,
          "design_updated": design_updated, "wayback_url": wb, "json_sha256": _sha(rawb), "html_sha256": html_sha}
    pdf = d / f"{stamp}_license-record.pdf"
    render_pdf(pdf, model, ev, cover)
    rel = lambda x: str(x.relative_to(settings().data_dir)) if x else None  # noqa: E731
    row = LicenseEvidence(model_id=model.id, kind=f"{platform}_snapshot", source_url=model.source_url, license_code=code,
                          license_raw=(license_raw or "")[:200], license_text=license_text, json_rel=rel(jp), html_rel=rel(hp),
                          pdf_rel=rel(pdf), wayback_url=wb, sha256=_sha(pdf.read_bytes()), captured_at=now)
    db.add(row)
    db.flush()
    return row
