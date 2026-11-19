"""One call for the OpenPrintHQ add-on for OrcaSlicer.

The add-on's farm tab shows the hub's farm features: plate checks (with the camera photo), staggered start, the
timeline and order due dates, maintenance, real filament use, report totals and quotes waiting. It polls this endpoint with a team API key; actions use the regular
endpoints (plate checks, maintenance, orders).
"""
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal, need
from ..db import get_db
from ..models import PlateCheck, Quote, User

router = APIRouter()
PLUGIN_API = 1


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw), None
    except Exception as e:  # noqa: BLE001  (one part failing must not hide the rest)
        return None, (getattr(e, "message", None) or str(e))[:200]


@router.get("/api/plugin/farm")
def plugin_farm(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    from ..services import farm, filament_use, maintenance, reports, schedule
    from ..kv import setting
    out = {"api": PLUGIN_API, "team": p.get("team_name"), "errors": {},
           "can_act": "farm" in (p.get("perms") or [])}
    ov, err = _safe(farm.overview, db, live_printers=False)
    if ov:
        out["plate_checks_on"] = ov["plate_checks_on"]
        out["checks"] = ov["checks"]
        out["recent_checks"] = ov["recent_checks"][:8]
        out["reasons"] = ov["reasons"]
        out["history"] = [{k: j.get(k) for k in ("id", "external_id", "name", "status", "printer", "material", "filament_g",
                                                  "used_g", "used_source", "completed_at")} for j in ov["history"][:12]]
    else:
        out["errors"]["farm"] = err
    st = farm.stagger(db)
    out["stagger"] = {"minutes": round(st / timedelta(minutes=1), 1),
                      "waiting": [{"check": c.id, "printer": c.printer_name, "due": c.ready_due.isoformat()}
                                  for c in db.execute(select(PlateCheck).where(PlateCheck.ready_due.is_not(None),
                                                                               PlateCheck.ready_set.is_(False))
                                                      .order_by(PlateCheck.ready_due)).scalars()]}
    tl, err = _safe(schedule.build, db)
    if tl:
        out["timeline"] = tl
    else:
        out["errors"]["timeline"] = err
    mt, err = _safe(maintenance.overview, db)
    if mt:
        out["maintenance"] = {"open": mt["open"], "due": [x for x in mt["plans"] if x.get("due")], "down": mt["down"],
                              "linked": mt["linked"]}
    else:
        out["errors"]["maintenance"] = err
    fu, err = _safe(filament_use.summary, db, 30)
    if fu:
        fu["weigh_ins"] = fu["weigh_ins"][:5]
        out["filament"] = fu
    else:
        out["errors"]["filament"] = err
    rp, err = _safe(reports.farm, db, 30)
    if rp:
        out["report"] = rp["totals"]
    else:
        out["errors"]["report"] = err
    out["quotes_waiting"] = db.scalar(select(func.count()).select_from(Quote).where(Quote.status == "ordered")) or 0
    look = None
    if p.get("user_id"):
        u = db.get(User, p["user_id"])
        prefs = (u.prefs or {}) if u else {}
        if prefs.get("skin"):
            look = {"skin": prefs["skin"], "mode": prefs.get("mode") or "system"}
    out["look"] = look
    out["hub_url"] = (setting(db, "public_url") or None)
    db.rollback()       # read-only: nothing the summaries touched is kept
    return out
