# OpenPrintHQ — Control-Command Framework: Specification

**Status:** deployed to dev (`tenant_norjms`), schema + data only (no execution feature).
**Version:** 1.0 · **Source basis:** OrcaSlicer nightly `2.5.0-dev` (commit `04e13200`).
**Repo:** `openprinthq/db/control-framework/`.

---

## 1. Purpose & scope

Let the frontend send **one generic command** ("set left nozzle temp to 60 C" targeting
printer X) and have the database resolve the **exact per-printer command string + the
communication mechanism to send it over**, returning **`UNSUPPORTED`** (with a reason) when a
printer lacks the capability. This spec defines the data model, how it was derived from
OrcaSlicer, and — most importantly — the **repeatable procedure to keep it current when
OrcaSlicer adds or changes printers** (§7).

Out of scope (deliberately not built yet): the runtime that reads these tables and dispatches
commands (frontend + engine execution).

## 2. Core principles

1. **Mechanism is per-MODEL, not per-vendor.** One brand spans transports: e.g. Creality
   *Ender-3 / Pro / S1 / V2* are **Marlin → OctoPrint/serial**, while *Ender-3 V3 / KE* and the
   *K-series (K1/K2)* are **Klipper → Moonraker**. Every `ctl_printer_type` row carries its own
   `dialect_key` (command syntax) and `mechanism_key` (transport), resolved from the model's
   own `gcode_flavor`. Vendor is metadata only.
2. **Two orthogonal axes** resolve a command: **dialect** (syntax — how the command reads) and
   **mechanism** (transport — how bytes reach the printer, and whether it even *allows*
   arbitrary commands). A command is only truly usable when the mechanism supports G-code
   passthrough (§6).
3. **No guessing.** Every command template is cited to OrcaSlicer source. Rows the app would
   need but OrcaSlicer does not itself emit are flagged `orca_verified=false`.
4. **Capability-gated support.** A command is `UNSUPPORTED` for a printer when (a) no template
   exists for its dialect, or (b) a capability gate fails (no chamber heater, single nozzle,
   Bambu-only, etc.).

## 3. Data model (6 tables, prefix `ctl_`)

| table | rows (dev) | role |
|---|---|---|
| `ctl_comm_mechanism` | 12 | transport: `transport`, `gcode_passthrough`, `passthrough_ref`, `auth` |
| `ctl_dialect` | 6 | command SYNTAX dialect: `bambu_mqtt, marlin, klipper, reprapfirmware, repetier, smoothie` |
| `ctl_command` | 34 | generic command catalog: `category`, `params` (jsonb), `gate` |
| `ctl_command_template` | 147 | (dialect × command) → `template`, `send_method`, `wait_variant`, `orca_verified`, `source_ref` |
| `ctl_printer_type` | 385 | model → `dialect_key`, `mechanism_key`, `nozzle_count`, `has_chamber_heater`, `has_aux_fan`, `is_multi_nozzle`, `popularity_rank`, `difficulty` |
| `ctl_printer_command_support` | 13,090 | cross-ref (printer × command) → `status` (`supported`/`UNSUPPORTED`), `supported`, `reason`, `template_override` |

`send_method` values: `gcode` (raw G-code), `mqtt_json` (Bambu structured), `mqtt_gcode_line`
(Bambu G-code tunnel), `rr_gcode` (Duet HTTP), `host_rpc`. `gate` values: `chamber`, `aux_fan`,
`chamber_fan`, `multi_nozzle`, `bambu_only`, or NULL.

## 4. Resolution algorithm (for the future runtime)

```
INPUT: command_key, params{}, printer_id
1. pt   ← ctl_printer_type[printer_id]                       -- dialect + mechanism + caps
2. sup  ← ctl_printer_command_support[pt.id, command_key]
   if not sup.supported: return UNSUPPORTED(sup.reason)
3. tpl  ← sup.template_override
          ?? ctl_command_template[pt.dialect_key, command_key].template
4. cmd  ← fill placeholders in tpl from params
5. mech ← ctl_comm_mechanism[pt.mechanism_key]
   if mech.gcode_passthrough == false AND tpl.send_method == 'gcode':
        return UNSUPPORTED('transport has no g-code passthrough')   -- e.g. PrusaLink
6. dispatch cmd via mech.transport (mqtt publish / gcode.script / rr_gcode / ...)
```
Example (verified live): `set_left_nozzle_temp` on **H2C** → `{"print":{"command":
"set_nozzle_temp","extruder_index":1,"target_temp":{temp}}}` over `bambu_mqtt` (extruder_index
1 = LEFT). On a **RatRig IDEX** → `SET_HEATER_TEMPERATURE HEATER=extruder1 TARGET={temp}` over
`moonraker`. On an **X1 Carbon** (single nozzle) → `UNSUPPORTED — single nozzle`.

## 5. Source-of-truth methodology (how the data was derived)

| what | OrcaSlicer source |
|---|---|
| per-flavor G-code syntax (temps/fans/move/extrude/accel/PA/toolchange) | `src/libslic3r/GCodeWriter.cpp` |
| Bambu MQTT structured commands (+ gcode_line fallbacks, capability bits) | `src/slic3r/GUI/DeviceManager.cpp`, `DeviceCore/*` |
| transports (endpoints, auth, gcode passthrough) | `src/slic3r/Utils/*`, `PrintHost.cpp` |
| models + capability flags (chamber, nozzle count) | `resources/profiles/<Vendor>/machine/**/*.json` |

Key facts captured: Bambu `extruder_index 0=main=RIGHT, 1=deputy=LEFT` (DevDefs.h:98,
DeviceManager.hpp:335); Bambu capability bits gate structured-vs-gcode_line fallback (bit 32
home, 38 axis, 39 bed); Marlin `M104 T{i}`, RRF `G10 P{i}`, Klipper native `SET_*`.

## 6. Transport / passthrough constraint (critical)

Live control needs the transport to accept arbitrary commands:
- **Passthrough YES:** `bambu_mqtt` (structured + gcode_line), `moonraker`
  (`/printer/gcode/script`), `octoprint` (`/api/printer/command`), `duet_rrf` (`rr_gcode`),
  `esp3d` (`/command?plain=`), `mks_tcp` / `flashforge_tcp` (raw console).
- **Passthrough NO (upload+print only):** `prusalink` / PrusaConnect, `repetier`,
  CrealityPrint-WS, ElegooLink-SDCP, cloud (SimplyPrint / 3DPrinterOS / Obico).
- ⚠ **PrusaLink has no passthrough** → live temp/move control of a Prusa can't go through the
  HTTP host; needs serial or a different channel. Flagged in `ctl_comm_mechanism.notes`.
- Note: OrcaSlicer itself only *uploads+starts* on Moonraker/OctoPrint; the passthrough is a
  property of those *servers'* APIs (and is how OpenPrintHQ's engine already drives Klipper).

## 7. RUNBOOK — updating when OrcaSlicer adds/changes printers

This is the concrete, reproducible procedure. All commands run from
`openprinthq/db/control-framework/`.

```bash
# 1. Refresh OrcaSlicer nightly (record the commit for provenance)
git clone --depth 1 --branch nightly-builds \
    https://github.com/SoftFever/OrcaSlicer.git /tmp/orca
ORCA_REV=$(git -C /tmp/orca rev-parse --short HEAD); echo "OrcaSlicer @ $ORCA_REV"

# 2. Regenerate the model list DETERMINISTICALLY (walks machine/**/*.json, resolves
#    inherits within-vendor, derives flavor + chamber + nozzle_count + aux-fan).
python3 extract_models.py /tmp/orca/resources/profiles > orca_models.csv
git diff --stat orca_models.csv        # review added/changed/removed models

# 3. ONLY if OrcaSlicer introduced a NEW gcode flavor or a NEW control action:
#    edit gen_ctl_schema.py — add the dialect / command / template rows, each cited
#    to the new OrcaSlicer source (GCodeWriter.cpp or DeviceManager.cpp). Otherwise skip.
#    To override a model's mechanism (e.g. a locked-down Creality K2 that must use
#    creality_ws instead of raw moonraker), adjust mech_for() or add a per-model override.

# 4. Regenerate the migration SQL from the CSV + generator.
python3 gen_ctl_schema.py             # -> ctl_framework.sql (idempotent, txn-wrapped)

# 5. Apply to each tenant engine DB (dev shown; repeat per tenant on promotion).
docker exec -i openprinthq-postgres-1 psql -U ophq -d tenant_norjms < ctl_framework.sql

# 6. Verify resolution (should print supported/UNSUPPORTED correctly):
docker exec -i openprinthq-postgres-1 psql -U ophq -d tenant_norjms -c "
  SELECT pt.vendor||' '||pt.model, pt.dialect_key, pt.mechanism_key, s.status,
         COALESCE(s.reason,t.template)
  FROM ctl_printer_type pt
  JOIN ctl_printer_command_support s ON s.printer_type_id=pt.id AND s.command_key='set_chamber_temp'
  LEFT JOIN ctl_command_template t ON t.dialect_key=pt.dialect_key AND t.command_key='set_chamber_temp'
  WHERE pt.model ILIKE ANY(ARRAY['%H2C%','%X1 Carbon%','%Q1 Pro%']);"

# 7. Commit + push (and update the Gitea wiki page if the schema/§ changed).
git add orca_models.csv ctl_framework.sql gen_ctl_schema.py
git commit -m "control-framework: refresh from OrcaSlicer @ $ORCA_REV"
git push origin dev
```

**What is and isn't reproducible:** steps 2 + 4 + 5 are fully deterministic (no agent, no
guessing). Step 3 is the only human step, and only when OrcaSlicer adds a brand-new *command
dialect* or *control action* — a new *printer model* on an existing flavor needs **no code
change**, it flows through automatically (new `ctl_printer_type` row + generated support rows).

## 8. Coverage & honesty caveats
- `orca_verified=false` templates (non-Bambu print pause/resume/stop = M25/M24/M0 or Klipper
  PAUSE/RESUME/CANCEL_PRINT; M221 flow) are firmware-standard, NOT emitted by OrcaSlicer
  (its PrintHost layer has no pause/resume) — included for usability, flagged.
- `mechanism_key` is inferred from flavor (klipper→moonraker, RRF→duet, marlin→octoprint,
  BBL→mqtt, Prusa→prusalink) and is **overridable per model** — vendor cloud transports
  (CrealityPrint, Elegoo SDCP) may front a Klipper printer that is otherwise Moonraker-native.
- `has_aux_fan` is heuristic (profile notes / `auxiliary_fan`); confirm per model before relying.
- Deployed per-tenant (dev `tenant_norjms`). These are GLOBAL reference tables; on promotion
  apply to every tenant DB, OR centralize into a shared reference DB later (open decision).

## 9. Files
- `SPECIFICATION.md` — this document.
- `extract_models.py` — deterministic profile → `orca_models.csv` (§7 step 2).
- `orca_models.csv` — 385 models + capability flags (generated).
- `gen_ctl_schema.py` — `orca_models.csv` → `ctl_framework.sql` (schema + seed + generative cross-ref).
- `ctl_framework.sql` — the idempotent migration.
- `README.md` — quick-start.
