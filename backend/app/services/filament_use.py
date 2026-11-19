"""Filament a print really used, as opposed to what the slicer estimated.

Each finished farm job gets `used_g` and where it came from (`used_source`), best first:
- printer: the printer reported it. Klipper printers (Moonraker) keep a history of every job with the filament
  extruded in mm; the hub matches the farm job to the printer's history entry by its start and end times and
  turns mm into grams with the filament's density and diameter.
- weighed: a spool was weighed (or corrected) in Spoolman. The difference between what the scale says and what the
  prints on that spool since the last weigh-in were thought to use is spread over those prints in proportion.
- progress: a failed or cancelled print, counted as the estimate times how far it got.
- estimate: the slicer's estimate (finished prints with no better source).

Which spools a job used is recorded when it starts (the farm's loaded filament on that printer; the farm's spool
ids are its Spoolman's), with the estimate split over them by material and colour.
"""
import logging
import math
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..kv import setting, set_setting
from ..models import FarmJob, SpoolWeighIn

log = logging.getLogger("hub.filament_use")
DONE = ("completed", "failed", "cancelled")
MATCH_WINDOW = timedelta(minutes=12)
HOSTS_TTL = timedelta(hours=1)
FACTOR_LIMITS = (0.5, 2.0)


def now():
    return datetime.now(timezone.utc)


def _aware(d):
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


DENSITY = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21, "PC": 1.20, "PA": 1.14, "PVA": 1.23,
           "HIPS": 1.04, "PET": 1.38}


def density(material: str | None) -> float:
    """g/cm3 for a material, from its name (PLA when unknown)."""
    m = (material or "PLA").upper()
    for k, v in DENSITY.items():
        if m.startswith(k):
            return v
    return 1.24


def mm_to_g(mm: float, material: str | None, diameter: float = 1.75, dens: float | None = None) -> float:
    area = math.pi * (diameter / 2) ** 2            # mm2
    return mm * area / 1000 * (dens or density(material))


def share(fj: FarmJob) -> float:
    """How much of the job ran: 1 for a finished print, else its progress (or time run over time expected)."""
    if fj.status == "completed":
        return 1.0
    if fj.progress:
        return min(1.0, max(0.0, float(fj.progress) / 100))
    st, en = _aware(fj.started_at), _aware(fj.completed_at)
    if st and en and en > st and fj.est_time_s:
        return min(1.0, (en - st).total_seconds() / fj.est_time_s)
    return 1.0 if fj.status == "failed" else 0.0


def plan_from_metadata(md: dict) -> list | None:
    """The file's filaments: [{type, color, g}] (the farm's parse of the G-code)."""
    out = []
    for f in md.get("filaments") or []:
        g = f.get("used_g")
        if g is None:
            continue
        out.append({"type": (f.get("type") or "").upper() or None,
                    "color": (f.get("color") or f.get("matched_color") or "").lstrip("#").lower() or None,
                    "g": float(g)})
    if not out and md.get("filament_used_g") is not None:
        out = [{"type": None, "color": (md.get("filament_color_hex") or "").lstrip("#").lower() or None,
                "g": float(md["filament_used_g"])}]
    return out or None


# ---------- printers that report use ----------

def klipper_hosts(db: Session, refresh: bool = False) -> dict:
    """{printer name: Moonraker URL}. Found from the farm's printer list (Klipper printers, port 7125); the
    `moonraker_urls` setting adds or overrides entries ("" turns one off)."""
    cache = setting(db, "klipper_hosts_cache") or {}
    at = cache.get("at")
    hosts = cache.get("hosts") or {}
    if refresh or not at or now() - datetime.fromisoformat(at) > HOSTS_TTL:
        try:
            from ..clients import farm
            ps = farm().printers_full()
            ps = ps.get("printers", ps) if isinstance(ps, dict) else (ps or [])
            hosts = {p["name"]: f"http://{p['ip_address']}:7125" for p in ps
                     if (p.get("type") == "klipper" or p.get("plugin") == "klipper") and p.get("ip_address") and p.get("name")}
            set_setting(db, "klipper_hosts_cache", {"at": now().isoformat(), "hosts": hosts})
        except Exception as e:  # noqa: BLE001  (farm down: keep the last list)
            log.info("klipper host discovery failed: %s", e)
    merged = {**hosts, **(setting(db, "moonraker_urls") or {})}
    return {k: v.rstrip("/") for k, v in merged.items() if v}


def moonraker_history(url: str, since: datetime, limit: int = 200) -> list[dict]:
    r = httpx.get(f"{url}/server/history/list", params={"limit": limit, "order": "desc",
                                                        "since": int(since.timestamp())}, timeout=15)
    r.raise_for_status()
    return (r.json().get("result") or {}).get("jobs") or []


def match_history(fj: FarmJob, hist: list[dict]) -> dict | None:
    """The printer's history entry for this job: closest end (else start) time within the window."""
    st, en = _aware(fj.started_at), _aware(fj.completed_at)
    best, gap = None, None
    for h in hist:
        if not h.get("filament_used"):
            continue
        cands = []
        if en and h.get("end_time"):
            cands.append(abs((datetime.fromtimestamp(h["end_time"], timezone.utc) - en).total_seconds()))
        if st and h.get("start_time"):
            cands.append(abs((datetime.fromtimestamp(h["start_time"], timezone.utc) - st).total_seconds()))
        if not cands:
            continue
        d = min(cands)
        if d <= MATCH_WINDOW.total_seconds() and (gap is None or d < gap):
            best, gap = h, d
    return best


def _spool_density(db: Session, fj: FarmJob) -> tuple[float | None, float]:
    """Density and diameter of the (first) Spoolman spool the job used, when known."""
    for s in fj.spools or []:
        if s.get("density"):
            return s["density"], s.get("diameter") or 1.75
    return None, 1.75


def measure(db: Session, fj: FarmJob, hist_cache: dict | None = None, hosts: dict | None = None) -> str | None:
    """Fill used_g for one finished job; answers the source used (None if nothing to go on)."""
    if fj.status not in DONE or fj.used_source in ("printer", "weighed"):
        return fj.used_source
    hosts = hosts if hosts is not None else klipper_hosts(db)
    url = hosts.get(fj.printer_name or "")
    if url and (fj.started_at or fj.completed_at):
        hist_cache = hist_cache if hist_cache is not None else {}
        if url not in hist_cache:
            since = (_aware(fj.started_at) or _aware(fj.completed_at)) - timedelta(days=1)
            try:
                hist_cache[url] = moonraker_history(url, since)
            except Exception as e:  # noqa: BLE001
                log.info("moonraker %s: %s", url, e)
                hist_cache[url] = []
        h = match_history(fj, hist_cache[url])
        if h:
            dens, dia = _spool_density(db, fj)
            fj.used_g = round(mm_to_g(float(h["filament_used"]), fj.material, dia, dens), 2)
            fj.used_source = "printer"
            return "printer"
    if fj.filament_g is None:
        return None
    fj.used_g = round(float(fj.filament_g) * share(fj), 2)
    fj.used_source = "estimate" if fj.status == "completed" else "progress"
    return fj.used_source


def measure_recent(db: Session, days: int = 3) -> dict:
    """Measure finished jobs from the last `days` that have nothing better than an estimate yet."""
    since = now() - timedelta(days=days)
    jobs = list(db.execute(select(FarmJob).where(FarmJob.status.in_(DONE), FarmJob.completed_at >= since)
                           .order_by(FarmJob.completed_at)).scalars())
    hosts = klipper_hosts(db)
    cache: dict = {}
    if hosts and jobs:
        oldest = min((_aware(j.started_at) or _aware(j.completed_at)) for j in jobs) - timedelta(days=1)
        for name, url in hosts.items():
            if any(j.printer_name == name for j in jobs):
                try:
                    cache[url] = moonraker_history(url, oldest, limit=500)
                except Exception as e:  # noqa: BLE001
                    log.info("moonraker %s: %s", url, e)
                    cache[url] = []
    out = {"jobs": len(jobs), "printer": 0, "weighed": 0, "progress": 0, "estimate": 0, "none": 0}
    for j in jobs:
        src = measure(db, j, cache, hosts)
        out[src or "none"] = out.get(src or "none", 0) + 1
    db.flush()
    return out


# ---------- spools a job used ----------

def _dist(a: str | None, b: str | None) -> float:
    try:
        x, y = int(a, 16), int(b, 16)
    except (TypeError, ValueError):
        return 1e9
    return sum((((x >> s) & 255) - ((y >> s) & 255)) ** 2 for s in (16, 8, 0))


def snapshot_spools(db: Session, fj: FarmJob) -> list:
    """Record which linked spools were loaded on the job's printer when it started, with the estimate split."""
    from ..clients import farm
    w = farm()
    ps = w.printers_full()
    ps = ps.get("printers", ps) if isinstance(ps, dict) else (ps or [])
    pid = next((p["id"] for p in ps if p.get("name") == fj.printer_name or p.get("id") == fj.printer_name), None)
    fa = (w.filament_assignments() or {}).get("printers") or {}
    loaded = [a for a in ((fa.get(pid) or {}).get("assignments") or []) if a.get("spool_id")]
    # the farm's spool ids are its Spoolman's (the farm keeps the library in Spoolman)
    spools = [{"farm": a["spool_id"], "sm": a["spool_id"], "type": (a.get("material_type") or "").upper() or None,
               "color": (a.get("spool_color_hex") or a.get("color_hex") or "").lstrip("#").lower() or None, "g": 0.0}
              for a in loaded]
    if not spools:
        fj.spools = []
        return []
    plan = fj.fil_plan or ([{"type": (fj.material or "").upper() or None, "color": None, "g": float(fj.filament_g or 0)}])
    for f in plan:
        pool = [s for s in spools if not f.get("type") or not s["type"] or s["type"] == f["type"]] or spools
        pick = min(pool, key=lambda s: _dist(s["color"], f.get("color")))
        pick["g"] += float(f.get("g") or 0)
    used = [s for s in spools if s["g"] > 0] or spools[:1]
    for s in used:
        s["g"] = round(s["g"], 2)
    fj.spools = used
    return used


# ---------- weigh-ins ----------

def _job_spool_g(fj: FarmJob, sm_id: int) -> float:
    """Grams this job is thought to have taken from one spool (its share of the job's current figure)."""
    parts = fj.spools or []
    total = sum(float(s.get("g") or 0) for s in parts) or 0.0
    mine = sum(float(s.get("g") or 0) for s in parts if s.get("sm") == sm_id)
    if not total or not mine:
        return 0.0
    job_g = fj.used_g if fj.used_g is not None else (float(fj.filament_g or 0) * share(fj))
    return job_g * mine / total


def weigh_in(db: Session, sm_id: int, extra_used_g: float, remaining_g: float) -> SpoolWeighIn:
    """A Spoolman change to a spool (weighed or corrected): extra_used_g is how much more the scale says was used
    than the hub had recorded (negative: less). Spread over the jobs on that spool since its last weigh-in, except
    jobs the printer measured."""
    prev = db.execute(select(SpoolWeighIn).where(SpoolWeighIn.spoolman_id == sm_id)
                      .order_by(SpoolWeighIn.created_at.desc())).scalars().first()
    since = _aware(prev.created_at) if prev else now() - timedelta(days=120)
    jobs = [j for j in db.execute(select(FarmJob).where(FarmJob.status.in_(DONE), FarmJob.completed_at >= since)).scalars()
            if any(s.get("sm") == sm_id for s in (j.spools or []))]
    adjustable = [j for j in jobs if j.used_source != "printer"]
    tracked = sum(_job_spool_g(j, sm_id) for j in jobs)
    adj_g = sum(_job_spool_g(j, sm_id) for j in adjustable)
    row = SpoolWeighIn(spoolman_id=sm_id, remaining_g=round(remaining_g, 2), tracked_g=round(tracked, 2),
                       measured_g=round(tracked + extra_used_g, 2), jobs=[])
    if adj_g < 1:
        row.note = "no prints on this spool to adjust" if not jobs else "only printer-measured prints on this spool"
    elif abs(extra_used_g) > max(50.0, adj_g):
        row.note = "change too large for a weigh-in (spool replaced or corrected); prints left as they were"
    else:
        factor = (adj_g + extra_used_g) / adj_g
        lo, hi = FACTOR_LIMITS
        factor = min(hi, max(lo, factor))
        row.factor = round(factor, 4)
        for j in adjustable:
            g = _job_spool_g(j, sm_id)
            base = j.used_g if j.used_g is not None else float(j.filament_g or 0) * share(j)
            j.used_g = round(base + g * (factor - 1), 2)
            j.used_source = "weighed"
            row.jobs.append(j.id)
    db.add(row)
    db.flush()
    return row


# ---------- summaries ----------

def model_ratios(db: Session, days: int = 180) -> dict:
    """{model_id: {"ratio": used / estimated, "prints": n}} over finished prints the printer or a scale measured."""
    since = now() - timedelta(days=days)
    acc: dict = {}
    for j in db.execute(select(FarmJob).where(FarmJob.status == "completed", FarmJob.completed_at >= since,
                                              FarmJob.used_source.in_(("printer", "weighed")),
                                              FarmJob.model_id.isnot(None))).scalars():
        if not j.filament_g or not j.used_g:
            continue
        a = acc.setdefault(j.model_id, [0.0, 0.0, 0])
        a[0] += j.used_g
        a[1] += j.filament_g
        a[2] += 1
    return {m: {"ratio": round(u / e, 4), "prints": n} for m, (u, e, n) in acc.items() if e}


def summary(db: Session, days: int = 30) -> dict:
    since = now() - timedelta(days=days)
    rows = list(db.execute(select(FarmJob).where(FarmJob.status.in_(DONE), FarmJob.completed_at >= since)).scalars())
    by = {}
    for j in rows:
        k = j.used_source or "none"
        b = by.setdefault(k, {"jobs": 0, "used_g": 0.0, "estimate_g": 0.0})
        b["jobs"] += 1
        b["used_g"] += j.used_g or 0
        b["estimate_g"] += (j.filament_g or 0) * share(j)
    meas = [j for j in rows if j.used_source in ("printer", "weighed") and j.filament_g]
    u, e = sum(j.used_g or 0 for j in meas), sum((j.filament_g or 0) * share(j) for j in meas)
    weighs = list(db.execute(select(SpoolWeighIn).where(SpoolWeighIn.created_at >= since)
                             .order_by(SpoolWeighIn.created_at.desc())).scalars())
    return {"days": days, "by_source": {k: {kk: round(vv, 1) if isinstance(vv, float) else vv for kk, vv in v.items()}
                                        for k, v in by.items()},
            "measured_vs_estimate": round(u / e, 3) if e else None, "measured_jobs": len(meas),
            "klipper_printers": sorted(klipper_hosts(db).keys()),
            "weigh_ins": [{"spool": w.spoolman_id, "at": w.created_at.isoformat(), "tracked_g": w.tracked_g,
                           "measured_g": w.measured_g, "factor": w.factor, "jobs": len(w.jobs or []), "note": w.note}
                          for w in weighs[:20]]}
