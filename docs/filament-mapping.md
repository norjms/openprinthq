# Filament mapping

When the next queued job can't start because the filament loaded on a printer doesn't match what the file was
sliced with, ArtisanHub Farm holds the job and lists it as unmatched (its rules: `docs/filament-matching.md` in
openprinthq-engine). The hub alerts at once and offers a mapping screen. Code: `services/filament_mapping.py`,
`routers/filament_mapping.py`, page `/farm/jobs/{id}/filament`.

## The alert

The job is mirrored as `waiting_filament`. When it turns unmatched the hub sends the `waiting_filament` alert
titled "Filament needs mapping: {job}", with the reason and a link to the mapping screen, through the channels
in the hub's alert settings.

## The mapping screen

One row per filament the model prints with: its colour, material and grams, and a picker of the chosen
printer's loaded filament (AMS slot, external spool), with the farm's best guess picked and a badge (same,
close colour, colour family, different colour, different material). "Show every printer" also offers printers
of other models. "Remember this mapping" saves it in the farm so the same filaments match next time.

- **Same printer model and, for every filament, the same material group** (PLA for PLA, PETG for PETG): the
  farm pins the job to that printer with the mapping and it starts.
- **Another material or another printer model:** the hub reslices the model from its source in the library for
  that printer with the loaded filaments (cached in `plate_slice`, keyed by the filaments), swaps the file in and
  maps it. A clean reslice starts by itself; one with warnings waits in Slicing review and alerts. A job not
  linked to a model in the hub, or a printer with no printer profile in the hub, can't be resliced: the screen
  says so and offers only same-material choices.

## API

The same routes under `/api/farm` (web) and `/api/mobile/v1/farm` (phone app):

| Method | Path | Permission | What |
|---|---|---|---|
| GET | `/jobs/{id}/filament?all_printers=false` | view | What the job needs, per printer what is loaded and the best mapping, `can_reslice`, the reslice's progress |
| POST | `/jobs/{id}/filament` | farm | `{"printer_id", "slots": [{"slot_id", "global_tray_id"}], "remember": true}` → `{"started"}` or `{"reslice": true, "why": [...]}` |
| PUT | `/jobs/{id}/match-level` | farm | `{"level": "exact" \| "close" \| "family" \| "any" \| null}` |
| GET | `/filament-matching/unmatched` | view | Jobs waiting to be mapped |
| GET, PUT | `/filament-matching/settings` | view, admin | The farm's default level and material groups |
| GET, DELETE | `/filament-matching/aliases[/{id}]` | view, farm | Saved mappings |

Only printers the person may use are offered (printer access). An engine without filament matching answers 400
("update ArtisanHub Farm").
