"""The alerts inbox (services/alert_inbox.py) and the actions its guided fixes use. The same routes under /api
(web) and /api/mobile/v1 (apps): an alert opened from a notification, an email or the dashboard is fixed the same
way everywhere."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, need
from ..db import get_db
from ..jobs import event
from ..kv import set_setting, setting
from ..models import Alert, FarmJob, PlateCheck, User
from ..services import alert_inbox as inbox

router = APIRouter(tags=["alerts"])
PREFIXES = ("/api", "/api/mobile/v1")
MATERIALS = {   # nozzle range the printer is told when a slot is corrected by hand
    "PLA": (190, 230), "PETG": (220, 260), "ABS": (240, 270), "ASA": (240, 270), "TPU": (200, 230),
    "PA": (260, 290), "PC": (260, 290), "PVA": (190, 220), "HIPS": (220, 250),
}


class FixedIn(BaseModel):
    how: str = Field(default="", max_length=300)


class ChoicesIn(BaseModel):
    on: dict[str, bool]


class MoveIn(BaseModel):
    printer_id: int


class SlotIn(BaseModel):
    type: str = Field(min_length=2, max_length=20)
    color: str = Field(pattern="^#?[0-9A-Fa-f]{6}$")


class WantIn(BaseModel):
    material: str = Field(min_length=2, max_length=40)
    color: str = Field(default="", max_length=9)
    grams: int | None = Field(default=None, ge=1, le=100000)


def _actor(p: Principal) -> str:
    return p.get("username") or p.get("email") or "someone"


def _user(db: Session, p: Principal) -> User | None:
    return db.get(User, p.get("user_id")) if p.get("user_id") else None


def _alert(db: Session, aid: int) -> Alert:
    a = db.get(Alert, aid)
    if not a:
        raise HTTPException(404, "no such alert")
    return a


def _context(db: Session, a: Alert) -> dict:
    """What a guided fix needs to start: the job or the printer the alert is about, as things stand now."""
    from ..services import farm
    s = a.subject or {}
    kind = inbox.FIXES.get(a.kind)
    if kind == "filament" and s.get("job"):
        fj = db.execute(select(FarmJob).where(FarmJob.external_id == str(s["job"]))).scalar()
        return {"kind": kind, "job": str(s["job"]), "job_name": fj.name if fj else None,
                "job_status": fj.status if fj else "gone", "waiting_reason": fj.waiting_reason if fj else None,
                "printer": fj.printer_name if fj else None, "material": fj.material if fj else None,
                "model_id": fj.model_id if fj else None}
    if kind == "ready" and s.get("printer"):
        name = s["printer"]
        check = db.execute(select(PlateCheck).where(PlateCheck.printer_name == name, PlateCheck.state == "waiting")
                           .order_by(PlateCheck.id.desc())).scalars().first()
        jobs = db.execute(select(FarmJob).where(FarmJob.status.in_(("queued", "assigned")),
                                                FarmJob.printer_name == name)).scalars().all()
        return {"kind": kind, "printer": name, "check": farm.check_dict(check, {}) if check else None,
                "waiting": [farm.job_dict(j, {}) for j in jobs]}
    return {"kind": None}


def _list(state: str = "open", limit: int = 100, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    inbox.expire(db)
    q = select(Alert).order_by(Alert.updated_at.desc()).limit(max(1, min(limit, 300)))
    if state == "open":
        q = q.where(Alert.state == "open")
    elif state == "closed":
        q = q.where(Alert.state != "open")
    rows = [inbox.view(a) for a in db.execute(q).scalars()]
    out = {"alerts": rows, **inbox.counts(db)}
    db.commit()
    return out


def _counts(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    return inbox.counts(db)


def _get(aid: int, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    a = _alert(db, aid)
    return {**inbox.view(a, full=True), "context": _context(db, a)}


def _dismiss(aid: int, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    a = _alert(db, aid)
    inbox.close(db, a, "dismissed", _actor(p))
    event(db, f"alert:{a.id}", f"Alert dismissed: {a.title}", actor=_actor(p))
    db.commit()
    return inbox.view(a, full=True)


def _fixed(aid: int, body: FixedIn, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    a = _alert(db, aid)
    inbox.close(db, a, "fixed", _actor(p), body.how)
    event(db, f"alert:{a.id}", f"Alert fixed: {a.title}" + (f" ({body.how})" if body.how else ""), actor=_actor(p))
    db.commit()
    return inbox.view(a, full=True)


def _choices(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    return inbox.choices(_user(db, p))


def _set_choices(body: ChoicesIn, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    u = _user(db, p)
    if u is None:
        raise HTTPException(400, "alert choices belong to a person: use a key made by a person, or sign in")
    inbox.set_choices(u, body.on)
    db.commit()
    return inbox.choices(u)


# ---------------------------------------------------------------- actions the guided fixes use

def _why(job_id: str, p: Principal = Depends(need("view"))):
    """Why a queued job isn't starting, printer by printer (offline, busy, plate not cleared, filament)."""
    from ..services.farm import _client
    try:
        return _client().preflight(int(job_id))
    except (ValueError, TypeError):
        raise HTTPException(404, "no such job") from None


def _move(job_id: str, body: MoveIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """Send a waiting job to another printer (one the person can see)."""
    from ..services import printer_access
    from ..services.farm import _client
    c = _client()
    names = {pr.get("id"): pr.get("name") for pr in (c.printers() or {}).get("printers") or []}
    name = names.get(body.printer_id)
    if not name or not printer_access.may_use(db, p, name):
        raise HTTPException(404, "no such printer")
    c.move_job(int(job_id), body.printer_id)
    fj = db.execute(select(FarmJob).where(FarmJob.external_id == str(job_id))).scalar()
    if fj:
        fj.printer_name = name
    event(db, f"farmjob:{fj.id if fj else job_id}", f"{fj.name if fj else 'Job ' + job_id} moved to {name}",
          actor=_actor(p))
    db.commit()
    return {"ok": True, "printer": name}


def _slot(name: str, gid: int, body: SlotIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    """Correct what the farm thinks is loaded in an AMS slot (Bambu printers): the printer is told the material and
    colour, as if it had been set on its screen."""
    from ..services import printer_access
    from ..services.farm import _client
    if not printer_access.may_use(db, p, name):
        raise HTTPException(404, "no such printer")
    if gid >= 254:
        raise HTTPException(400, "set the external spool's filament on the printer itself")
    ams, tray = (gid, 0) if gid >= 128 else (gid // 4, gid % 4)
    t = body.type.strip().upper()
    lo, hi = MATERIALS.get(t.split()[0], (200, 240))
    _client().configure_slot(name, ams, tray, t, body.color.lstrip("#").upper(), lo, hi)
    event(db, "farm", f"{name}: slot {gid} set to {t} #{body.color.lstrip('#').upper()}", actor=_actor(p))
    db.commit()
    return {"ok": True}


def _want(aid: int, body: WantIn, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    """Put filament on the shopping list for a job that needs it; the alert stays open until the job can print."""
    a = _alert(db, aid)
    wanted = list(setting(db, "filament_wanted") or [])
    item = {"material": body.material.strip().upper(), "color": body.color.strip().upper(), "grams": body.grams,
            "alert_id": a.id, "for": a.title, "by": _actor(p), "at": inbox.now().isoformat()}
    wanted = [w for w in wanted if not (w.get("alert_id") == a.id and w.get("material") == item["material"]
                                        and w.get("color") == item["color"])] + [item]
    set_setting(db, "filament_wanted", wanted[-100:])
    a.subject = {**(a.subject or {}), "ordered": [*(a.subject or {}).get("ordered", []), item["material"] + " " + item["color"]]}
    event(db, f"alert:{a.id}", f"{item['material']} {item['color']} added to the shopping list", actor=_actor(p))
    db.commit()
    return {"ok": True, "wanted": wanted[-100:]}


def _wanted(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    return {"wanted": list(setting(db, "filament_wanted") or [])}


def _unwant(index: int, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    wanted = list(setting(db, "filament_wanted") or [])
    if not 0 <= index < len(wanted):
        raise HTTPException(404, "no such item")
    wanted.pop(index)
    set_setting(db, "filament_wanted", wanted)
    db.commit()
    return {"wanted": wanted}


for _pre in PREFIXES:
    router.add_api_route(_pre + "/alerts", _list, methods=["GET"])
    router.add_api_route(_pre + "/alerts/counts", _counts, methods=["GET"])
    router.add_api_route(_pre + "/alerts/choices", _choices, methods=["GET"])
    router.add_api_route(_pre + "/alerts/choices", _set_choices, methods=["PUT"])
    router.add_api_route(_pre + "/alerts/filament-wanted", _wanted, methods=["GET"])
    router.add_api_route(_pre + "/alerts/filament-wanted/{index}", _unwant, methods=["DELETE"])
    router.add_api_route(_pre + "/alerts/{aid}", _get, methods=["GET"])
    router.add_api_route(_pre + "/alerts/{aid}/dismiss", _dismiss, methods=["POST"])
    router.add_api_route(_pre + "/alerts/{aid}/fixed", _fixed, methods=["POST"])
    router.add_api_route(_pre + "/alerts/{aid}/want-filament", _want, methods=["POST"])
    router.add_api_route(_pre + "/farm/jobs/{job_id}/why", _why, methods=["GET"])
    router.add_api_route(_pre + "/farm/jobs/{job_id}/move", _move, methods=["POST"])
    router.add_api_route(_pre + "/farm/printers/{name}/slots/{gid}/filament", _slot, methods=["POST"])
