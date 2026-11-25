# OpenPrintHQ Print Queue for OrcaSlicer

An OrcaSlicer add-on (a Python plugin for OrcaSlicer 2.5.1 and later) that sends sliced plates to this farm's
print queue instead of straight to a printer. It works for every kind of printer the farm runs, Bambu Lab
included, and adds an OpenPrintHQ tab for watching the farm and managing its queue.

- **Print** puts the job first in line on the printer; OrcaSlicer's **Send** adds it to the end of that
  printer's queue. Nothing starts on the spot: the farm starts each job when the printer is ready.
- Before sending, one check against the farm: out of service, missing material, nozzle size, build plate, or a
  file sliced for another model. It offers a printer of the same model that can take the job.
- Sync filaments from the printer's AMS, or from the spools assigned to it in the farm.

## Install

The farm's **Slice** page offers the add-on, declared for that farm's address, and makes a key for it.

1. OrcaSlicer, **Plugins**, **Install local plugin**, and pick `openprinthq_print_queue.py`. Keep that file name:
   OrcaSlicer loads the add-on by it.
2. OrcaSlicer, **Preferences**: turn on **Use printer agents instead of print hosts**.
3. The add-on's **Config** page: the farm's address and the key from the Slice page.
4. For each farm printer, a printer in OrcaSlicer whose name includes the farm printer's name (for example
   "H2D - OpenPrintHQ Farm"), with **Printer agent** set to OpenPrintHQ and any print host (OrcaSlicer only uses an
   agent when the host is set; the add-on takes the address from its Config page).

OrcaSlicer in the browser on an OpenPrintHQ hub comes with all of this done.

A managed install can give the address and key in the environment OrcaSlicer starts with (`OPENPRINTHQ_URL`,
`OPENPRINTHQ_API_KEY`); they fill only what the Config page leaves blank.

## Tests

    python3 -I -m unittest discover -s tests

## License

AGPL-3.0-or-later, like the rest of this repository.
