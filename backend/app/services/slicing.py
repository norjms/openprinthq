"""Slicing workflow: printer profiles, slice results per model and printer, review and approval.

A team describes each of its printers once (engine, printer preset from that engine's catalog or an imported
one, tools with their nozzles and loaded materials). The hub slices a model for a printer through the slicer
service, the result lands in review with previews and per-tool grams, and approving it makes those files the
ones that printer uses. Anything that fed a slice (files, printer, settings, slicer version) is hashed, so a
result goes stale when one of them changes and can be re-sliced.

The designer's recommended settings (layer height, walls, infill, supports) are layered on top of the
engine's presets for the printer, then the team's per-printer overrides on top of that.
"""
import hashlib
import json
import logging
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.base import ApiError
from ..config import settings
from ..models import Model, ModelFile, PrinterProfile, BomFilament, SliceResult
from . import importer

log = logging.getLogger("hub.slicing")

ENGINES = {"orca": "OrcaSlicer", "prusa": "PrusaSlicer"}
STATES = ("queued", "slicing", "review", "approved", "failed", "stale")
ORIENT_MODES = ("as_is", "largest_face", "auto", "face")

# designer setting -> slicer config key, per engine
KEYS = {
    "orca": {"walls": "wall_loops", "infill": "sparse_infill_density", "pattern": "sparse_infill_pattern",
             "supports": "enable_support"},
    "prusa": {"walls": "perimeters", "infill": "fill_density", "pattern": "fill_pattern", "supports": "support_material"},
}
PATTERNS = {
    "orca": {"grid": "grid", "gyroid": "gyroid", "rectilinear": "zig-zag", "line": "line", "cubic": "cubic",
             "honeycomb": "honeycomb", "triangles": "triangles", "lightning": "lightning", "adaptive": "adaptivecubic",
             "concentric": "concentric", "crosshatch": "crosshatch", "3d honeycomb": "3dhoneycomb"},
    "prusa": {"grid": "grid", "gyroid": "gyroid", "rectilinear": "rectilinear", "line": "line", "cubic": "cubic",
              "honeycomb": "honeycomb", "triangles": "triangles", "lightning": "lightning", "adaptive": "adaptivecubic",
              "concentric": "concentric", "3d honeycomb": "3dhoneycomb", "stars": "stars"},
}


# filament types the slicers' presets are filed under, longest first so PETG-CF wins over PETG
BASE_TYPES = ["PETG-CF", "PLA-CF", "PA-CF", "PA6-CF", "PAHT-CF", "PET-CF", "ABS-GF", "ASA-CF", "PC-CF", "PA12-CF",
              "PETG", "PCTG", "PLA", "ABS", "ASA", "TPU", "TPE", "PA6", "PA12", "PA", "PC", "PET", "PVA", "BVOH",
              "HIPS", "PP", "PVB", "FLEX"]


def material_type(material: str | None) -> str:
    """The filament type a material name belongs to: "PLA+", "Silk PLA", "Matte PLA" -> PLA; "PETG CF" -> PETG-CF."""
    m = (material or "PLA").upper().replace("PLUS", "").replace("+", " ").replace("_", "-").replace("NYLON", "PA")
    m = re.sub(r"\s*-\s*", "-", m)
    m = re.sub(r"\b(PETG|PLA|PA|PET|ABS|ASA|PC)\s+(CF|GF)\b", r"\1-\2", m)
    for t in BASE_TYPES:
        if re.search(r"(?<![A-Z0-9])" + re.escape(t) + r"(?![A-Z0-9])", m):
            return t
    return m.split()[0] if m.split() else "PLA"


def client():
    from ..clients import slicer
    return slicer()


def engine_of(prof: PrinterProfile) -> str:
    return prof.engine or "orca"


def label(engine: str) -> str:
    return ENGINES.get(engine, engine)


def now():
    return datetime.now(timezone.utc)


# ---------- printers ----------

def catalog_entry(prof_or_machine, engine: str | None = None) -> dict | None:
    """The slicer's summary of a printer preset (nozzle, tool count, vendor), or None."""
    if isinstance(prof_or_machine, PrinterProfile):
        machine, engine = prof_or_machine.machine_preset, engine_of(prof_or_machine)
    else:
        machine = prof_or_machine
    try:
        return next((m for m in client().machines(machine, engine or "orca") if m["name"] == machine), None)
    except ApiError:
        return None


def tool_count(entry: dict | None) -> int:
    if not entry:
        return 1
    return int(entry.get("tools") or entry.get("extruders") or 1)


def default_tools(entry: dict | None, machine: str) -> list[dict]:
    n = tool_count(entry)
    try:
        nozzle = float(str((entry or {}).get("nozzle") or "").split(",")[0])
    except ValueError:
        m = re.search(r"(\d\.\d+)\s*(mm\s*)?nozzle", machine or "")
        nozzle = float(m.group(1)) if m else 0.4
    flow = "hf" if re.search(r"\bHF\s?\d", machine or "") else "standard"
    return [{"slot": i + 1, "nozzle": nozzle, "flow": flow, "material": None, "color": None, "preset": None}
            for i in range(n)]


def clean_tools(tools: list | None) -> list[dict]:
    out = []
    for i, t in enumerate(tools or []):
        t = t or {}
        color = (t.get("color") or "").strip() or None
        if color and not re.fullmatch(r"#?[0-9A-Fa-f]{6}", color):
            raise ValueError(f"tool {i + 1}: colour must look like #RRGGBB")
        try:
            nozzle = float(t.get("nozzle") or 0.4)
        except (TypeError, ValueError):
            raise ValueError(f"tool {i + 1}: nozzle must be a number")
        out.append({"slot": i + 1, "nozzle": nozzle, "flow": t.get("flow") if t.get("flow") in ("standard", "hf") else "standard",
                    "material": (t.get("material") or "").strip().upper() or None,
                    "color": ("#" + color.lstrip("#").upper()) if color else None,
                    "preset": (t.get("preset") or "").strip() or None})
    if len(out) > 16:
        raise ValueError("at most 16 tools")
    return out


def ensure_tools(prof: PrinterProfile) -> list[dict]:
    if not prof.tools:
        prof.tools = default_tools(catalog_entry(prof), prof.machine_preset)
    return prof.tools


def discover_printers() -> list[dict]:
    """Printers the farm reports, as hints for the setup wizard. Without the farm, an empty list."""
    from ..services.connections import is_connected
    from ..clients import farm, farm_connected
    if not farm_connected():
        return []
    out = []
    try:
        for p in farm().printers_full() or []:
            cap = p.get("capabilities") or {}
            out.append({"source": "farm", "name": p.get("name"), "model": p.get("model"),
                        "nozzle": cap.get("nozzle_size"), "tools": cap.get("tool_count") or cap.get("extruders")})
    except Exception as e:  # noqa: BLE001
        log.info("farm printer discovery failed: %s", e)
    return out


def default_profile(db: Session) -> PrinterProfile | None:
    return db.execute(select(PrinterProfile).where(PrinterProfile.is_default.is_(True))).scalar() or \
        db.execute(select(PrinterProfile).where(PrinterProfile.active.is_(True)).order_by(PrinterProfile.id)).scalar()


# ---------- what a model needs ----------

def colors_needed(model: Model) -> int:
    prod = model.product
    bom = len(prod.filaments) if prod and prod.filaments else 0
    return max(model.colors_needed or 0, bom, 1)


def filament_capacity(prof: PrinterProfile) -> int:
    """How many filaments the printer holds at once: its tools, or more with an AMS or other filament changer."""
    return max(len(prof.tools or []), prof.filament_slots or 0) or 1


def problems_for(model: Model, prof: PrinterProfile) -> list[dict]:
    """Things to fix before this model prints well on this printer (shown on the Print tab and Problems)."""
    out = []
    tools = prof.tools or []
    need = colors_needed(model)
    have = filament_capacity(prof)
    if need > have and have > 1:          # a one-filament printer prints every model in one colour (by choice)
        held = (f"holds {have} filament{'s' if have != 1 else ''}" if prof.filament_slots
                else f"has {have} tool{'s' if have != 1 else ''}")
        out.append({"code": "tools", "msg": f"needs {need} colours, {prof.name} {held}"
                    + (" (it will print with filament changes)" if have == 1 else "")})
    prod = model.product
    loaded = [material_type(t.get("material")) for t in tools if t.get("material")]
    if prod and prod.filaments and loaded:
        for b in prod.filaments:
            mat = material_type(b.material)
            if mat not in loaded:
                out.append({"code": "material", "msg": f"colour {b.slot} wants {mat}; {prof.name} has {', '.join(sorted(set(loaded)))} loaded"})
    return out


# ---------- the spec sent to the slicer ----------

KEYS["orca"].update({"top": "top_shell_layers", "bottom": "bottom_shell_layers"})
KEYS["prusa"].update({"top": "top_solid_layers", "bottom": "bottom_solid_layers"})
ORCA_TO_PRUSA_PATTERN = {"zig-zag": "rectilinear", "alignedrectilinear": "alignedrectilinear", "crosshatch": "rectilinear",
                         "3dhoneycomb": "3dhoneycomb", "adaptivecubic": "adaptivecubic", "supportcubic": "supportcubic"}


def project_settings(sources: list | None) -> dict:
    """The designer's own settings saved in a slicer project (Bambu Studio / OrcaSlicer / PrusaSlicer 3MF)."""
    for f in sources or []:
        if f.filename.lower().endswith(".3mf") and f.kind != "3mf_sliced" and (f.settings or {}):
            return dict(f.settings)
    return {}


def _truthy(v) -> bool | None:
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y"):
        return True
    if s in ("0", "false", "no", "n", "none"):
        return False
    return None


def designer_overrides(model: Model, engine: str = "orca", sources: list | None = None) -> tuple[dict, float | None]:
    """Process overrides from the designer's settings; also returns the layer height.
    Base layer: the settings saved in the designer's project file. On top: the recommendations recorded with the
    model (its notes or listing), which win where both exist."""
    keys, pats = KEYS[engine], PATTERNS[engine]
    out, lh = {}, None
    proj = project_settings(sources)
    if proj:
        try:
            lh = float(proj.get("layer_height")) or None
        except (TypeError, ValueError):
            lh = None
        w = proj.get("walls")
        if w is not None and 1 <= int(float(w)) <= 12:
            out[keys["walls"]] = str(int(float(w)))
        inf = str(proj.get("infill") or "").strip()
        if re.fullmatch(r"\d+(\.\d+)?%?", inf):
            out[keys["infill"]] = inf if inf.endswith("%") else f"{inf}%"
        pat = str(proj.get("infill_pattern") or "").strip()
        if pat:
            out[keys["pattern"]] = pat if engine == "orca" else ORCA_TO_PRUSA_PATTERN.get(pat, pat)
        sup = _truthy(proj.get("supports"))
        if sup is not None:
            out[keys["supports"]] = "1" if sup else "0"
        for k in ("top", "bottom"):
            v = proj.get(f"{k}_layers")
            if v is not None and 0 <= int(float(v)) <= 30:
                out[keys[k]] = str(int(float(v)))
    ps = model.print_settings or {}
    try:
        lh = float(str(ps.get("layer_height") or "").split(",")[0]) or lh
    except ValueError:
        pass
    walls = re.search(r"\d+", str(ps.get("walls") or ""))
    if walls and 1 <= int(walls.group()) <= 12:
        out[keys["walls"]] = walls.group()
    inf = str(ps.get("infill") or "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", inf)
    if m:
        out[keys["infill"]] = f"{m.group(1)}%"
    for word, pat in pats.items():
        if word in inf.lower():
            out[keys["pattern"]] = pat
            break
    sup = str(ps.get("supports") or "").strip().lower()
    if sup in ("yes", "true", "1", "y") or sup.startswith("yes"):
        out[keys["supports"]] = "1"
    elif sup in ("no", "false", "0", "n", "none"):
        out[keys["supports"]] = "0"
    return out, lh


def choose_sources(model: Model, file_id: int | None = None) -> list[ModelFile]:
    files = [f for f in model.files if f.library_rel]
    if file_id:
        f = next((x for x in files if x.id == file_id), None)
        if not f:
            raise ValueError("file not found on this model")
        return [f]
    projects = [f for f in files if f.kind == "3mf_project" or (f.filename.lower().endswith(".3mf") and f.kind != "3mf_sliced")]
    if projects:
        def rank(f):
            n = f.filename.lower()
            return (0 if ("bambu" in n or "orca" in n) else 1 if (f.slicer or "").lower().startswith(("bambu", "orca")) else 2,
                    -(f.bytes or 0))
        return [sorted(projects, key=rank)[0]]
    meshes = [f for f in files if f.filename.lower().endswith((".stl", ".step", ".stp", ".obj", ".amf"))]
    if meshes:
        return meshes[:12]
    raise ValueError("no model file to slice (add an STL or 3MF)")


def _slots(model: Model) -> list:
    prod = model.product
    return sorted(prod.filaments, key=lambda b: b.slot) if prod and prod.filaments else []


def _brand_preset(prof: PrinterProfile, material: str | None) -> str | None:
    """A materials-list entry that names a Bambu Lab line ("PLA Matte", "PETG HF") uses that line's own preset for
    this printer ("Bambu PLA Matte @BBL H2D"): the same settings Bambu Studio uses."""
    if not material or engine_of(prof) != "orca":
        return None
    from .filaments import bambu_catalog, preset_family
    fams = {c["family"] for c in bambu_catalog()}
    if material not in fams:
        return None
    want = preset_family(material) + " @"
    try:
        names = [x["name"] for x in client().presets("filament", prof.machine_preset, engine="orca")]
    except ApiError:
        return None
    hits = [n for n in names if n.startswith(want)]
    if not hits:
        return None
    # the preset made for this printer and nozzle ("Bambu PLA Matte @BBL H2D", "... @BBL H2D 0.6 nozzle"),
    # which is what Bambu Studio picks; the generic "@System"/"@base" ones only as a last resort
    entry = catalog_entry(prof) or {}
    model = re.sub(r"^Bambu Lab\s+", "", str(entry.get("model") or ""))
    m = re.search(r"(\d\.\d+)\s*nozzle", prof.machine_preset or "")
    nozzle = m.group(1) if m else "0.4"
    exact = [f"{want}BBL {model}" + ("" if nozzle == "0.4" else f" {nozzle} nozzle")]
    for e in exact:
        if e in hits:
            return e
    tagged = [n for n in hits if f"@BBL {model}" in n and (nozzle == "0.4" or f"{nozzle} nozzle" in n)]
    if tagged:
        return sorted(tagged, key=len)[0]
    plain = [n for n in hits if "@System" not in n and "@base" not in n]
    return sorted(plain or hits, key=len)[0]


def _pick_filament(prof: PrinterProfile, material: str, process: str | None) -> str | None:
    c, eng = client(), engine_of(prof)
    hit = _brand_preset(prof, material)
    if hit:
        return hit
    base = material_type(material)
    cands = [x["name"] for x in c.presets("filament", prof.machine_preset, base, engine=eng, process=process)]
    if not cands:
        return None
    entry = catalog_entry(prof) or {}
    default = entry.get("default_filament")
    if default in cands:
        return default
    if eng == "prusa":
        pref = [n for n in cands if n.lower().startswith("prusament")] or [n for n in cands if n.lower().startswith("generic")]
        if pref:
            return sorted(pref, key=len)[0]
    gen = [n for n in cands if "generic" in n.lower()]
    return sorted(gen or cands, key=len)[0]


def filament_spec(model: Model, prof: PrinterProfile, process: str | None,
                  override: list[dict] | None = None) -> tuple[list[str], list[str | None], str, list[int]]:
    """Per colour: the filament preset, its colour, and which tool prints it.
    Colour n prints on tool n; a printer with fewer tools than colours reuses tools (filament changes).
    override: [{material, color}] per colour, the filament someone mapped each colour to (filament mapping)."""
    from types import SimpleNamespace
    tools = ensure_tools(prof)
    mapping = prof.filament_presets or {}
    slots = _slots(model)
    if override:
        slots = [SimpleNamespace(material=o.get("material") or (slots[i].material if i < len(slots) else None),
                                 color_hex=o.get("color") or (slots[i].color_hex if i < len(slots) else None))
                 for i, o in enumerate(override)]
    names, colors, tool_of = [], [], []
    for i, b in enumerate(slots or [None]):
        tool = tools[i % len(tools)] if tools else {}
        mat = ((b.material if b else None) or tool.get("material") or "PLA").upper()
        base = material_type(mat)
        preset = None
        if tool.get("preset") and (not tool.get("material") or material_type(tool["material"]) == base):
            preset = tool["preset"]
        preset = preset or mapping.get(mat) or mapping.get(base) or _pick_filament(prof, (b.material if b and b.material else base), process)
        if not preset:
            raise ValueError(f"no {label(engine_of(prof))} filament preset for {base} on {prof.machine_preset}")
        names.append(preset)
        colors.append((b.color_hex if b and b.color_hex else None) or tool.get("color"))
        tool_of.append((i % len(tools)) + 1 if tools else 1)
    return names, colors, (slots[0].material if slots and slots[0].material else "PLA"), tool_of


def _process_for(prof: PrinterProfile, lh: float | None, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    process = prof.process_preset
    if lh:
        procs = client().presets("process", prof.machine_preset, engine=engine_of(prof))

        def score(p):
            try:
                v = float(p.get("layer_height") or 0)
            except (TypeError, ValueError):
                v = 0
            n = p["name"].lower()
            return (abs(v - lh), 0 if ("standard" in n or "balanced" in n) else 1 if ("quality" in n or "speed" in n) else 2, len(n))
        best = sorted(procs, key=score)[0] if procs else None
        if best and (abs(float(best.get("layer_height") or 0) - lh) < 0.021 or not process):
            process = best["name"]      # nothing set on the printer: the nearest layer height wins
    return process


def orientation_spec(orient: dict | None, engine: str) -> tuple[dict, list[str]]:
    o = orient or {}
    mode = o.get("mode") or "as_is"
    warn = []
    if mode == "as_is":
        return {"orient": False}, warn
    if engine != "orca":
        return {"orient": False}, [f"{label(engine)} can't reorient from the command line; sliced as the file is"]
    if mode == "largest_face":
        return {"ground_largest_face": True}, warn
    if mode == "auto":
        return {"orient": True}, warn
    if mode == "face" and o.get("normal") and len(o["normal"]) == 3:
        return {"ground_face_normal": [float(x) for x in o["normal"]]}, warn
    return {}, warn


# OrcaSlicer's build plate names (curr_bed_type)
BED_TYPES = ["Cool Plate", "Engineering Plate", "High Temp Plate", "Textured PEI Plate", "Supertack Plate",
             "Textured Cool Plate"]


def project_filament_count(sources: list | None) -> int | None:
    """How many filaments the designer's project file defines (None when slicing plain model files)."""
    from . import filaments
    for f in sources or []:
        if f.kind == "3mf_project" and f.library_rel:
            n = len(filaments.project_slots_of(Path(settings().library_root) / f.library_rel))
            if n:
                return n
    return None


def build_spec(db: Session, model: Model, prof: PrinterProfile, opts: dict, sources: list | None = None) -> dict:
    eng = engine_of(prof)
    if sources is None:
        try:
            sources = choose_sources(model, opts.get("file_id"))
        except ValueError:
            sources = []
    over, lh = designer_overrides(model, eng, sources) if prof.use_model_settings and not opts.get("ignore_designer") else ({}, None)
    if opts.get("layer_height"):
        lh = float(opts["layer_height"])
    process = _process_for(prof, lh, opts.get("process"))
    names, colors, material, tool_of = filament_spec(model, prof, process, opts.get("filament_materials"))
    cap = project_filament_count(sources)
    if cap and len(names) > cap:
        # the designer's project prints with fewer filaments than the materials list names (their note often lists
        # alternative colours, not a multi-colour print); OrcaSlicer refuses more filaments than the project has
        names, colors, tool_of = names[:cap], colors[:cap], tool_of[:cap]
    one_colour_note = None
    # Printers that hold one filament (Voron, Prusas) print multi-colour models in one colour;
    # a project may use a second filament even when the materials list names one colour, so it is always flattened
    single = eng == "orca" and filament_capacity(prof) == 1 and (
        len(names) > 1 or any(f.kind == "3mf_project" for f in (sources or [])))
    if single:
        if len(names) > 1:
            one_colour_note = f"Printed in one colour: {prof.name} holds one filament"
        names, colors, tool_of = names[:1], colors[:1], tool_of[:1]
    elif eng == "orca" and not cap and len(names) > 1 and sources and all(f.kind != "3mf_project" for f in sources):
        # plain model files can't say which part prints in which colour, so every part prints in the first one
        # (OrcaSlicer also rejects three or more filaments for them on the H2D)
        names, colors, tool_of = names[:1], colors[:1], tool_of[:1]
        one_colour_note = ("Plain model files print in one colour: they can't say which part gets which colour, "
                           "so only the first colour was used")
    proc_over = {**over, **(prof.process_overrides or {}), **(opts.get("process_overrides") or {})}
    mach_over = {**(prof.machine_overrides or {}), **(opts.get("machine_overrides") or {})}
    orient, warn = orientation_spec(opts.get("orientation"), eng)
    spec = {"engine": eng, "machine": prof.machine_preset, "process": process, "filaments": names, "material": material,
            "machine_overrides": mach_over, "process_overrides": proc_over, "copies": int(opts.get("copies") or 1),
            "arrange": opts.get("arrange"), "output_name": f"{model.code} {prof.name}", "tool_of_color": tool_of,
            **orient}
    if single:
        spec["single_filament"] = True       # the slicer service moves every part and painted area to filament 1
    if prof.machine_config:
        cfg = prof.machine_config
        if eng == "orca":
            try:
                cfg = json.loads(cfg)
            except ValueError:
                raise ValueError("the imported OrcaSlicer printer preset is not valid JSON")
        spec["machine_config"] = cfg
    if eng == "orca":
        spec["filament_overrides"] = [{"filament_colour": [c]} if c else {} for c in colors]
        bed = opts.get("bed_type") or prof.bed_type
        if bed:
            spec["bed_type"] = bed                  # the plate always fitted (or, for a plate reslice, the one swapped
                                                    # in); also replaces a designer project's plate
        prod = model.product
        spec["metadata"] = {k: v for k, v in {"hub_model": model.code, "hub_sku": prod.sku if prod else None,
                                               "hub_printer": prof.name}.items() if v}
    elif any(colors):
        spec["machine_overrides"] = {**mach_over, "extruder_colour": ";".join(c or "" for c in colors)}
    spec["_warnings"] = warn + ([one_colour_note] if one_colour_note else [])
    return spec


# ---------- what fed a slice (for stale detection) ----------

def inputs_hash(model: Model, prof: PrinterProfile, opts: dict, engine_version: str | None,
                sources: list[ModelFile] | None = None) -> str:
    try:
        srcs = sources if sources is not None else choose_sources(model, opts.get("file_id"))
    except ValueError:
        srcs = []
    ps = model.print_settings or {}
    data = {
        "src": sorted(f.sha256 for f in srcs),
        "prof": [engine_of(prof), prof.machine_preset, prof.process_preset,
                 hashlib.sha256((prof.machine_config or "").encode()).hexdigest()[:16],
                 [{k: t.get(k) for k in ("nozzle", "flow", "material", "preset")} for t in (prof.tools or [])],
                 prof.filament_presets or {}, prof.machine_overrides or {}, prof.process_overrides or {},
                 bool(prof.use_model_settings), prof.bed_type],
        "designer": {k: ps.get(k) for k in ("layer_height", "walls", "infill", "supports")} if prof.use_model_settings else {},
        # the colours as the slicer sees them (a model without a materials list slices as one PLA colour)
        "bom": [(b.slot, (b.material or "PLA").upper(), (b.color_hex or "").upper()) for b in _slots(model)] or [(1, "PLA", "")],
        "opts": {k: opts.get(k) for k in ("layer_height", "process", "process_overrides", "machine_overrides", "copies",
                                          "ignore_designer", "orientation", "file_id")},
        # a plate reslice hashes its plate; the usual slice leaves the key out so its hash is unchanged
        **({"bed_type": opts["bed_type"]} if opts.get("bed_type") else {}),
        # a filament-mapping reslice hashes the filaments it was given
        **({"filament_materials": opts["filament_materials"]} if opts.get("filament_materials") else {}),
        "engine": engine_version,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def engine_version(prof: PrinterProfile) -> str | None:
    try:
        return client().engines().get(engine_of(prof))
    except ApiError:
        return None


# ---------- results ----------

def result_for(db: Session, model_id: int, profile_id: int, create: bool = True) -> SliceResult | None:
    r = db.execute(select(SliceResult).where(SliceResult.model_id == model_id, SliceResult.profile_id == profile_id)).scalar()
    if not r and create:
        r = SliceResult(model_id=model_id, profile_id=profile_id, state="queued")
        db.add(r)
        db.flush()
    return r


def preview_dir(result_id: int) -> Path:
    return Path(settings().data_dir) / "slice-previews" / str(result_id)


def hub_made(f: ModelFile) -> bool:
    """The hub's slicer made this file (as opposed to one uploaded or imported and chosen by hand)."""
    s = f.settings or {}
    return bool(s.get("slice_result_id") or s.get("plate_slice_id") or s.get("inputs_hash"))


def _in_use(db: Session, model_id: int, fid: int, skip=None) -> bool:
    """Another printer's slice of this model (candidate or approved) still uses the file."""
    skip_id = skip.id if isinstance(skip, SliceResult) else None
    for r in db.execute(select(SliceResult).where(SliceResult.model_id == model_id)).scalars():
        if r.id != skip_id and (fid in (r.file_ids or []) or fid in (r.approved_file_ids or [])):
            return True
    return False


def _retire(db: Session, model: Model, ids: list[int], owner=None, force: bool = False):
    """Remove superseded sliced files from the library and from the farm. Unless force (someone deleted the file),
    a file the hub didn't make, or one another printer still uses, is only let go of, never deleted."""
    for f in [f for f in model.files if f.id in set(ids)]:
        if not force and (not hub_made(f) or _in_use(db, model.id, f.id, owner)):
            continue
        shared = any(o.id != f.id and o.library_rel and o.library_rel == f.library_rel for o in model.files)
        try:
            if f.library_rel and not shared:      # a replacement with the same name already sits at this path
                (Path(settings().library_root) / f.library_rel).unlink(missing_ok=True)
        except OSError:
            pass
        from .shop import farm_ref
        if farm_ref(f):
            try:
                from ..clients import farm
                farm().delete_library_file(farm_ref(f))
            except Exception:  # noqa: BLE001
                log.warning("could not remove %s from the farm", f.filename)
        model.files.remove(f)
    db.flush()


def mark_failed(db: Session, model_id: int, profile_id: int, error: str):
    r = result_for(db, model_id, profile_id, create=True)
    r.error = error[:4000]
    r.state = "approved" if r.approved_file_ids and r.approved_hash == r.inputs_hash else "failed"
    if r.approved_file_ids:
        r.problems = [p for p in (r.problems or []) if p.get("code") != "failed"] + [{"code": "failed", "msg": "last re-slice failed: " + error[:300]}]


def slice_model(db: Session, model_id: int, profile_id: int, opts: dict | None = None, actor: str | None = None) -> dict:
    opts = opts or {}
    model = db.get(Model, model_id)
    prof = db.get(PrinterProfile, profile_id)
    if not model or not prof:
        raise ValueError("model or printer profile not found")
    res_row = result_for(db, model.id, prof.id)
    report = _slice_into(db, model, prof, res_row, opts, actor)
    if opts.get("approve") or opts.get("make_production"):
        report.update(approve(db, res_row, actor or "slicer"))
    elif res_row.locked:           # a locked printer keeps its file until someone approves the new one
        report["locked"] = True
    elif auto_approve_ok(db, res_row):
        report.update(approve(db, res_row, "auto-approve (barely changed)"))
        report["auto_approved"] = True
    elif cfg(db, "slice_auto_approve_clean") and not res_row.problems:
        report.update(approve(db, res_row, "auto-approve (no problems)"))
        report["auto_approved"] = True
    return report


def plate_preview_dir(plate_slice_id: int) -> Path:
    return Path(settings().data_dir) / "plate-slice-previews" / str(plate_slice_id)


def _slice_into(db: Session, model: Model, prof: PrinterProfile, res_row, opts: dict, actor: str | None) -> dict:
    """Slice model for prof and record the candidate on res_row: a SliceResult (the printer's usual slice) or a
    PlateSlice (the same printer, resliced for another plate; see services/plate_reslice.py). Approval is the
    caller's decision."""
    from ..models import PlateSlice
    is_plate = isinstance(res_row, PlateSlice)
    if "orientation" not in opts and res_row.orientation:
        opts["orientation"] = res_row.orientation
    sources = choose_sources(model, opts.get("file_id"))
    spec = build_spec(db, model, prof, opts, sources)
    warn = spec.pop("_warnings", [])
    lib = Path(settings().library_root)
    paths = [lib / f.library_rel for f in sources]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise ValueError(f"file missing from the library: {missing[0].name}")
    c = client()
    res_row.state = "slicing"
    res_row.error = None
    db.commit()

    res = c.slice(paths, spec)
    retry_problem = None
    if not res.get("ok"):
        # two fixes the slicer can make itself: a project laid out for another printer's bed gets re-arranged, and
        # a model with nothing touching the bed is laid on its largest flat face (that one needs a human look)
        err = str(res.get("error") or "")
        alt, note = None, None
        if (("over the boundary" in err or "nothing to slice" in err) and spec.get("arrange") is None
                and any(p.suffix.lower() == ".3mf" for p in paths)):
            alt, note = {"arrange": True}, "Parts re-arranged for this printer's bed (the project was laid out for another printer)"
        elif "empty first layer" in err and not spec.get("ground_largest_face") and not spec.get("ground_face_normal"):
            alt, note = ({"ground_largest_face": True, "arrange": True},
                         "Turned to rest on its largest flat face (nothing touched the bed as designed): check the orientation")
            retry_problem = {"code": "check_orientation", "msg": note}
        if alt:
            res2 = c.slice(paths, {**spec, **alt})
            if res2.get("ok"):
                c.forget(res["id"])
                res, spec = res2, {**spec, **alt}
                warn.append(note)
            else:
                c.forget(res2["id"])
                retry_problem = None
    eng = res.get("engine") or spec["engine"]
    ever = res.get("engine_version") or res.get("orca")
    spec["process"] = res.get("process") or spec["process"]
    spec["filaments"] = res.get("filaments") or spec["filaments"]
    if not res.get("ok"):
        c.forget(res["id"])
        err = str(res.get("error") or res.get("return_code"))
        if "nothing to slice" in err or "inside the print volume" in err:
            raise ValueError(f"the model doesn't fit on {prof.name}'s bed (or the plate is empty): scale it, split it, "
                             f"or slice it for a bigger printer")
        raise ValueError(f"slicing failed: {err} {res.get('log_tail', '')[-300:]}")
    files = res.get("files") or []
    layer = re.search(r"([\d.]+)mm", spec["process"] or "")
    h = inputs_hash(model, prof, opts, ever, sources)
    plate_tag = f" - {opts['bed_type']}" if is_plate and opts.get("bed_type") else ""
    nice = re.sub(r'[<>:"/\\|?*]+', "_", f"{model.code} - {prof.name}{plate_tag} - {layer.group(1) + 'mm' if layer else 'sliced'} - {h[:6]}")
    out_3mf = res.get("sliced_3mf")
    if out_3mf and out_3mf.endswith(".gcode.3mf"):
        wanted = [(out_3mf, nice + ".gcode.3mf")]
    elif res.get("sliced_file") and res["sliced_file"].endswith((".bgcode", ".gcode")):
        wanted = [(res["sliced_file"], nice + Path(res["sliced_file"]).suffix)]
    else:   # Orca for non-Bambu printers: plain G-code, one file per plate
        gfiles = sorted([f for f in files if re.match(r"plate_\d+\.(b?gcode)$", f)], key=lambda f: int(re.findall(r"\d+", f)[0]))
        many = len(gfiles) > 1
        wanted = [(g, nice + (f" - plate {re.findall(r'[0-9]+', g)[0]}" if many else "") + Path(g).suffix) for g in gfiles]
    if not wanted:
        raise ValueError("the slicer produced no printable file")

    # the previous candidate (never the approved files) makes way for this one
    stale_candidates = [i for i in (res_row.file_ids or []) if i not in set(res_row.approved_file_ids or [])]
    _retire(db, model, stale_candidates, res_row)
    new_files = []
    with tempfile.TemporaryDirectory() as td:
        for remote, local in wanted:
            dest = Path(td) / local
            c.download(res["id"], remote, dest)
            existing = {f.sha256: f for f in model.files}
            new_files.append(importer.add_model_file(db, model, dest, existing))
        pdir = plate_preview_dir(res_row.id) if is_plate else preview_dir(res_row.id)
        shutil.rmtree(pdir, ignore_errors=True)
        pdir.mkdir(parents=True, exist_ok=True)
        previews = []
        for name in res.get("previews") or []:
            if re.fullmatch(r"preview_plate_\d+\.png", name):
                try:
                    c.download(res["id"], name, pdir / name)
                    previews.append(name)
                except ApiError:
                    pass

    plates = res.get("plates") or []
    tool_of = spec.get("tool_of_color") or []
    by_slot: dict[int, float] = {}
    for p in plates:
        for f in p.get("filaments") or []:
            by_slot[int(f["slot"])] = by_slot.get(int(f["slot"]), 0) + (f.get("grams") or 0)
    copies = max(1, spec["copies"])
    tools_used = [{"slot": s, "tool": tool_of[s - 1] if s - 1 < len(tool_of) else s, "grams": round(g, 2),
                   "per_unit_g": round(g / copies, 2),
                   "preset": spec["filaments"][s - 1] if s - 1 < len(spec["filaments"]) else None}
                  for s, g in sorted(by_slot.items())]
    # what each colour was sliced as (the materials list at slice time), so the slice keeps its own filament set
    cols = [((o or {}).get("filament_colour") or [None])[0] for o in spec.get("filament_overrides") or []]
    rows = _slots(model)
    for t in tools_used:
        b = rows[t["slot"] - 1] if 0 < t["slot"] <= len(rows) else None
        t["material"] = (b.material if b else None) or spec.get("material") or "PLA"
        t["color_hex"] = (cols[t["slot"] - 1] if t["slot"] - 1 < len(cols) else None) or (b.color_hex if b else None)
        t["color_name"] = (b.color_name if b and (b.color_hex or "").upper() == (t["color_hex"] or "").upper() else None)
    stats = {"time_s": sum(p.get("time_s") or 0 for p in plates), "grams": round(sum(by_slot.values()), 2),
             "per_unit_g": round(sum(by_slot.values()) / copies, 2), "copies": copies, "plates": len(plates) or len(new_files),
             "tower_g": round(sum(p.get("tower_g") or 0 for p in plates), 2), "tools": tools_used,
             "process": spec["process"], "warnings": (res.get("warnings") or []) + warn,
             "layer_height": float(layer.group(1)) if layer else None, "opts": opts}
    for i, nf in enumerate(new_files):
        nf.printer = prof.name
        nf.slicer = f"{label(eng)} {str(ever or '').lstrip('v')}".strip()
        nf.print_seconds = stats["time_s"] if len(new_files) == 1 else (plates[i].get("time_s") if i < len(plates) else None)
        nf.plates = len(plates) if len(new_files) == 1 else 1
        nf.filament = [{"slot": t["slot"], "tool": t["tool"], "grams": round(t["grams"], 1), "preset": t["preset"]} for t in tools_used]
        nf.settings = {**(nf.settings or {}), "printer_profile_id": prof.id,
                       **({"plate_slice_id": res_row.id, "plate": opts.get("bed_type")} if is_plate
                          else {"slice_result_id": res_row.id}),
                       "engine": eng, "machine": spec["machine"], "process": spec["process"], "filaments": spec["filaments"],
                       "overrides": spec["process_overrides"], "copies": spec["copies"],
                       "plate_of": [i + 1, len(new_files)] if len(new_files) > 1 else None,
                       "sliced_at": now().isoformat(timespec="seconds"), "inputs_hash": h}
    res_row.state = "review"
    res_row.engine, res_row.engine_version = eng, ever
    res_row.inputs_hash = h
    res_row.spec = {**{k: v for k, v in spec.items() if k != "machine_config"}, "_opts": opts}
    res_row.orientation = opts.get("orientation") or {}
    res_row.stats = stats
    res_row.file_ids = [f.id for f in new_files]
    res_row.previews = previews
    res_row.problems = problems_for(model, prof) + ([retry_problem] if retry_problem else [])
    res_row.requested_by = actor
    res_row.sliced_at = now()
    db.flush()
    c.forget(res["id"])

    report = {"result_id": res_row.id, "files": [f.filename for f in new_files], "printer": prof.name, "engine": eng,
              "process": spec["process"], "copies": spec["copies"], "time_s": stats["time_s"], "grams": stats["grams"],
              "plates": stats["plates"], "tools": tools_used, "warnings": stats["warnings"], "state": "review"}
    return report


def approve(db: Session, r: SliceResult, actor: str) -> dict:
    """Make the candidate files the ones this printer uses."""
    if not r.file_ids or not (r.state == "review" or (r.state == "stale" and r.file_ids != (r.approved_file_ids or []))):
        raise ValueError("nothing new to approve: slice it first")
    model, prof = db.get(Model, r.model_id), db.get(PrinterProfile, r.profile_id)
    old = [i for i in (r.approved_file_ids or []) if i not in set(r.file_ids)]
    _retire(db, model, old, r)
    r.approved_file_ids = list(r.file_ids)
    r.approved_hash = r.inputs_hash
    r.approved_stats = dict(r.stats or {})
    r.approved_at, r.approved_by = now(), actor
    r.state = "approved"
    r.problems = [p for p in (r.problems or []) if p.get("code") != "failed"]
    out = {"approved": True}
    d = default_profile(db)
    if d and d.id == prof.id:
        for f in model.files:
            f.is_print_file = f.id == r.file_ids[0]
        out["production"] = True
        prod = model.product
        tools = (r.stats or {}).get("tools") or []
        if prod and tools:
            sync_bom(db, model, r, prof)
            where = "chosen file" if r.source == "file" else label(r.engine or "orca")
            ps = dict(model.print_settings or {})
            ps.pop("filament_estimate", None)
            ps["sliced_grams"] = f"{r.stats.get('per_unit_g')} g per unit ({where}, {prof.name}, {r.stats.get('process')})"
            model.print_settings = ps
            out["bom_updated"] = True
    db.flush()
    return out


def reject(db: Session, r: SliceResult, actor: str) -> dict:
    """Throw away the candidate; the approved files (if any) stay in use."""
    model = db.get(Model, r.model_id)
    cand = [i for i in (r.file_ids or []) if i not in set(r.approved_file_ids or [])]
    _retire(db, model, cand, r)
    r.file_ids = list(r.approved_file_ids or [])
    r.stats = dict(r.approved_stats or {})
    r.inputs_hash = r.approved_hash
    shutil.rmtree(preview_dir(r.id), ignore_errors=True)
    r.previews = []
    if not r.approved_file_ids:          # never approved: back to "not sliced"
        db.delete(r)
        db.flush()
        return {"state": "none"}
    r.state = "approved"
    r.error = None
    db.flush()
    return {"state": r.state}


KEEP_PROBLEMS = ("failed", "check_orientation")     # set by a slice or a print, not recomputed from the model


# ---------- sliced files chosen by hand, locks, removal ----------

SLICED_KINDS = ("3mf_sliced", "gcode")


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def sliced_for(f: ModelFile, prof: PrinterProfile) -> bool | None:
    """Whether a sliced file says it was made for this printer (None when the file doesn't say)."""
    a = _norm(f.printer)
    if not a:
        return None
    names = {_norm(prof.name), _norm(prof.farm_printer), _norm(prof.machine_preset)} - {""}
    return any(a == n or f" {a} " in f" {n} " or f" {n} " in f" {a} " for n in names)


def _family_of(preset: str | None) -> str | None:
    """"Bambu PLA Metal @BBL H2D" -> "PLA Metal" when that is a Bambu Lab line (None for anything else)."""
    m = re.match(r"Bambu\s+(.+?)\s*@", preset or "")
    if not m:
        return None
    try:
        from .filaments import bambu_catalog
        return m.group(1) if m.group(1) in {c["family"] for c in bambu_catalog()} else None
    except Exception:  # noqa: BLE001  (no catalog: the filament type will do)
        return None


def colour_name(family: str | None, hexc: str | None) -> str | None:
    """The Bambu Lab colour of this line with this colour code (within a small difference), else None."""
    if not family or not hexc:
        return None
    try:
        from .filaments import bambu_catalog, delta_e
        best = None
        for c in bambu_catalog():
            if c.get("family") != family or c.get("clear") or not c.get("hex"):
                continue
            d = delta_e(hexc, c["hex"])
            if best is None or d < best[0]:
                best = (d, c["name"])
        return best[1] if best and best[0] <= 5 else None
    except Exception:  # noqa: BLE001
        return None


def _hex(h: str | None) -> str | None:
    h = (h or "").strip().lstrip("#")
    return "#" + h[:6].upper() if re.fullmatch(r"[0-9a-fA-F]{6,8}", h) else None


def file_filaments(f: ModelFile) -> list[dict]:
    """The filaments a sliced file prints with, numbered 1..n in the file's order: material (the Bambu Lab line when
    the file names one), colour and grams. file_slot is the file's own filament number."""
    proj = []
    if f.library_rel and f.filename.lower().endswith(".3mf"):
        from .filaments import project_slots_of
        proj = project_slots_of(Path(settings().library_root) / f.library_rel)
    by_id = {p["slot"]: p for p in proj}
    out = []
    for i, fl in enumerate(f.filament or []):
        g = float(fl.get("grams") or 0)
        if g <= 0:
            continue
        fid = int(fl.get("slot") or i + 1)
        p = by_id.get(fid) or {}
        preset = fl.get("preset") or p.get("preset")
        fam = _family_of(preset)
        hexc = _hex(fl.get("color") or p.get("hex"))
        n = len(out) + 1
        out.append({"slot": n, "tool": n, "file_slot": fid, "grams": round(g, 2), "per_unit_g": round(g, 2),
                    "preset": preset or fl.get("type"), "material": fam or fl.get("type") or p.get("type") or "PLA",
                    "color_hex": hexc, "color_name": colour_name(fam, hexc)})
    return out


def file_stats(f: ModelFile) -> dict:
    """What the hub knows about a sliced file it didn't make, in the shape of a slice's stats."""
    tools = file_filaments(f)
    total = sum(t["grams"] for t in tools)
    s = f.settings or {}
    lh = s.get("layer_height")
    try:
        lh = float(lh) if lh not in (None, "") else None
    except (TypeError, ValueError):
        lh = None
    return {"time_s": f.print_seconds or 0, "grams": round(total, 2), "per_unit_g": round(total, 2), "copies": 1,
            "plates": f.plates or 1, "tower_g": 0, "tools": tools, "warnings": [], "layer_height": lh,
            "process": s.get("process") or s.get("print_settings_id") or f"{f.slicer or 'Sliced file'}: {f.filename}",
            "file": f.filename}


def previews_from_file(r: SliceResult, f: ModelFile) -> list[str]:
    """The plate pictures inside a sliced 3MF (Metadata/plate_N.png), for the Print tab."""
    import zipfile
    d = preview_dir(r.id)
    shutil.rmtree(d, ignore_errors=True)
    if not f.library_rel or not f.filename.lower().endswith(".3mf"):
        return []
    out = []
    try:
        with zipfile.ZipFile(Path(settings().library_root) / f.library_rel) as z:
            for n in sorted(z.namelist()):
                m = re.fullmatch(r"Metadata/plate_(\d+)\.png", n)
                if not m or z.getinfo(n).file_size > 8_000_000 or len(out) >= 12:
                    continue
                d.mkdir(parents=True, exist_ok=True)
                name = f"preview_plate_{int(m.group(1))}.png"
                (d / name).write_bytes(z.read(n))
                out.append(name)
    except (OSError, zipfile.BadZipFile, KeyError):
        pass
    return out


def use_file(db: Session, model: Model, prof: PrinterProfile, f: ModelFile, actor: str, lock: bool = True) -> dict:
    """Make one of the model's sliced files the one this printer prints (approved at once; for the default printer
    it becomes the production file). Locked by default, so re-slicing never replaces it. Raises ValueError for
    what the person can fix."""
    if f.model_id != model.id:
        raise ValueError("that file belongs to another model")
    if f.kind not in SLICED_KINDS:
        raise ValueError(f"{f.filename} isn't sliced: only a sliced .3mf or .gcode file can print")
    r = result_for(db, model.id, prof.id)
    waiting = [i for i in (r.file_ids or []) if i not in set(r.approved_file_ids or []) and i != f.id]
    _retire(db, model, waiting, r)        # a slice waiting for review makes way too
    r.file_ids = [f.id]
    r.stats = file_stats(f)
    r.inputs_hash = None
    r.spec = {"file_id": f.id}
    r.orientation = {}
    r.engine_version = None
    r.error = None
    r.problems = problems_for(model, prof)
    r.requested_by, r.sliced_at = actor, now()
    r.previews = previews_from_file(r, f)
    r.state = "review"
    if r.approved_file_ids == [f.id]:     # the same file again: only its lock changes
        r.approved_file_ids = []
    r.source, r.locked = "file", bool(lock)
    out = approve(db, r, actor)
    warn = []
    if sliced_for(f, prof) is False:
        warn.append(f"{f.filename} says it was sliced for {f.printer}, not {prof.name}")
    db.flush()
    return {**out, "result_id": r.id, "printer": prof.name, "file": f.filename, "locked": r.locked, "warnings": warn}


def production_result(db: Session, model: Model) -> SliceResult | None:
    """The default printer's approved slice: what store orders print."""
    d = default_profile(db)
    r = result_for(db, model.id, d.id, create=False) if d else None
    return r if r is not None and r.approved_file_ids else None


def filament_set(db: Session, r: SliceResult | None, model: Model) -> list[dict]:
    """What a printer's approved slice prints, colour by colour: slot, material, colour, grams per unit."""
    if r is None or not r.approved_file_ids:
        return []
    if r.source == "file":
        f = next((x for x in model.files if x.id == r.approved_file_ids[0]), None)
        if f is not None:
            return file_filaments(f)
    tools = (r.approved_stats or {}).get("tools") or []
    rows = _slots(model)
    cols = []
    if r.file_ids == r.approved_file_ids:    # slices from before tools kept their colour: the spec they were sliced with
        cols = [((o or {}).get("filament_colour") or [None])[0] for o in (r.spec or {}).get("filament_overrides") or []]
    out = []
    for t in tools:
        s = int(t.get("slot") or len(out) + 1)
        b = rows[s - 1] if 0 < s <= len(rows) else None
        hexc = _hex(t.get("color_hex") or (cols[s - 1] if 0 < s <= len(cols) else None) or (b.color_hex if b else None))
        mat = t.get("material") or _family_of(t.get("preset")) or (b.material if b else None) or material_type(t.get("preset"))
        name = t.get("color_name") or (b.color_name if b and _hex(b.color_hex) == hexc else None) or colour_name(mat, hexc)
        g = t.get("per_unit_g") if t.get("per_unit_g") is not None else t.get("grams")
        out.append({"slot": s, "tool": t.get("tool") or s, "grams": t.get("grams"), "per_unit_g": round(float(g or 0), 2),
                    "preset": t.get("preset"), "material": mat, "color_hex": hexc, "color_name": name})
    return out


def sync_bom(db: Session, model: Model, r: SliceResult, prof: PrinterProfile | None = None) -> bool:
    """The production slice's filament set becomes the materials list (filament per unit), so supply and the number
    that can be built follow what is really printed. A file chosen by hand sets the colours and materials too, and
    colours it doesn't print leave the list. A slice made here was sliced from the list's colours, so only the grams
    change (and a colour it printed that the list lacks is added). True when the list changed."""
    prod = model.product
    fs = filament_set(db, r, model)
    if prod is None or not fs:
        return False
    prof = prof or db.get(PrinterProfile, r.profile_id)
    from_file = r.source == "file"
    before = None
    if not from_file and prof is not None and r.approved_hash:
        before = inputs_hash(model, prof, (r.approved_stats or {}).get("opts") or {}, r.engine_version)
    by_slot = {b.slot: b for b in prod.filaments}
    changed, keep = False, set()
    for t in fs:
        keep.add(t["slot"])
        b = by_slot.get(t["slot"])
        if b is None:
            b = BomFilament(slot=t["slot"], label=f"Color {t['slot']}", material=t["material"] or "PLA", waste_pct=5)
            prod.filaments.append(b)
            by_slot[t["slot"]] = b
            changed = True
        want = {"grams": t["per_unit_g"]}
        if not b.follows_variant and (from_file or not b.color_hex):
            want["material"] = t["material"] or b.material or "PLA"
            want["color_hex"] = t["color_hex"] or b.color_hex
            want["color_name"] = t["color_name"] or (b.color_name if _hex(b.color_hex) == t["color_hex"] else None)
        for k, v in want.items():
            if getattr(b, k) != v:
                if k in ("material", "color_hex") and getattr(b, k):
                    b.spoolman_filament_id = None      # a spool picked for the old colour no longer applies
                setattr(b, k, v)
                changed = True
    if from_file:
        for b in [x for x in prod.filaments if x.slot not in keep]:
            prod.filaments.remove(b)
            changed = True
    db.flush()
    if before is not None and changed and r.approved_hash == before:
        # the slice was made from this list; its own sync (grams, colours it already printed) doesn't make it stale
        after = inputs_hash(model, prof, (r.approved_stats or {}).get("opts") or {}, r.engine_version)
        if r.inputs_hash == r.approved_hash:
            r.inputs_hash = after
        r.approved_hash = after
    return changed


def refresh_file_result(db: Session, r: SliceResult, model: Model) -> bool:
    """A chosen file's slice re-reads its file (colours, materials, grams); True when it changed."""
    if r.source != "file" or not r.approved_file_ids:
        return False
    f = next((x for x in model.files if x.id == r.approved_file_ids[0]), None)
    if f is None:
        return False
    st = file_stats(f)
    if st == (r.approved_stats or {}):
        return False
    r.approved_stats = dict(st)
    if r.file_ids == r.approved_file_ids:
        r.stats = dict(st)
    db.flush()
    return True


def set_lock(db: Session, r: SliceResult, locked: bool) -> dict:
    """Lock a printer's approved slice as it is, or let re-slicing look after it again."""
    if locked and not r.approved_file_ids:
        raise ValueError("approve a slice for this printer before locking it")
    r.locked = bool(locked)
    if r.locked and r.state == "stale":
        r.state = "approved" if r.file_ids == r.approved_file_ids else "review"
    db.flush()
    if not r.locked:
        check_stale(db, profile_id=r.profile_id, model_id=r.model_id)
    return {"locked": bool(r.locked), "state": r.state}


def remove_result(db: Session, r: SliceResult) -> dict:
    """Take a printer's slice away: its files go (files the hub didn't make stay in the model's files), and the
    printer is back to "not sliced"."""
    model, prof = db.get(Model, r.model_id), db.get(PrinterProfile, r.profile_id)
    ids = list(dict.fromkeys((r.file_ids or []) + (r.approved_file_ids or [])))
    d = default_profile(db)
    production = False
    if d and prof and d.id == prof.id:
        for f in model.files:
            if f.id in ids and f.is_print_file:
                f.is_print_file, production = False, True
    _retire(db, model, ids, r)
    shutil.rmtree(preview_dir(r.id), ignore_errors=True)
    db.delete(r)
    db.flush()
    return {"state": "none", "printer": prof.name if prof else None, "production_cleared": production}


def _unlink_file(db: Session, model: Model, fid: int) -> list[str]:
    """Take a file out of every printer's slice of the model; the printers it touched."""
    touched = []
    for r in list(db.execute(select(SliceResult).where(SliceResult.model_id == model.id)).scalars()):
        if fid not in (r.file_ids or []) and fid not in (r.approved_file_ids or []):
            continue
        prof = db.get(PrinterProfile, r.profile_id)
        touched.append(prof.name if prof else str(r.profile_id))
        r.file_ids = [i for i in (r.file_ids or []) if i != fid]
        r.approved_file_ids = [i for i in (r.approved_file_ids or []) if i != fid]
        if not r.approved_file_ids:
            r.approved_stats, r.approved_hash, r.approved_at, r.approved_by, r.locked = {}, None, None, None, False
            if r.file_ids:
                r.state = "review"
            else:
                shutil.rmtree(preview_dir(r.id), ignore_errors=True)
                db.delete(r)
        elif not r.file_ids:
            r.file_ids, r.stats, r.inputs_hash = list(r.approved_file_ids), dict(r.approved_stats or {}), r.approved_hash
            r.state = "approved"
    db.flush()
    return touched


def delete_file(db: Session, f: ModelFile) -> dict:
    """Delete one of a model's files for good: from the library, the farm and every printer's slice."""
    model, name, production = f.model, f.filename, bool(f.is_print_file)
    touched = _unlink_file(db, model, f.id)
    _retire(db, model, [f.id], force=True)
    return {"deleted": name, "printers": touched, "was_production": production}


def replace_file(db: Session, old: ModelFile, new: ModelFile, actor: str) -> dict:
    """A new version of a file takes the old one's place: production, and every printer that printed it (those
    stay locked to the new file, since someone chose it by hand). The old file is deleted."""
    model = old.model
    if new.id == old.id:
        raise ValueError("that is the same file")
    users = [r for r in db.execute(select(SliceResult).where(SliceResult.model_id == model.id)).scalars()
             if old.id in (r.file_ids or []) or old.id in (r.approved_file_ids or [])]
    if users and new.kind not in SLICED_KINDS:
        raise ValueError(f"{old.filename} is printed by {len(users)} printer(s); its replacement must be sliced too")
    printers, warn, out = [], [], {}
    for r in users:
        prof = db.get(PrinterProfile, r.profile_id)
        if (r.approved_file_ids or []) == [old.id]:
            res = use_file(db, model, prof, new, actor, lock=True)
            warn += res.get("warnings") or []
            out.update({k: v for k, v in res.items() if k in ("production", "bom_updated")})
        else:                              # a plate of a multi-plate slice, or a candidate: swap in place
            r.file_ids = [new.id if i == old.id else i for i in (r.file_ids or [])]
            r.approved_file_ids = [new.id if i == old.id else i for i in (r.approved_file_ids or [])]
            r.locked, r.source = True, "file"
        printers.append(prof.name if prof else str(r.profile_id))
    if old.is_print_file and new.kind in SLICED_KINDS:
        new.is_print_file = True
        out["production"] = True
    _retire(db, model, [old.id], force=True)
    return {**out, "replaced": old.filename, "file": new.filename, "file_id": new.id, "printers": printers, "warnings": warn}


def check_stale(db: Session, profile_id: int | None = None, model_id: int | None = None) -> dict:
    """Mark results whose inputs changed (files, printer, designer settings, colours, slicer version)."""
    q = select(SliceResult).where(SliceResult.state.in_(("review", "approved", "stale")), SliceResult.locked.isnot(True))
    if profile_id:
        q = q.where(SliceResult.profile_id == profile_id)
    if model_id:
        q = q.where(SliceResult.model_id == model_id)
    versions: dict = {}
    n_stale = n_fresh = 0
    for r in db.execute(q).scalars():
        model, prof = db.get(Model, r.model_id), db.get(PrinterProfile, r.profile_id)
        if not model or not prof:
            continue
        eng = engine_of(prof)
        if eng not in versions:
            v = engine_version(prof)
            if v is None:
                return {"skipped": "slicer not reachable"}
            versions[eng] = v
        use_approved = r.state in ("approved", "stale") and bool(r.approved_file_ids) and r.file_ids == r.approved_file_ids
        st = (r.approved_stats if use_approved else r.stats) or {}
        cur = inputs_hash(model, prof, (st["opts"] if "opts" in st else (r.spec or {}).get("_opts")) or {}, versions[eng])
        ref = r.approved_hash if use_approved else r.inputs_hash
        if cur != ref and r.state != "stale":
            r.state = "stale"
            n_stale += 1
        elif cur == ref and r.state == "stale":
            r.state = "approved" if r.approved_file_ids and r.approved_hash == cur else "review"
            n_fresh += 1
        r.problems = [p for p in problems_for(model, prof)] + [p for p in (r.problems or []) if p.get("code") in KEEP_PROBLEMS]
    db.flush()
    return {"stale": n_stale, "fresh": n_fresh}


# ---------- inspection ----------

def _paint_colors(data) -> int:
    """Distinct painted colour states in Orca's --inspect-paint output (0 when unpainted)."""
    seen = set()

    def walk(x, in_mmu=False):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "mmu_segmentation":
                    walk(v, True)
                elif in_mmu and k in ("states", "extruders", "colors", "colours", "used_states") and isinstance(v, (list, dict)):
                    for s in (v.keys() if isinstance(v, dict) else v):
                        try:
                            n = int(s if not isinstance(s, dict) else s.get("state", s.get("extruder")))
                            if n > 0:
                                seen.add(n)
                        except (TypeError, ValueError):
                            pass
                else:
                    walk(v, in_mmu)
        elif isinstance(x, list):
            for v in x:
                walk(v, in_mmu)
    walk(data)
    return len(seen)


def inspect_model(db: Session, model_id: int) -> dict:
    """Ask the slicer how many colours the model is painted with and which faces it can rest on."""
    model = db.get(Model, model_id)
    if not model:
        raise ValueError("model not found")
    sources = choose_sources(model)
    lib = Path(settings().library_root)
    paths = [lib / f.library_rel for f in sources]
    c = client()
    out = dict(model.inspect or {})
    project_colors = 0
    for f in sources:
        if f.filename.lower().endswith(".3mf"):
            project_colors = max(project_colors, len([x for x in (f.filament or []) if x]))
    try:
        paint = c.inspect(paths, "paint")
        out["painted_colors"] = _paint_colors(paint)
        out["painted_facets"] = (paint.get("summary") or {}).get("painted_facets_total")
    except ApiError as e:
        out["paint_error"] = e.message[:300]
    try:
        mesh = c.inspect(paths, "mesh")
        faces = []
        for o in mesh.get("objects") or []:
            for pl in sorted(o.get("planes") or [], key=lambda p: -(p.get("area_mm2") or 0))[:6]:
                faces.append({"object": o.get("name"), "normal": [round(v, 4) for v in pl.get("normal") or []],
                              "area_mm2": round(pl.get("area_mm2") or 0, 1)})
            out["size_mm"] = (o.get("bbox_object") or {}).get("size")
        out["faces"] = faces[:12]
    except ApiError as e:
        out["mesh_error"] = e.message[:300]
    out["project_colors"] = project_colors
    out["inspected_at"] = now().isoformat(timespec="seconds")
    model.inspect = out
    model.colors_needed = max(out.get("painted_colors") or 0, project_colors) or None
    db.flush()
    return {"colors_needed": model.colors_needed, "faces": len(out.get("faces") or [])}


# ---------- team settings for the slicing workflow ----------

SETTINGS = {
    "slice_auto_new": "default",         # off | default | all: slice newly imported models for these printers
    "slice_nightly": True,               # re-slice out-of-date results overnight
    "slice_night_hour": 2,               # local hour the nightly run starts
    "timezone": "UTC",                   # the team's time zone (IANA name)
    "filament_brand": None,              # "Bambu Lab": replace requested filaments with the closest Bambu Lab colour
    "slice_paused": False,               # hold every slice job for this team
    "slice_auto_approve_clean": False,   # approve every slice that finishes without problems
    "slice_auto_approve": False,         # approve re-slices that barely changed
    "slice_auto_approve_pct": 10,        # ... when time and grams are within this many percent of the approved slice
    "require_approved_slice": True,      # no going live on the store without an approved file for the default printer
    "material_prices": {},               # {"PLA": 20.0} per kg, when Spoolman has no price for the filament
    "default_material_price": 20.0,      # per kg
    "printer_hourly_rate": 0.0,          # machine time per hour when a printer has no rate of its own
}


def cfg(db: Session, key: str):
    from ..kv import setting
    return setting(db, key, SETTINGS[key])


def drop_pending(db: Session, model_id: int, profile_id: int):
    """A queued slice that won't run: back to its approved files, or gone when it never had any."""
    r = result_for(db, model_id, profile_id, create=False)
    if r is None or r.state not in ("queued", "slicing"):
        return
    if r.approved_file_ids:
        r.state, r.file_ids, r.stats, r.inputs_hash = "approved", list(r.approved_file_ids), dict(r.approved_stats or {}), r.approved_hash
    else:
        db.delete(r)
    db.flush()


def filaments_used(db: Session, model: Model) -> int:
    """How many filaments the print really uses: the default printer's approved slice when there is one (filaments
    with weight), else the colours painted or assigned in the designer's project; plain model files print in one."""
    d = default_profile(db)
    if d:
        r = result_for(db, model.id, d.id, create=False)
        st = (r.approved_stats if r and r.approved_file_ids else None) or (r.stats if r else None)
        if st and st.get("tools"):
            return max(1, len({t.get("slot") for t in st["tools"] if (t.get("grams") or 0) > 0}))
    return max(1, model.colors_needed or 1)


def skips_on(db: Session, model: Model, prof: PrinterProfile) -> str | None:
    """Why this model isn't sliced for this printer (None when it is)."""
    if skips_slicing(model):
        return "slicing is turned off for this model"
    if filament_capacity(prof) == 1 and filaments_used(db, model) > 1:
        # One-filament printers (Voron, Prusas) only get single-colour models
        return f"multi-colour model, and {prof.name} holds one filament"
    return None


def skips_slicing(model: Model) -> bool:
    """The team chose not to slice this model (it fits none of its printers, or it isn't printed in house)."""
    return bool((model.extra or {}).get("skip_slicing"))


def after_import(db: Session, model: Model):
    """A model was imported or its files changed: inspect it, and slice it if the team wants that."""
    from ..clients import slicer_configured
    from ..jobs import enqueue
    if model.files:      # record what the designer asked for; with a team brand, choose that brand's colours
        try:
            from . import filaments
            brand = cfg(db, "filament_brand")
            filaments.apply_brand(db, model, brand) if brand else filaments.document(db, model)
        except Exception as e:  # noqa: BLE001  (a catalog that can't be fetched must not block an import)
            log.warning("filament matching for %s failed: %s", model.code, e)
    if not model.files or not slicer_configured() or skips_slicing(model):
        return
    if not (model.inspect or {}).get("inspected_at"):
        enqueue(db, "slice.inspect", ref=f"model:{model.id}:inspect", payload={"model_id": model.id}, delay=20, priority=8)
    mode = cfg(db, "slice_auto_new")
    if mode not in ("default", "all"):
        return
    try:
        choose_sources(model)
    except ValueError:
        return
    if mode == "default":
        d = default_profile(db)
        profs = [d] if d else []
    else:
        profs = list(db.execute(select(PrinterProfile).where(PrinterProfile.active.is_(True))).scalars())
    for p in profs:
        if result_for(db, model.id, p.id, create=False) or (p.id != (default_profile(db) or p).id and skips_on(db, model, p)):
            continue
        r = result_for(db, model.id, p.id)
        j = enqueue(db, "slice.model", ref=f"model:{model.id}:slice:{p.id}",
                    payload={"model_id": model.id, "profile_id": p.id, "opts": {}}, actor="auto-slice",
                    max_attempts=2, priority=8, delay=60)
        r.job_id = j.id


def _close(a, b, pct: float) -> bool:
    if not a or not b:
        return a == b
    return abs(a - b) / max(abs(b), 1e-9) * 100 <= pct


def auto_approve_ok(db: Session, r: SliceResult) -> bool:
    """A re-slice that changed little (time and grams within the team's tolerance, no new problems) needs no review."""
    if not cfg(db, "slice_auto_approve") or not r.approved_file_ids or r.problems:
        return False
    pct = float(cfg(db, "slice_auto_approve_pct") or 0)
    new, old = r.stats or {}, r.approved_stats or {}
    return _close(new.get("time_s"), old.get("time_s"), pct) and _close(new.get("per_unit_g"), old.get("per_unit_g"), pct)


def nightly(db: Session, force: bool = False) -> dict:
    """Once a night in the team's time zone: find what went out of date and re-slice it."""
    from zoneinfo import ZoneInfo
    from ..kv import set_setting
    from ..jobs import enqueue
    if not cfg(db, "slice_nightly") and not force:
        return {"skipped": "nightly re-slicing is off"}
    try:
        tz = ZoneInfo(cfg(db, "timezone") or "UTC")
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")
    local = now().astimezone(tz)
    today = local.date().isoformat()
    if not force and (local.hour != int(cfg(db, "slice_night_hour") or 0) or cfg_last(db) == today):
        return {"skipped": "not now"}
    set_setting(db, "slice_nightly_last", today)
    slots = sync_printer_slots(db)
    found = check_stale(db)
    n = 0
    off = {p.id for p in db.execute(select(PrinterProfile).where(PrinterProfile.active.is_(False))).scalars()}
    for r in db.execute(select(SliceResult).where(SliceResult.state == "stale").limit(2000)).scalars():
        if r.profile_id in off or r.locked:     # a printer turned off for slicing, or a locked slice, keeps what it has
            continue
        m_, p_ = db.get(Model, r.model_id), db.get(PrinterProfile, r.profile_id)
        if m_ and p_ and skips_on(db, m_, p_):
            continue
        opts = dict(((r.approved_stats if r.file_ids == r.approved_file_ids else r.stats) or {}).get("opts") or {})
        opts.pop("approve", None)
        r.state = "queued"
        j = enqueue(db, "slice.model", ref=f"model:{r.model_id}:slice:{r.profile_id}",
                    payload={"model_id": r.model_id, "profile_id": r.profile_id, "opts": opts}, actor="nightly",
                    max_attempts=2, priority=8)
        r.job_id = j.id
        n += 1
    return {"checked": found, "requeued": n, "printer_slots": slots}


def sync_printer_slots(db: Session) -> dict:
    """Filament slots per printer from the farm: every AMS, AMS HT, external spool or tool slot it has reported."""
    from .. import clients
    try:
        fc = clients.farm()
        printers = fc.printers_full() or []
        assigned = (fc.filament_assignments() or {}).get("printers") or {}
    except Exception as e:  # noqa: BLE001
        return {"skipped": f"The farm is not reachable: {str(e)[:120]}"}
    by_name = {p.get("name"): p for p in printers if isinstance(p, dict)}
    changed = {}
    for prof in db.execute(select(PrinterProfile)).scalars():
        p = by_name.get(prof.farm_printer or prof.name)
        if not p:
            continue
        slots = {(a.get("slot_type"), a.get("system_index"), a.get("slot_index"), a.get("extruder_id"))
                 for a in (assigned.get(p.get("id")) or {}).get("assignments") or []}
        cap = p.get("capabilities") or {}
        n = len(slots) or ((cap.get("num_mf_systems") or 0) * (cap.get("slots_per_mf_system") or 0)
                           + (cap.get("num_external_spools") or 0)) or None
        if n and n != prof.filament_slots:
            changed[prof.name] = [prof.filament_slots, n]
            prof.filament_slots = n
    db.flush()
    return {"changed": changed}


def cfg_last(db: Session):
    from ..kv import setting
    return setting(db, "slice_nightly_last")


# ---------- cost per unit ----------

def price_per_kg(db: Session, material: str | None, spoolman_filament_id: int | None = None) -> tuple[float, str]:
    from ..models import FilamentCatalog
    if spoolman_filament_id:
        f = db.execute(select(FilamentCatalog).where(FilamentCatalog.filament_id == spoolman_filament_id)).scalar()
        if f and f.price and f.weight_g:
            return f.price / f.weight_g * 1000, "spoolman"
    prices = {k.upper(): v for k, v in (cfg(db, "material_prices") or {}).items()}
    mat = (material or "PLA").upper()
    if mat in prices:
        return float(prices[mat]), "team price"
    if material_type(mat) in prices:
        return float(prices[material_type(mat)]), "team price"
    return float(cfg(db, "default_material_price") or 0), "default price"


def cost_of(db: Session, model: Model, prof: PrinterProfile, stats: dict) -> dict | None:
    """Material and machine cost of one unit from a slice's numbers."""
    if not stats or not (stats.get("tools") or stats.get("grams")):
        return None
    copies = max(1, int(stats.get("copies") or 1))
    slots = {b.slot: b for b in _slots(model)}
    material = 0.0
    sources = set()
    for t in stats.get("tools") or []:
        b = slots.get(t.get("slot"))
        ppk, src = price_per_kg(db, b.material if b else (stats.get("material") or "PLA"), b.spoolman_filament_id if b else None)
        sources.add(src)
        waste = (b.waste_pct if b and b.waste_pct is not None else 0) / 100
        g = t.get("per_unit_g") if t.get("per_unit_g") is not None else (t.get("grams") or 0) / copies
        material += g * (1 + waste) * ppk / 1000
    if not stats.get("tools") and stats.get("grams"):
        ppk, src = price_per_kg(db, "PLA")
        sources.add(src)
        material += (stats.get("per_unit_g") or stats["grams"] / copies) * ppk / 1000
    if stats.get("tower_g"):
        b = slots.get(1)
        ppk, _ = price_per_kg(db, b.material if b else "PLA", b.spoolman_filament_id if b else None)
        material += stats["tower_g"] / copies * ppk / 1000
    rate = prof.hourly_rate if prof.hourly_rate is not None else float(cfg(db, "printer_hourly_rate") or 0)
    machine = (stats.get("time_s") or 0) / 3600 * rate / copies
    return {"material": round(material, 2), "machine": round(machine, 2), "total": round(material + machine, 2),
            "rate": rate, "prices": sorted(sources)}


# ---------- real prints ----------

def record_print(db: Session, r: SliceResult | None, model_id: int, outcome: str, *, source: str = "manual",
                 external_id: str | None = None, printer_name: str | None = None, duration_s: int | None = None,
                 note: str | None = None, actor: str | None = None, file_id: int | None = None,
                 printed_at: datetime | None = None):
    from ..models import PrintRecord
    if outcome not in ("ok", "failed", "cancelled"):
        raise ValueError("outcome is ok, failed or cancelled")
    if external_id and db.execute(select(PrintRecord).where(PrintRecord.source == source,
                                                            PrintRecord.external_id == external_id)).first():
        return None
    rec = PrintRecord(result_id=r.id if r else None, model_id=model_id, profile_id=r.profile_id if r else None,
                      file_id=file_id or ((r.approved_file_ids or r.file_ids or [None])[0] if r else None),
                      inputs_hash=(r.approved_hash or r.inputs_hash) if r else None, outcome=outcome, source=source,
                      external_id=external_id, printer_name=printer_name, duration_s=duration_s,
                      note=(note or "")[:2000] or None, created_by=actor, printed_at=printed_at or now())
    db.add(rec)
    db.flush()
    if r:
        refresh_print_problems(db, r)
    return rec


def print_summary(db: Session, r: SliceResult) -> dict:
    """How the approved version of this slice has done on real printers."""
    from ..models import PrintRecord
    recs = list(db.execute(select(PrintRecord).where(PrintRecord.result_id == r.id)
                           .order_by(PrintRecord.printed_at.desc()).limit(50)).scalars())
    cur = [x for x in recs if x.inputs_hash and x.inputs_hash == (r.approved_hash or r.inputs_hash)]
    ok = sum(1 for x in cur if x.outcome == "ok")
    failed = sum(1 for x in cur if x.outcome == "failed")
    durs = [x.duration_s for x in cur if x.outcome == "ok" and x.duration_s]
    return {"ok": ok, "failed": failed, "verified": ok > 0,
            "avg_duration_s": round(sum(durs) / len(durs)) if durs else None,
            "recent": [{"id": x.id, "outcome": x.outcome, "source": x.source, "printer": x.printer_name,
                        "duration_s": x.duration_s, "note": x.note, "by": x.created_by,
                        "at": x.printed_at.isoformat() if x.printed_at else None,
                        "current": x in cur} for x in recs[:8]]}


def refresh_print_problems(db: Session, r: SliceResult):
    s = print_summary(db, r)
    probs = [p for p in (r.problems or []) if p.get("code") != "prints"]
    recent = [x for x in s["recent"] if x["current"]]
    if len(recent) >= 2 and all(x["outcome"] == "failed" for x in recent[:2]):
        probs.append({"code": "prints", "msg": "the last two prints of this slice failed"})
    r.problems = probs


def match_farm_job(db: Session, job_name: str | None, printer: str | None) -> tuple[SliceResult | None, int | None, int | None]:
    """Which slice a print-farm job printed: by the sliced file's name in the job name, else by SKU on the default printer."""
    from ..models import Product, Variant
    name = (job_name or "").lower()
    if not name:
        return None, None, None
    for f in db.execute(select(ModelFile).where(ModelFile.kind.in_(("3mf_sliced", "gcode")))).scalars():
        stem = f.filename.lower()
        for suf in (".gcode.3mf", ".bgcode", ".gcode", ".3mf"):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
                break
        if stem and stem in name:
            rid = (f.settings or {}).get("slice_result_id")
            r = db.get(SliceResult, rid) if rid else None
            if not r and (f.settings or {}).get("printer_profile_id"):
                r = result_for(db, f.model_id, f.settings["printer_profile_id"], create=False)
            return r, f.model_id, f.id
    for token in re.split(r"[\s_]+", job_name or ""):
        prod = db.execute(select(Product).where(func_lower(Product.sku) == token.lower())).scalar()
        if not prod:
            v = db.execute(select(Variant).where(func_lower(Variant.sku) == token.lower())).scalar()
            prod = v.product if v else None
        if prod:
            d = default_profile(db)
            r = result_for(db, prod.model_id, d.id, create=False) if d else None
            return r, prod.model_id, None
    return None, None, None


def func_lower(col):
    from sqlalchemy import func
    return func.lower(col)
