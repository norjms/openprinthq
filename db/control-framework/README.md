# Control-Command Framework (DB) — OrcaSlicer-derived

Reference/catalog tables that let the frontend send ONE generic command (e.g.
"set left nozzle temp to 60C" on printer X) and have the DB resolve the exact
per-printer command + how to send it. **Schema + data only — no feature code yet.**

Grounded in the OrcaSlicer **nightly / v2.5.0-dev** source (commit 04e13200), not guesses:
- Per-flavor G-code syntax from `src/libslic3r/GCodeWriter.cpp`.
- Bambu MQTT structured commands from `src/slic3r/GUI/DeviceManager.cpp` (+ DeviceCore/*).
- Print-host transports from `src/slic3r/Utils/*`.
- Printer models + capability flags from `resources/profiles/*/machine/*.json`.

## Tables
- `ctl_comm_mechanism` — how to talk to a printer (transport, gcode-passthrough, auth).
- `ctl_dialect` — command SYNTAX dialect (bambu_mqtt, marlin, klipper, reprapfirmware, repetier, smoothie).
- `ctl_command` — the generic command catalog (every control action Orca can perform) + capability `gate`.
- `ctl_command_template` — (dialect × command) → the concrete command template + send_method + source citation.
- `ctl_printer_type` — every OrcaSlicer printer model (386) with dialect, mechanism, capability flags, popularity, difficulty.
- `ctl_printer_command_support` — cross-ref (printer × command) → supported | UNSUPPORTED (+reason). Generated from templates × capability gates.

## Resolution flow (for the future feature)
frontend generic command + target printer → `ctl_printer_type` (dialect+mechanism+caps)
→ check `ctl_printer_command_support` (supported?) → `ctl_command_template` (dialect,command)
→ fill placeholders → send via `ctl_comm_mechanism.transport`.

## Apply (idempotent — DELETEs+reseeds catalog rows inside a txn)
    docker exec -i openprinthq-postgres-1 psql -U ophq -d tenant_norjms < ctl_framework.sql

## Regenerate
    python3 gen_ctl_schema.py   # reads orca_models.csv → writes ctl_framework.sql

## Deploy target / promotion
Deployed to the **engine tenant DB** (dev: `tenant_norjms`) since command resolution
happens engine-side at execution time. These are GLOBAL reference tables (identical across
tenants) — apply the same SQL to each tenant DB on promotion to test/prod, OR centralize in
a shared reference DB later (design note; not yet decided).

## Coverage notes / honesty
- `orca_verified=true` templates are cited to OrcaSlicer source. `orca_verified=false` rows
  (e.g. print pause/resume/stop for non-Bambu, M221 flow) are firmware-standard commands Orca
  itself does NOT emit (Orca's PrintHost layer has no pause/resume; temp/move for Moonraker/
  OctoPrint go through those APIs' own gcode passthrough, which Orca doesn't call) — included
  so the framework is usable, but flagged as not-Orca-sourced.
- Mechanism per model is INFERRED from gcode_flavor/vendor (Klipper→Moonraker, RRF→Duet,
  Marlin→OctoPrint/serial, BBL→MQTT, Prusa→PrusaLink) and is overridable per printer.
