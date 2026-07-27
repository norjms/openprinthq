#!/usr/bin/env python3
"""Deterministically extract the OrcaSlicer printer-model list + capability flags.

This is the REPEATABLE step for "OrcaSlicer added new printers": point it at a
fresh OrcaSlicer checkout's profiles dir and it regenerates orca_models.csv.
No agent, no guessing — it reads resources/profiles/<Vendor>/machine/*.json,
resolves `inherits` chains, and derives per-model capability flags.

Usage:
    python3 extract_models.py /path/to/OrcaSlicer/resources/profiles > orca_models.csv

Columns: vendor,model_or_profile_name,gcode_flavor,nozzle_count,has_chamber_heater,notes
One row per distinct printer_model (capabilities = max/any across its variants).
"""
import sys, os, json, glob, csv, io

GCODE_FIELDS = ["machine_start_gcode","machine_end_gcode","before_layer_change_gcode",
                "layer_change_gcode","change_filament_gcode","machine_pause_gcode",
                "template_custom_gcode","time_lapse_gcode"]

def load(path):
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except Exception: return None

def build_index(profiles_dir):
    """Per-vendor name->data (OrcaSlicer resolves `inherits` WITHIN a vendor). A base
    like `fdm_machine_common` exists in many vendors with different flavors, so a global
    index would cross-contaminate. Returns {vendor: {name: data}}."""
    idx = {}
    for vend in sorted(os.listdir(profiles_dir)):
        mdir = os.path.join(profiles_dir, vend, "machine")
        if not os.path.isdir(mdir): continue
        vmap = {}
        for fp in glob.glob(os.path.join(mdir, "**", "*.json"), recursive=True):
            d = load(fp)
            if not isinstance(d, dict): continue
            nm = d.get("name")
            if nm: vmap[nm] = d
        idx[vend] = vmap
    # global fallback: only names that are UNIQUE across all vendors (so a shared base
    # can still resolve, without re-introducing the per-vendor collision).
    from collections import Counter
    name_counts = Counter(nm for vmap in idx.values() for nm in vmap)
    gmap = {}
    for vmap in idx.values():
        for nm, d in vmap.items():
            if name_counts[nm] == 1: gmap[nm] = d
    return idx, gmap

def resolve(field, data, vmap, gmap, seen=None):
    """Field value following `inherits`: vendor map first, then globally-unique bases."""
    seen = seen or set()
    if field in data and data[field] not in (None, "", []):
        return data[field]
    par = data.get("inherits")
    if par and par not in seen:
        seen.add(par)
        nxt = vmap.get(par) or gmap.get(par)
        if nxt is not None:
            return resolve(field, nxt, vmap, gmap, seen)
    return None

def resolve_gcode_blob(data, vmap, gmap):
    """Concatenate all resolved gcode fields (to scan for M141)."""
    parts = []
    for fld in GCODE_FIELDS:
        v = resolve(fld, data, vmap, gmap)
        if isinstance(v, str): parts.append(v)
        elif isinstance(v, list): parts.append("\n".join(str(x) for x in v))
    return "\n".join(parts)

def main(profiles_dir):
    idx, gmap = build_index(profiles_dir)
    models = {}  # printer_model -> dict(vendor, flavor, nozzle, chamber, notes)
    for vend in sorted(os.listdir(profiles_dir)):
        mdir = os.path.join(profiles_dir, vend, "machine")
        if not os.path.isdir(mdir): continue
        vmap = idx[vend]
        for fp in glob.glob(os.path.join(mdir, "**", "*.json"), recursive=True):
            d = load(fp)
            if not isinstance(d, dict): continue
            # instantiable printer variant = has a printer_model and isn't a non-instantiated base
            if str(d.get("instantiation", "true")).lower() == "false": continue
            pm = resolve("printer_model", d, vmap, gmap)
            if not pm: continue
            flavor = (resolve("gcode_flavor", d, vmap, gmap) or "unknown")
            nd = resolve("nozzle_diameter", d, vmap, gmap)
            nozzle = len(nd) if isinstance(nd, list) and nd else 1
            # chamber heater: explicit control flag OR M141 present in resolved gcode
            chamber = str(resolve("support_chamber_temp_control", d, vmap, gmap) or "0") == "1"
            if not chamber and "M141" in resolve_gcode_blob(d, vmap, gmap):
                chamber = True
            aux = str(resolve("auxiliary_fan", d, vmap, gmap) or "0") == "1"
            m = models.setdefault(pm, {"vendor": vend, "flavor": set(), "nozzle": 1,
                                       "chamber": False, "aux": False})
            m["vendor"] = vend
            m["flavor"].add(flavor)
            m["nozzle"] = max(m["nozzle"], nozzle)
            m["chamber"] = m["chamber"] or chamber
            m["aux"] = m["aux"] or aux

    out = io.StringIO()
    wtr = csv.writer(out)
    wtr.writerow(["vendor","model_or_profile_name","gcode_flavor","nozzle_count",
                  "has_chamber_heater","notes"])
    for pm in sorted(models, key=lambda k: (models[k]["vendor"], k)):
        m = models[pm]
        flavors = "|".join(sorted(f for f in m["flavor"] if f != "unknown")) or "unknown"
        notes = []
        if m["nozzle"] > 1: notes.append(f"multi-nozzle({m['nozzle']})")
        if m["aux"]: notes.append("aux-fan")
        if m["vendor"] == "BBL": notes.append("MQTT")
        wtr.writerow([m["vendor"], pm, flavors, m["nozzle"],
                      "true" if m["chamber"] else "false", "; ".join(notes)])
    sys.stdout.write(out.getvalue())
    sys.stderr.write(f"[extract_models] {len(models)} distinct printer_models, "
                     f"{sum(1 for k in models if models[k]['chamber'])} chamber, "
                     f"{sum(1 for k in models if models[k]['nozzle']>1)} multi-nozzle\n")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: extract_models.py /path/to/OrcaSlicer/resources/profiles")
    main(sys.argv[1])
