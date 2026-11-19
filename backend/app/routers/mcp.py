"""MCP endpoint (Streamable HTTP, stateless JSON) so Claude can slice models through the hub.

POST /mcp with JSON-RPC 2.0. Auth is a hub API key (Authorization: Bearer ahk_...).
The hub talks to the dev slicer service; nothing here touches OrcaSlicer directly.
"""
import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session

from ..auth import current, Principal
from ..clients.base import ApiError
from ..config import settings
from ..db import get_db
from ..jobs import enqueue
from ..models import Job, Model, ModelFile, PrinterProfile
from ..services import slicing
from ..services.shop import farm_ref

router = APIRouter()

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER = {"name": "openprinthq", "title": "OpenPrintHQ slicing", "version": "1.0.0"}
INSTRUCTIONS = (
    "OpenPrintHQ slicing tools. Models are identified by hub id or code (for example M1185 or PR-460493). "
    "Printers are the team's printer profiles (list_printers), each sliced with OrcaSlicer or PrusaSlicer on the team's "
    "slicer service. Slicing can take a few minutes: slice_model waits up to wait_seconds, then returns a job id to check "
    "with slice_job_status. A new slice lands in review; approve_slice (or approve=true) makes it the file that printer "
    "uses, and for the default printer the file sent to the print farm."
)


class ToolError(Exception):
    pass


# ---------- helpers ----------

def _model(db: Session, ref) -> Model:
    m = None
    s = str(ref).strip()
    if s.isdigit():
        m = db.get(Model, int(s))
    if not m:
        m = db.execute(select(Model).where(func.lower(Model.code) == s.lower())).scalar()
    if not m:
        raise ToolError(f"no model with id or code {ref!r}; use find_models")
    return m


def _printer(db: Session, ref) -> PrinterProfile:
    if ref in (None, ""):
        p = db.execute(select(PrinterProfile).where(PrinterProfile.is_default.is_(True))).scalar() or \
            db.execute(select(PrinterProfile).where(PrinterProfile.active.is_(True)).order_by(PrinterProfile.id)).scalar()
        if not p:
            raise ToolError("no printer profiles yet; add them on the hub Printers page")
        return p
    s = str(ref).strip()
    if s.isdigit():
        p = db.get(PrinterProfile, int(s))
        if p:
            return p
    p = db.execute(select(PrinterProfile).where(or_(func.lower(PrinterProfile.name) == s.lower(),
                                                    func.lower(PrinterProfile.farm_printer) == s.lower()))).scalar()
    if not p:
        names = [x.name for x in db.execute(select(PrinterProfile)).scalars()]
        raise ToolError(f"no printer {ref!r}; printers: {', '.join(names)}")
    return p


def _printer_dict(p: PrinterProfile) -> dict:
    return {"id": p.id, "name": p.name, "engine": slicing.label(slicing.engine_of(p)), "tools": p.tools or [],
            "imported_preset": bool(p.machine_config), "farm_printer": p.farm_printer, "machine_preset": p.machine_preset,
            "default_process": p.process_preset or None, "filament_presets": p.filament_presets or {},
            "uses_designer_settings": p.use_model_settings, "default": p.is_default, "active": p.active}


def _file_dict(f: ModelFile) -> dict:
    return {"file_id": f.id, "filename": f.filename, "kind": f.kind, "printer": f.printer, "slicer": f.slicer,
            "plates": f.plates, "print_minutes": round(f.print_seconds / 60) if f.print_seconds else None,
            "grams": round(sum((x.get("grams") or 0) for x in (f.filament or [])), 1) or None,
            "production": bool(f.is_print_file), "in_farm": bool(farm_ref(f) and not f.farm_file_error),
            "settings": f.settings or None}


def _overrides(a: dict, engine: str = "orca") -> dict:
    keys, pats = slicing.KEYS[engine], slicing.PATTERNS[engine]
    o = dict(a.get("process_overrides") or {})
    if a.get("walls") is not None:
        o[keys["walls"]] = str(int(a["walls"]))
    if a.get("infill_percent") is not None:
        o[keys["infill"]] = f"{float(a['infill_percent']):g}%"
    if a.get("infill_pattern"):
        pat = str(a["infill_pattern"]).lower()
        o[keys["pattern"]] = pats.get(pat, pat)
    if a.get("supports") is not None:
        o[keys["supports"]] = "1" if a["supports"] else "0"
    return o


def _job_view(j: Job) -> dict:
    out = {"job_id": j.id, "state": j.state, "attempts": j.attempts}
    if j.state == "done" and j.result:
        r = j.result
        out["result"] = {k: r.get(k) for k in ("result_id", "state", "approved", "engine", "printer", "process", "time_s", "grams",
                                               "plates", "copies", "tools", "production", "bom_updated", "files", "warnings") if k in r}
        if r.get("time_s"):
            mins = round(r["time_s"] / 60)
            out["result"]["print_time"] = f"{mins // 60}h {mins % 60}m"
    if j.state == "failed":
        out["error"] = (j.error or "").split("\n")[0][:500]
    elif j.state == "queued" and j.error:
        out["last_error"] = (j.error or "").split("\n")[0][:300]
    return out


def _wait(db: Session, jid: int, seconds: float) -> Job:
    end = time.time() + max(0.0, min(float(seconds or 0), 55.0))
    while True:
        db.expire_all()
        j = db.get(Job, jid)
        if j.state in ("done", "failed") or time.time() >= end:
            return j
        time.sleep(2)


def _need(p: Principal, perm: str):
    perm = {"write": "slice", "read": "view"}.get(perm, perm)
    if perm not in (p.get("perms") or []):
        raise ToolError(f"you don't have the {perm} permission in this team")


# ---------- tools ----------

def t_slicer_status(db, p, a):
    from ..clients import slicer_configured
    if not slicer_configured():
        return {"configured": False}
    try:
        h = slicing.client().health()
    except ApiError as e:
        h = {"ok": False, "error": e.message}
    busy = db.execute(select(func.count()).select_from(Job).where(Job.kind == "slice.model",
                                                                  Job.state.in_(("queued", "running")))).scalar()
    return {"configured": True, "slicer": h, "slice_jobs_waiting": busy}


def t_list_printers(db, p, a):
    return [_printer_dict(x) for x in db.execute(select(PrinterProfile).order_by(PrinterProfile.name)).scalars()]


def t_find_models(db, p, a):
    q = (a.get("query") or "").strip()
    lim = max(1, min(int(a.get("limit") or 10), 50))
    stmt = select(Model).where(Model.status != "archived")
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Model.code) == q.lower(), func.lower(Model.name).like(like),
                              func.lower(Model.code).like(like)))
    if a.get("unsliced_only"):
        stmt = stmt.where(~Model.files.any(ModelFile.kind.in_(("3mf_sliced", "gcode"))))
    rows = db.execute(stmt.order_by(Model.code).limit(lim)).scalars().all()
    out = []
    for m in rows:
        sliced = [f for f in m.files if f.kind in ("3mf_sliced", "gcode")]
        out.append({"id": m.id, "code": m.code, "name": m.name, "category": m.category.name if m.category else None,
                    "status": m.status, "source_files": sum(1 for f in m.files if f.kind in ("stl", "3mf_project", "obj", "step")),
                    "sliced_files": len(sliced), "has_production_file": any(f.is_print_file for f in sliced)})
    return out


def t_model_slicing_info(db, p, a):
    m = _model(db, a["model"])
    prof = _printer(db, a.get("printer"))
    info = {"id": m.id, "code": m.code, "name": m.name, "designer_settings": m.print_settings or {},
            "files": [_file_dict(f) for f in m.files]}
    try:
        info["would_slice"] = [f.filename for f in slicing.choose_sources(m)]
        spec = slicing.build_spec(db, m, prof, {})
        info["plan_for_" + prof.name] = {k: spec[k] for k in ("machine", "process", "filaments", "process_overrides")}
    except (ValueError, ApiError) as e:
        info["cannot_slice"] = str(getattr(e, "message", e))
    return info


def t_slice_model(db, p, a):
    _need(p, "write")
    m = _model(db, a["model"])
    prof = _printer(db, a.get("printer"))
    opts = {"copies": max(1, min(int(a.get("copies") or 1), 50)), "ignore_designer": bool(a.get("ignore_designer_settings"))}
    if a.get("approve") or a.get("make_production"):
        opts["approve"] = True
    if a.get("ground_largest_face"):
        opts["orientation"] = {"mode": "largest_face"}
    if a.get("orientation") in ("as_is", "largest_face", "auto"):
        opts["orientation"] = {"mode": a["orientation"]}
    if a.get("layer_height"):
        opts["layer_height"] = float(a["layer_height"])
    if a.get("process"):
        opts["process"] = a["process"]
    if a.get("file_id"):
        opts["file_id"] = int(a["file_id"])
    over = _overrides(a, slicing.engine_of(prof))
    if over:
        opts["process_overrides"] = over
    try:
        slicing.choose_sources(m, opts.get("file_id"))
    except ValueError as e:
        raise ToolError(str(e))
    from .slicing import _queue
    j = _queue(db, m.id, prof.id, opts, p.actor, priority=2)
    db.commit()
    jid = j.id
    j = _wait(db, jid, a.get("wait_seconds", 50))
    out = {"model": f"{m.code} {m.name}", "printer": prof.name, **_job_view(j)}
    if j.state not in ("done", "failed"):
        out["note"] = "still slicing; call slice_job_status with this job_id"
    return out


def t_slice_job_status(db, p, a):
    j = db.get(Job, int(a["job_id"]))
    if not j or j.kind != "slice.model":
        raise ToolError("no slice job with that id")
    return _job_view(_wait(db, j.id, a.get("wait_seconds", 0)))


def t_bulk_slice(db, p, a):
    _need(p, "write")
    prof = _printer(db, a.get("printer"))
    refs = a.get("models") or []
    if not refs:
        raise ToolError("give a list of model ids or codes")
    if len(refs) > 200:
        raise ToolError("at most 200 models per call")
    opts = {"copies": 1}
    if a.get("approve") or a.get("make_production"):
        opts["approve"] = True
    if a.get("layer_height"):
        opts["layer_height"] = float(a["layer_height"])
    over = _overrides(a, slicing.engine_of(prof))
    if over:
        opts["process_overrides"] = over
    queued, skipped = [], []
    for r in refs:
        try:
            m = _model(db, r)
            slicing.choose_sources(m)
        except (ToolError, ValueError) as e:
            skipped.append({"model": r, "reason": str(e)})
            continue
        from .slicing import _queue
        _queue(db, m.id, prof.id, opts, p.actor, priority=6)
        queued.append(m.code)
    db.commit()
    return {"printer": prof.name, "queued": len(queued), "models": queued, "skipped": skipped}


def t_slice_results(db, p, a):
    from ..models import SliceResult
    m = _model(db, a["model"])
    out = []
    for prof in db.execute(select(PrinterProfile).where(PrinterProfile.active.is_(True)).order_by(PrinterProfile.name)).scalars():
        r = slicing.result_for(db, m.id, prof.id, create=False)
        st = (r.stats or {}) if r else {}
        out.append({"printer": prof.name, "result_id": r.id if r else None, "state": r.state if r else "not sliced",
                    "print_minutes": round(st["time_s"] / 60) if st.get("time_s") else None, "grams_per_unit": st.get("per_unit_g"),
                    "tools": st.get("tools"), "problems": [x["msg"] for x in ((r.problems if r else None) or slicing.problems_for(m, prof))],
                    "cost_per_unit": slicing.cost_of(db, m, prof, st) if r else None,
                    "real_prints": {k: v for k, v in slicing.print_summary(db, r).items() if k != "recent"} if r else None,
                    "error": r.error if r else None})
    return {"model": f"{m.code} {m.name}", "colors_needed": slicing.colors_needed(m), "printers": out}


def t_approve_slice(db, p, a):
    _need(p, "write")
    from ..models import SliceResult
    from ..tasks import _after_approve
    r = db.get(SliceResult, int(a["result_id"]))
    if not r:
        raise ToolError("no slice result with that id; see slice_results")
    try:
        out = slicing.approve(db, r, p.actor)
    except ValueError as e:
        raise ToolError(str(e))
    m = db.get(Model, r.model_id)
    if out.get("bom_updated"):
        enqueue(db, "store.sync_stock", ref="stock:all", delay=5)
    _after_approve(db, m, out)
    db.commit()
    return {"model": m.code, **out}


def t_record_print(db, p, a):
    _need(p, "write")
    from ..models import SliceResult
    r = db.get(SliceResult, int(a["result_id"]))
    if not r:
        raise ToolError("no slice result with that id; see slice_results")
    rec = slicing.record_print(db, r, r.model_id, a.get("outcome") or "ok", note=a.get("note"), actor=p.actor,
                               duration_s=int(float(a["duration_minutes"]) * 60) if a.get("duration_minutes") else None,
                               printer_name=db.get(PrinterProfile, r.profile_id).name)
    db.commit()
    return {"recorded": rec.id, **{k: v for k, v in slicing.print_summary(db, r).items() if k != "recent"}}


def t_set_production_file(db, p, a):
    _need(p, "write")
    f = db.get(ModelFile, int(a["file_id"]))
    if not f:
        raise ToolError("no file with that id")
    if f.kind not in ("3mf_sliced", "gcode"):
        raise ToolError("The farm needs a sliced .gcode.3mf or .gcode file")
    for o in f.model.files:
        o.is_print_file = o.id == f.id
    if f.model.product:
        enqueue(db, "farm.sync_product", ref=f"product:{f.model.product.id}:farm",
                payload={"product_id": f.model.product.id}, delay=5)
    enqueue(db, "farm.mirror_model", ref=f"model:{f.model.id}:farm", payload={"model_id": f.model.id}, delay=2, priority=9)
    db.commit()
    return {"model": f.model.code, "production_file": _file_dict(f)}


S_MODEL = {"type": ["string", "integer"], "description": "Hub model id or code, e.g. M1185"}
S_PRINTER = {"type": ["string", "integer"], "description": "Printer profile name, farm printer name or id. Omit for the default printer."}
S_TUNE = {
    "layer_height": {"type": "number", "description": "mm, picks the matching process preset (e.g. 0.16, 0.2, 0.28)"},
    "walls": {"type": "integer", "minimum": 1, "maximum": 20},
    "infill_percent": {"type": "number", "minimum": 0, "maximum": 100},
    "infill_pattern": {"type": "string", "description": "grid, gyroid, rectilinear, honeycomb, cubic, lightning, ..."},
    "supports": {"type": "boolean"},
    "approve": {"type": "boolean", "description": "Approve the result right away (skip review); for the default printer it becomes the production file"},
}

TOOLS = {
    "slicer_status": (t_slicer_status, "Is the slicer service up, which engines and versions it runs, and how many slice jobs are waiting.",
                      {"type": "object", "properties": {}}, True),
    "list_printers": (t_list_printers, "List the printer profiles the hub slices for (machine preset, default process, filaments).",
                      {"type": "object", "properties": {}}, True),
    "find_models": (t_find_models, "Search hub models by code or name. Shows how many source and sliced files each has.",
                    {"type": "object", "properties": {
                        "query": {"type": "string", "description": "Code or part of the name; empty lists all"},
                        "unsliced_only": {"type": "boolean", "description": "Only models with no sliced file yet"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50}}}, True),
    "model_slicing_info": (t_model_slicing_info, "Show a model's files, sliced results per printer, designer settings, and what a slice would use.",
                           {"type": "object", "properties": {"model": S_MODEL, "printer": S_PRINTER}, "required": ["model"]}, True),
    "slice_model": (t_slice_model, "Slice a model for a printer. Returns print time and grams per tool when done. "
                                   "The result waits for review unless approve is set; the approved file stays in use until then.",
                    {"type": "object", "properties": {
                        "model": S_MODEL, "printer": S_PRINTER, **S_TUNE,
                        "copies": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Copies arranged on the plate(s)"},
                        "process": {"type": "string", "description": "Exact process (print) preset name for the printer's engine (overrides layer_height)"},
                        "file_id": {"type": "integer", "description": "Slice this source file instead of the automatic choice"},
                        "ignore_designer_settings": {"type": "boolean"},
                        "orientation": {"type": "string", "enum": ["as_is", "largest_face", "auto"],
                                        "description": "How the model sits on the bed (OrcaSlicer printers only)"},
                        "process_overrides": {"type": "object", "description": "Raw slicer process keys for the printer's engine, e.g. {\"brim_type\": \"no_brim\"}"},
                        "wait_seconds": {"type": "number", "minimum": 0, "maximum": 55, "description": "How long to wait for the result (default 50)"}},
                     "required": ["model"]}, False),
    "slice_job_status": (t_slice_job_status, "Check a slice job started by slice_model or bulk_slice; can wait up to 55 s.",
                         {"type": "object", "properties": {"job_id": {"type": "integer"},
                                                           "wait_seconds": {"type": "number", "minimum": 0, "maximum": 55}},
                          "required": ["job_id"]}, True),
    "bulk_slice": (t_bulk_slice, "Queue slices for many models on one printer (runs in the background, 3 at a time).",
                   {"type": "object", "properties": {
                       "models": {"type": "array", "items": S_MODEL, "maxItems": 200}, "printer": S_PRINTER,
                       **{k: S_TUNE[k] for k in ("layer_height", "walls", "infill_percent", "infill_pattern", "supports", "approve")}},
                    "required": ["models"]}, False),
    "slice_results": (t_slice_results, "A model's slice state on every printer: review, approved, stale, failed, with grams per tool and problems.",
                      {"type": "object", "properties": {"model": S_MODEL}, "required": ["model"]}, True),
    "approve_slice": (t_approve_slice, "Approve a slice result in review so the printer uses it.",
                      {"type": "object", "properties": {"result_id": {"type": "integer"}}, "required": ["result_id"]}, False),
    "record_print": (t_record_print, "Record how a real print of a slice went (ok, failed or cancelled), with an optional note and time.",
                     {"type": "object", "properties": {"result_id": {"type": "integer"},
                                                       "outcome": {"type": "string", "enum": ["ok", "failed", "cancelled"]},
                                                       "duration_minutes": {"type": "number"}, "note": {"type": "string"}},
                      "required": ["result_id", "outcome"]}, False),
    "set_production_file": (t_set_production_file, "Make a sliced file the one the farm prints for its model.",
                            {"type": "object", "properties": {"file_id": {"type": "integer"}}, "required": ["file_id"]}, False),
}


def _tool_list():
    out = []
    for name, (_, desc, schema, ro) in TOOLS.items():
        out.append({"name": name, "description": desc, "inputSchema": schema,
                    "annotations": {"readOnlyHint": ro, "destructiveHint": False, "openWorldHint": False}})
    return out


def _handle(db: Session, p: Principal, msg: dict, req_version: list):
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        want = params.get("protocolVersion")
        ver = want if want in PROTOCOLS else PROTOCOLS[0]
        req_version.append(ver)
        return {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER, "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": _tool_list()}
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            raise LookupError(f"unknown tool {name}")
        fn = TOOLS[name][0]
        try:
            data = fn(db, p, params.get("arguments") or {})
        except (ToolError, KeyError, ValueError, TypeError) as e:
            db.rollback()
            msg_ = f"missing argument {e}" if isinstance(e, KeyError) else str(e)
            return {"content": [{"type": "text", "text": msg_}], "isError": True}
        except ApiError as e:
            db.rollback()
            return {"content": [{"type": "text", "text": f"{e.service}: {e.message}"}], "isError": True}
        text = json.dumps(data, default=str, indent=1)
        res = {"content": [{"type": "text", "text": text}]}
        if isinstance(data, dict):
            res["structuredContent"] = data
        return res
    if method in ("resources/list", "prompts/list"):
        return {method.split("/")[0]: []}
    raise LookupError(f"method not found: {method}")


def _one(db, p, msg, ver):
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "invalid request"}}
    if "id" not in msg:   # notification
        return None
    try:
        return {"jsonrpc": "2.0", "id": msg["id"], "result": _handle(db, p, msg, ver)}
    except LookupError as e:
        return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": str(e)}}
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"[:500]}}


def _unauthorized(detail: str):
    from .oauth import resource_metadata_url
    return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": detail}}, status_code=401,
                        headers={"WWW-Authenticate": f'Bearer resource_metadata="{resource_metadata_url()}"'})


@router.post("/mcp")
async def mcp_post(request: Request, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    try:
        p = current(request, db)
        from ..tenant import set_team
        set_team(p["team_id"])
    except HTTPException as e:
        if e.status_code == 401:
            return _unauthorized(str(e.detail))
        raise
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, 400)
    from starlette.concurrency import run_in_threadpool
    ver: list = []
    if isinstance(body, list):
        out = [r for r in [await run_in_threadpool(_one, db, p, m, ver) for m in body] if r is not None]
        return JSONResponse(out) if out else Response(status_code=202)
    r = await run_in_threadpool(_one, db, p, body, ver)
    return JSONResponse(r) if r is not None else Response(status_code=202)


@router.get("/mcp")
@router.delete("/mcp")
def mcp_other():
    return Response(status_code=405, headers={"Allow": "POST"})
