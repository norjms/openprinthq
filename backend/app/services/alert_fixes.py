"""Guided fixes the hub describes, so the web dashboard and the apps show the same ones without each knowing every
kind of alert (docs/alerts.md). For an alert of one of these kinds, `describe` answers what the alert is about as
things stand now (a summary, pictures, the printer's camera) and the ways to fix it. Each way has a button and
may ask for a few values first (a reason, a printer, notes). `run` carries one out; most ways close the alert as
fixed, a few (pause the print, take the printer out of service) leave it open for the next step.

Waiting on filament and a printer that isn't ready have richer fixes of their own in the clients
(FIXES "filament" and "ready" in alert_inbox.py).
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..jobs import event
from ..models import Alert, FarmJob, MaintTask, Order, PlateCheck, Quote
from . import alert_inbox as inbox

KINDS = ("print_done", "print_failed", "failure_detected", "alarm", "printer_offline", "maintenance_due",
         "order_problem", "order_late", "quote_ordered")


class FixError(Exception):
    """The fix can't be done (shown to the person as is)."""


def _path(pid: str, title: str, detail: str, icon: str, button: str, *, closes: bool = True,
          confirm: str | None = None, fields: list | None = None, open: dict | None = None,
          off: str | None = None) -> dict:
    """One way to fix it. `icon` is a name both clients know (check, report, play, pause, stop, refresh, move,
    wrench, mute, clear, calendar, flag, open, thumbs_up, thumbs_down). `open` makes it a link: {"web": path,
    "app": "printer:<name>" | "farm:<tab>" | "store:<tab>"}. `off` says why it can't be used right now."""
    return {"id": pid, "title": title, "detail": detail, "icon": icon, "button": button, "closes": closes,
            "confirm": confirm, "fields": fields or [], "open": open, "off": off}


def _choice(name, label, options, value=None):
    return {"name": name, "label": label, "type": "choice", "options": [[str(k), v] for k, v in options],
            "value": str(value if value is not None else options[0][0]) if options else None}


def _text(name, label, required=False, hint=""):
    return {"name": name, "label": label, "type": "text", "required": required, "hint": hint, "value": ""}


def _switch(name, label, value=False):
    return {"name": name, "label": label, "type": "switch", "value": bool(value)}


def _client():
    from .farm import _client as c
    return c()


def _live(name: str | None) -> dict:
    """The printer as the farm sees it now ({} when the farm can't be reached or doesn't know it)."""
    if not name:
        return {}
    try:
        return _client().printer(name) or {}
    except Exception:  # noqa: BLE001
        return {}


def _state(p: dict) -> str:
    if not p:
        return "unknown"
    if not p.get("online"):
        return "offline"
    if p.get("state") in ("pause", "paused"):
        return "paused"
    return "printing" if p.get("busy") else ("ready" if p.get("ready") else (p.get("state") or "idle"))


def _running(db: Session, name: str | None) -> FarmJob | None:
    if not name:
        return None
    return db.execute(select(FarmJob).where(FarmJob.printer_name == name,
                                            FarmJob.status.in_(("printing", "paused", "resuming")))
                      .order_by(FarmJob.id.desc())).scalars().first()


def _waiting(db: Session, name: str) -> list[FarmJob]:
    return list(db.execute(select(FarmJob).where(FarmJob.printer_name == name,
                                                 FarmJob.status.in_(("queued", "assigned", "waiting_filament"))))
                .scalars())


def _printer_open(name: str) -> dict:
    return {"web": "/farm", "app": f"printer:{name}"}


def _command(name: str, command: str):
    try:
        _client().command(name, command)
    except Exception as e:  # noqa: BLE001
        raise FixError(f"{name} didn't take the {command} command: {getattr(e, 'message', None) or e}"[:300]) from None


def _stop(db: Session, name: str, actor: str) -> str:
    fj = _running(db, name)
    if not fj:
        raise FixError(f"Nothing is printing on {name}.")
    try:
        _client().cancel_job(int(fj.external_id))
    except Exception as e:  # noqa: BLE001
        raise FixError(f"The farm didn't stop it: {getattr(e, 'message', None) or e}"[:300]) from None
    event(db, "farm", f"{name}: {fj.name or 'print'} stopped from its alert", actor=actor)
    return f"stopped {fj.name or 'the print'}"


# ---------------------------------------------------------------- the plate after a print

def _check(db: Session, a: Alert) -> PlateCheck | None:
    cid = (a.subject or {}).get("check")
    return db.get(PlateCheck, int(cid)) if cid else None


def _plate(db: Session, a: Alert) -> dict:
    from . import farm
    c = _check(db, a)
    s = a.subject or {}
    name = c.printer_name if c else s.get("printer")
    d = farm.check_dict(c, {}) if c else {}
    out = {"summary": [["Printer", name or ""], ["Print", (c.job_name if c else s.get("job_name")) or ""],
                       ["How it ended", {"completed": "Finished", "failed": "Failed", "cancelled": "Stopped"}
                        .get(c.job_outcome if c else "", "")]],
           "photos": [{"label": "When it ended", "url": d["photo"], "app": f"/plate-checks/{c.id}/photo"}]
           if d.get("photo") else [],
           "camera": name, "paths": []}
    if not c or c.state != "waiting":
        out["note"] = (f"Its plate check is already {c.state}." if c else "There is no plate check for this print.") + \
            " Nothing is left to do here."
        return out
    reasons = list(farm.FAIL_REASONS.items())
    why = [_choice("reason", "What went wrong", reasons, "other" if a.kind == "print_failed" else "spaghetti"),
           _text("note", "Note", hint="Optional")]
    if a.kind == "print_done":
        out["paths"] = [
            _path("pass", "The print is good and the plate is clear",
                  "Pass the plate check: the printer is set ready and the next job can start.", "check",
                  "Pass the plate check"),
            _path("fail", "Something is wrong with the print",
                  "Fail the plate check with a reason, and print it again if you like. Clear the plate first.",
                  "report", "Fail the plate check", fields=why + [_switch("reprint", "Print it again", True)]),
        ]
    else:
        out["paths"] = [
            _path("reprint", "Clear the plate and print it again",
                  "Records what went wrong and puts the job back in the queue. The printer is set ready.",
                  "refresh", "Print it again", fields=why),
            _path("drop", "Clear the plate, don't print it again",
                  "Records what went wrong; the printer is set ready for the next job.", "clear", "Done", fields=why),
            _path("look", "Look at the printer first", "Open the printer to see its camera and state.", "open",
                  "Open the printer", closes=False, open=_printer_open(name)),
        ]
    return out


def _plate_run(db: Session, a: Alert, pid: str, v: dict, actor: str) -> tuple[str, str | None]:
    from . import farm
    c = _check(db, a)
    if not c:
        raise FixError("There is no plate check for this print.")
    try:
        if pid == "pass":
            farm.pass_check(db, c, actor, set_ready=True)
            return f"{c.printer_name} is ready", "plate check passed"
        reprint = pid == "reprint" or (pid == "fail" and bool(v.get("reprint")))
        farm.fail_check(db, c, actor, str(v.get("reason") or "other"), note=(v.get("note") or "").strip() or None,
                        reprint=reprint, set_ready=True)
    except ValueError as e:
        raise FixError(str(e)) from None
    label = farm.FAIL_REASONS.get(c.reason, c.reason)
    return ("Queued again" if reprint else "Plate check failed"), f"{label}{', printing it again' if reprint else ''}"


# ---------------------------------------------------------------- the camera saw a failure

def _vision(db: Session, a: Alert) -> dict:
    s = a.subject or {}
    name = s.get("printer")
    p = _live(name)
    fj = _running(db, name)
    st = _state(p)
    conf = s.get("confidence")
    return {"summary": [["Printer", name or ""], ["Print", (fj.name if fj else "") or ""],
                        ["How sure", f"{round(float(conf) * 100)}%" if isinstance(conf, (int, float)) else ""],
                        ["Now", st]],
            "photos": [], "camera": name,
            "paths": [
                _path("fine", "It looks fine: keep printing",
                      "A false alarm. If the print is paused it carries on.", "thumbs_up", "Keep printing"),
                _path("pause", "Pause it while I look", "Pause the print, look at the camera, then come back.",
                      "pause", "Pause the print", closes=False,
                      off=None if st == "printing" else "It isn't printing right now."),
                _path("stop", "Stop the print", "Cancel it. Its plate check then asks whether to print it again.",
                      "stop", "Stop the print", confirm=f"Stop {fj.name if fj else 'the print'} on {name}? It can't be resumed.",
                      off=None if fj else "Nothing is printing on it."),
            ]}


def _vision_run(db: Session, a: Alert, pid: str, v: dict, actor: str) -> tuple[str, str | None]:
    name = (a.subject or {}).get("printer")
    if pid == "fine":
        if _state(_live(name)) == "paused":
            _command(name, "resume")
            return "Resumed", "a false alarm; resumed"
        return "Noted", "a false alarm"
    if pid == "pause":
        _command(name, "pause")
        return "Paused. Look at the camera, then keep printing or stop it.", None
    return "Stopped", _stop(db, name, actor)


# ---------------------------------------------------------------- printer alarm

def _alarm_row(name: str, code: str | None) -> dict | None:
    from .printer_status import norm
    try:
        rows = _client().alarms(state="active", printer=name) or []
    except Exception:  # noqa: BLE001
        return None
    for r in rows if isinstance(rows, list) else rows.get("alarms") or []:
        if not code or norm(r.get("code")) == norm(code):
            return r
    return None


def _alarm(db: Session, a: Alert) -> dict:
    s = a.subject or {}
    name, code = s.get("printer"), s.get("code")
    p = _live(name)
    st = _state(p)
    fj = _running(db, name)
    row = _alarm_row(name, code)
    gone = None if row else "The printer no longer reports it."
    return {"summary": [["Printer", name or ""], ["Code", code or ""], ["What it means", s.get("message") or a.message or ""],
                        ["Now", st], ["Print", (fj.name if fj else "") or ""]],
            "photos": [], "camera": name,
            "paths": [
                _path("resume", "I fixed it: resume the print", "Clear the alarm and carry on printing.", "play",
                      "Resume", off=None if st == "paused" else "The print isn't paused."),
                _path("clear", "Clear the alarm",
                      "Take it off the alarm list. If the printer reports it again, it comes back.", "clear",
                      "Clear it"),
                _path("mute", f"Stop alerts for this code on {name}",
                      f"No alarm or notification for {code or 'this code'} on {name} again. Undo it under Farm > Alarms.",
                      "mute", "Stop these alerts", confirm=f"Stop alerts for {code} on {name}?", off=gone),
                _path("stop", "Stop the print", "Cancel it. Its plate check then asks whether to print it again.",
                      "stop", "Stop the print", confirm=f"Stop {fj.name if fj else 'the print'} on {name}? It can't be resumed.",
                      off=None if fj else "Nothing is printing on it."),
                _path("task", "Open its maintenance task", "The alarm opened a maintenance task for the printer.",
                      "wrench", "Open maintenance", closes=False, open={"web": "/farm", "app": "farm:maintenance"}),
            ]}


def _alarm_run(db: Session, a: Alert, pid: str, v: dict, actor: str) -> tuple[str, str | None]:
    s = a.subject or {}
    name, code = s.get("printer"), s.get("code")
    row = _alarm_row(name, code)
    c = _client()

    def clear():
        try:
            if row and row.get("id") is not None:
                c.dismiss_alarm(int(row["id"]), actor)
            elif name:
                c.ack_printer_alarms(name, actor)
        except Exception:  # noqa: BLE001
            pass
        from . import printer_status
        printer_status.alarm_cleared(db, name, code)

    if pid == "resume":
        _command(name, "resume")
        clear()
        return "Resumed", "fixed on the printer; resumed"
    if pid == "clear":
        clear()
        return "Alarm cleared", "alarm cleared"
    if pid == "mute":
        if not row:
            raise FixError("The printer no longer reports it, so there is nothing to turn off.")
        try:
            c.suppress_alarm(int(row["id"]), actor)
        except Exception as e:  # noqa: BLE001
            raise FixError(f"The farm didn't turn it off: {getattr(e, 'message', None) or e}"[:300]) from None
        event(db, "farm", f"Alerts for {code} on {name} turned off from its alert", actor=actor)
        return "Turned off", f"alerts for {code} on {name} turned off"
    return "Stopped", _stop(db, name, actor)


# ---------------------------------------------------------------- printer went offline mid-print

def _offline(db: Session, a: Alert) -> dict:
    s = a.subject or {}
    name = s.get("printer")
    p = _live(name)
    st = _state(p)
    waiting = _waiting(db, name) if name else []
    others = []
    try:
        others = [(x.get("id"), x.get("name")) for x in (_client().printers() or {}).get("printers") or []
                  if x.get("online") and x.get("name") != name]
    except Exception:  # noqa: BLE001
        pass
    fj = _running(db, name)
    return {"summary": [["Printer", name or ""], ["Now", st], ["Print", (fj.name if fj else s.get("job_name")) or ""],
                        ["Jobs waiting for it", str(len(waiting))]],
            "photos": [], "camera": None,
            "paths": [
                _path("back", "Check again", "If it is back online, this alert closes. If not, check its power and "
                      "its network cable or Wi-Fi.", "refresh", "Check now", closes=False),
                _path("down", "Take it out of service", "No new jobs go to it until it is back in service. "
                      "A print it is still running carries on.", "flag", "Out of service",
                      fields=[_text("reason", "Why", hint="Optional")]),
                _path("move", "Send its waiting jobs to another printer",
                      "Each job's filament is checked again on the new printer.", "move", "Move the jobs",
                      fields=[_choice("printer", "To", others)] if others else [],
                      off=None if waiting and others else ("No jobs are waiting for it." if not waiting
                                                           else "No other printer is online.")),
            ]}


def _offline_run(db: Session, a: Alert, pid: str, v: dict, actor: str) -> tuple[str, str | None]:
    name = (a.subject or {}).get("printer")
    if pid == "back":
        if _live(name).get("online"):
            return f"{name} is back online", "back online"
        raise FixError(f"{name} is still offline. Check its power and its network cable or Wi-Fi.")
    if pid == "down":
        reason = (v.get("reason") or "").strip() or f"Offline; taken out of service from its alert by {actor}"
        try:
            _client().set_down(name, True, reason)
        except Exception as e:  # noqa: BLE001
            raise FixError(f"The farm didn't take it out of service: {getattr(e, 'message', None) or e}"[:300]) from None
        return "Out of service", "taken out of service"
    pid_to = int(v.get("printer") or 0)
    names = {x.get("id"): x.get("name") for x in (_client().printers() or {}).get("printers") or []}
    to = names.get(pid_to)
    if not to:
        raise FixError("Pick a printer.")
    moved = 0
    for fj in _waiting(db, name):
        _client().move_job(int(fj.external_id), pid_to)
        fj.printer_name = to
        moved += 1
    return f"Moved {moved} job{'s' if moved != 1 else ''} to {to}", f"waiting jobs moved to {to}"


# ---------------------------------------------------------------- maintenance due

def _task(db: Session, a: Alert) -> MaintTask | None:
    tid = (a.subject or {}).get("task")
    return db.get(MaintTask, int(tid)) if tid else None


def _maint(db: Session, a: Alert) -> dict:
    t = _task(db, a)
    s = a.subject or {}
    name = t.printer_name if t else s.get("printer")
    out = {"summary": [["Printer", name or ""], ["Task", t.title if t else ""], ["Why", a.message or ""],
                       ["Parts", ", ".join(f"{p.get('qty', 1)} x {p.get('name') or p.get('part_id')}"
                                           for p in (t.parts or [])) if t else ""],
                       ["Notes", (t.notes or "") if t else ""]],
           "photos": [], "camera": None, "paths": []}
    if not t or t.state != "open":
        out["note"] = (f"This task is already {t.state}." if t else "The task is gone.") + " Nothing is left to do here."
        return out
    down = bool(s.get("down"))
    out["paths"] = [
        _path("done", "Done", "Mark it done. Parts it uses come out of stock." +
              (f" {name} goes back in service." if down else ""), "check", "Mark it done",
              fields=[_text("notes", "Notes", hint="Optional")]),
        _path("skip", "Not needed this time", "Close the task without doing it; the plan counts from now.", "clear",
              "Skip it"),
        _path("down", f"Take {name} out of service while I do it", "No new jobs go to it until the task is done.",
              "flag", "Out of service", closes=False, off="It is already out of service." if down else None),
    ]
    return out


def _maint_run(db: Session, a: Alert, pid: str, v: dict, actor: str) -> tuple[str, str | None]:
    from . import maintenance
    t = _task(db, a)
    if not t or t.state != "open":
        raise FixError("This task is already closed.")
    s = a.subject or {}
    if pid == "down":
        try:
            _client().set_down(t.printer_name, True, f"Maintenance: {t.title}")
        except Exception as e:  # noqa: BLE001
            raise FixError(f"The farm didn't take it out of service: {getattr(e, 'message', None) or e}"[:300]) from None
        a.subject = {**s, "down": True}
        return f"{t.printer_name} is out of service. Mark the task done when you finish.", None
    if pid == "done":
        try:
            maintenance.complete(db, t, actor, notes=(v.get("notes") or "").strip() or None)
        except ValueError as e:
            raise FixError(str(e)) from None
        if s.get("down"):
            try:
                _client().set_down(t.printer_name, False)
            except Exception:  # noqa: BLE001
                pass
        return "Done", "maintenance done"
    t.state, t.done_at, t.done_by = "dismissed", maintenance.now(), actor
    maintenance._release(db, t)
    return "Skipped", "not needed this time"


# ---------------------------------------------------------------- orders

def _order(db: Session, a: Alert) -> Order | None:
    oid = (a.subject or {}).get("order")
    return db.get(Order, int(oid)) if oid else None


def _route(db: Session, a: Alert) -> dict:
    o = _order(db, a)
    return {"summary": [["Order", f"#{o.number}" if o else ""], ["Customer", (o.customer or "") if o else ""],
                        ["What went wrong", a.message or ""]],
            "photos": [], "camera": None,
            "paths": [
                _path("retry", "Try again", "Send the order to the farm again, for example after adding a missing "
                      "product or print file.", "refresh", "Send it again", off=None if o else "The order is gone."),
                _path("open", "Open the order", "See its lines and what each one needs.", "open", "Open the order",
                      closes=False, open={"web": "/orders", "app": "store:orders"}),
            ]}


def _route_run(db: Session, a: Alert, pid: str, v: dict, actor: str) -> tuple[str, str | None]:
    from . import farm
    o = _order(db, a)
    if not o:
        raise FixError("The order is gone.")
    out = farm.route_order(db, o.id, force=True)
    if out.get("skipped"):
        raise FixError(f"Order #{o.number} isn't paid, so it can't go to the farm yet.")
    if out.get("problems"):
        raise FixError("Still not sent: " + "; ".join(out["problems"])[:500])
    return f"Order #{o.number} sent to the farm", "sent to the farm"


def _late(db: Session, a: Alert) -> dict:
    o = _order(db, a)
    due = o.due_at.strftime("%a %b %d") if o and o.due_at else ""
    return {"summary": [["Order", f"#{o.number}" if o else ""], ["Due", due], ["Why", a.message or ""]],
            "photos": [], "camera": None,
            "paths": [
                _path("sooner", "Print it sooner", "Raise the priority of its jobs that haven't started.", "flag",
                      "Raise the priority", fields=[_choice("priority", "Priority", [(100, "Urgent"), (75, "High")])],
                      off=None if o else "The order is gone."),
                _path("later", "Move the due date", "Give it more time; the hub warns again if it still won't make it.",
                      "calendar", "Move the date",
                      fields=[_choice("days", "New date", [(1, "One day later"), (2, "Two days later"),
                                                           (3, "Three days later"), (7, "A week later")])],
                      off=None if o else "The order is gone."),
                _path("open", "Open the farm timeline", "See what prints when, on every printer.", "open",
                      "Open the timeline", closes=False, open={"web": "/farm", "app": "farm:queue"}),
            ]}


def _late_run(db: Session, a: Alert, pid: str, v: dict, actor: str) -> tuple[str, str | None]:
    from . import schedule
    o = _order(db, a)
    if not o:
        raise FixError("The order is gone.")
    if pid == "sooner":
        pr = int(v.get("priority") or 100)
        if pr not in (75, 100):
            raise FixError("Pick a priority.")
        out = schedule.prioritise(db, o.id, pr)
        if out.get("errors") and not out.get("raised"):
            raise FixError("; ".join(out["errors"])[:300])
        label = "urgent" if pr == 100 else "high"
        return f"Order #{o.number} is {label} priority", f"priority raised to {label}"
    days = int(v.get("days") or 1)
    if days not in (1, 2, 3, 7):
        raise FixError("Pick a new date.")
    o.due_at = (o.due_at or inbox.now()) + timedelta(days=days)
    o.late_alerted_at = None
    when = o.due_at.strftime("%a %b %d")
    event(db, f"order:{o.id}", f"Due date moved to {when} from its alert", actor=actor)
    return f"Due {when}", f"due date moved to {when}"


# ---------------------------------------------------------------- custom print to review

def _quote(db: Session, a: Alert) -> Quote | None:
    qid = (a.subject or {}).get("quote")
    return db.get(Quote, int(qid)) if qid else None


def _review(db: Session, a: Alert) -> dict:
    q = _quote(db, a)
    out = {"summary": [["File", q.filename if q else ""], ["How many", str(q.quantity) if q else ""],
                       ["Material", ((q.breakdown or {}).get("material") or q.material_key or "") if q else ""],
                       ["Colour", (q.colour or "") if q else ""],
                       ["Filament", f"{round(q.grams)} g each" if q and q.grams else ""]],
           "photos": [{"label": "Preview", "url": f"/api/quotes/{q.id}/preview/{q.previews[0]}", "app": None}]
           if q and q.previews else [], "camera": None, "paths": []}
    if not q or q.status != "ordered":
        out["note"] = (f"This quote is already {q.status}." if q else "The quote is gone.") + " Nothing is left to do here."
        return out
    out["paths"] = [
        _path("approve", "Approve it", "It is sliced for the farm and goes in the queue.", "thumbs_up", "Approve"),
        _path("refuse", "Refuse it", "The customer is told why and that this part of the order is refunded.",
              "thumbs_down", "Refuse", confirm="Refuse this custom print? The customer is told.",
              fields=[_text("reason", "Reason for the customer", required=True)]),
        _path("open", "Look at it first", "Open the quote to see the model and its slicing.", "open",
              "Open the quote", closes=False, open={"web": "/quotes", "app": "store:quotes"}),
    ]
    return out


def _review_run(db: Session, a: Alert, pid: str, v: dict, actor: str) -> tuple[str, str | None]:
    from . import quotes
    q = _quote(db, a)
    if not q:
        raise FixError("The quote is gone.")
    try:
        if pid == "approve":
            quotes.approve(db, q, actor)
            return "Approved", "approved"
        quotes.refuse(db, q, actor, v.get("reason") or "")
    except ValueError as e:
        raise FixError(str(e)) from None
    return "Refused", "refused"


PLAYBOOKS = {
    "print_done": (_plate, _plate_run), "print_failed": (_plate, _plate_run),
    "failure_detected": (_vision, _vision_run), "alarm": (_alarm, _alarm_run),
    "printer_offline": (_offline, _offline_run), "maintenance_due": (_maint, _maint_run),
    "order_problem": (_route, _route_run), "order_late": (_late, _late_run), "quote_ordered": (_review, _review_run),
}


def describe(db: Session, a: Alert) -> dict:
    """What the guided fix shows for this alert now ({"kind": "guided", ...}). Photos carry a web address (url) and
    the apps' path under /api/mobile/v1 (app, None when the apps can't show it); camera names the printer whose
    camera to show live."""
    build, _ = PLAYBOOKS[a.kind]
    out = build(db, a)
    out["summary"] = [[k, v] for k, v in out.get("summary", []) if v]
    return {"kind": "guided", "note": None, **out}


def run(db: Session, a: Alert, pid: str, values: dict, actor: str) -> dict:
    """Carry out one way to fix it. Answers {message, closed}; raises FixError when it can't be done."""
    if a.state != "open":
        raise FixError(f"This alert is already {a.state}.")
    if a.kind not in PLAYBOOKS:
        raise FixError("This alert has no guided fix.")
    paths = {p["id"]: p for p in describe(db, a).get("paths", [])}
    p = paths.get(pid)
    if not p or p.get("open"):
        raise FixError("That isn't one of the ways to fix it.")
    if p.get("off"):
        raise FixError(p["off"])
    vals = {}
    for f in p["fields"]:
        val = values.get(f["name"], f.get("value"))
        if f["type"] == "choice" and str(val) not in {o[0] for o in f["options"]}:
            raise FixError(f"Pick {f['label'].lower()}.")
        if f["type"] == "text":
            val = str(val or "")[:1000]
            if f.get("required") and not val.strip():
                raise FixError(f"{f['label']} is needed.")
        vals[f["name"]] = val
    message, how = PLAYBOOKS[a.kind][1](db, a, pid, vals, actor)
    closed = how is not None
    if closed:
        if a.state == "resolved":       # the action closed it on the way (a plate check done, say): it was this fix
            a.state = "open"
        inbox.close(db, a, "fixed", actor, how)
    event(db, f"alert:{a.id}", f"{p['title']}: {how or message}", actor=actor)
    return {"message": message, "closed": closed}

