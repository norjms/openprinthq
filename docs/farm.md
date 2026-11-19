# The print farm

OpenPrintHQ runs its printers through its farm: the print engine and its control plane. It is the only print
farm the hub supports. The hub keeps the catalog, orders, slicing, filament use and maintenance plans; the farm keeps
the printers, the print queue, the print files it was sent and the filament library (its Spoolman).

## Connecting it

1. Finish first-run setup at `/setup` (or through the setup API, `docs/setup-api.md`). It records the farm as
   the installation's farm.
2. In the farm, create an API key with the `control` scope.
3. In the hub, open Settings, Connected services, the farm, and enter the farm's address and that key. Test
   the connection, then save.
4. On Sync health, run "Set up connected apps". It registers the hub's signed webhook with the farm (and adds the
   starter categories to the other connected apps). Running it again is safe.

The connection lives in the hub's database, per team.

## What the hub sends and reads

- Print files: a model's print-ready files are uploaded to the farm's library when they are first needed (an order,
  Print now, or the setting "Store 3MF and G-code files in the farm").
- Jobs: orders are sent to the farm when order routing is `hub` (Farm page); with `off` nothing is sent on its own.
  Print now pins a job to one printer.
- Events: the farm's webhooks keep the hub's view of the queue current; a periodic sync covers anything missed.
- Maintenance: each farm maintenance item is a hub plan; marking a task done in the hub completes it in the farm.
- Filament: when the farm is connected its Spoolman is the filament library. `POST /api/actions/spools-migrate-to-farm`
  copies the hub's own Spoolman into it (`docs/mobile-api.md`, One spool library).

A feature the connected engine does not have yet is hidden rather than failing; `GET /api/mobile/v1/me` lists the
farm's capabilities.

## Moving from another farm

Earlier versions could also run on another farm app. This version starts only on the farm. An installation
set up with another farm stops at startup with "no longer supports" and changes nothing, so the previous version
keeps working until you are ready to move.

To move:

1. Keep the hub on its current version. Install the farm and add your printers to it, using the same printer
   names the hub's printer profiles use (Slicing, Printer profiles, "Printer name on the farm").
2. Let the old farm finish what is printing and empty its queue. Jobs still waiting there when you move are closed in
   the hub ("The print farm was changed: send it again") and have to be sent again.
3. Back up the hub's database.
4. Set `HUB_FARM_MOVE=openprinthq` for the hub (web and worker), upgrade it, and start it. At that start the hub:
   - records the farm as the installation's farm;
   - keeps the old farm's job and print history, with its ids prefixed `old:` so they never match the new farm's;
   - clears the links to the old farm's files and maintenance items, and its stored connection.
5. Connect the farm and run "Set up connected apps" (Connecting it, above). Print files are uploaded to it
   again when they are next needed.
6. Maintenance: the hub's plans stay, now hub-only. Recreate the maintenance items in the farm; each one shows
   up in the hub as a linked plan at the next sync, and you can remove the hub-only copy it replaces.
7. Remove `HUB_FARM_MOVE` when the hub has started once. Leaving it set does nothing further.

To undo before step 5, restore the backup from step 3 and go back to the previous version.
