"""Crafts: what this installation makes, and so which tools the hub shows.

3D Printing is always on: the print farm is the hub's core (invariant 1). The other crafts are optional and chosen
for the whole installation, by the installer at first-run setup or later on Settings, Crafts. A craft that is off
leaves its sidebar group out for everybody. Turning a craft on installs nothing: Laser Crafting's tools are a
separate app, OpenPrintHQ Laser at /apps/laser, which the installer adds when Laser Crafting is chosen. Sublimation
and Graphic Art have no tools yet. Documented in docs/crafts.md.
"""

CRAFTS = {
    "3d_printing": {
        "label": "3D Printing", "always_on": True, "slug": None,
        "summary": "Printers, the print queue, filament, slicing and the farm. Always on.",
    },
    "laser": {
        "label": "Laser Crafting", "always_on": False, "slug": "laser",
        "summary": "Convert laser projects between Bambu Suite, xTool Studio, Falcon Design Space and SVG. "
                   "The tools are the OpenPrintHQ Laser app, which the installer adds.",
    },
    "sublimation": {
        "label": "Sublimation", "always_on": False, "slug": "sublimation",
        "summary": "Tools for sublimation are coming.",
    },
    "graphic_art": {
        "label": "Graphic Art", "always_on": False, "slug": "graphic-art",
        "summary": "Tools for graphic art are coming.",
    },
}
ALWAYS = tuple(k for k, v in CRAFTS.items() if v["always_on"])
OPTIONAL = tuple(k for k, v in CRAFTS.items() if not v["always_on"])


def normalise(values) -> list[str]:
    """The optional crafts named in values, in the list's order, each once. An unknown name is refused
    (ValueError); an always-on craft may be named and is left out, since it needs no choosing."""
    names = list(values or ())
    unknown = [v for v in names if v not in CRAFTS]
    if unknown:
        raise ValueError(f"unknown craft {unknown[0]!r}; the crafts are {', '.join(CRAFTS)}")
    return [k for k in OPTIONAL if k in names]


def enabled(stored) -> list[str]:
    """Every craft that is on, always-on ones included, from the optional crafts stored for the installation.
    Anything stored that this version does not know is ignored rather than refused."""
    picked = set(stored or ())
    return [k for k in CRAFTS if k in ALWAYS or k in picked]


def chosen() -> list[str]:
    """The installation's crafts that are on (cached with the installation's state)."""
    from .installation import state
    return enabled(state().get("crafts"))


def describe(on: list[str]) -> list[dict]:
    return [{"id": k, "label": v["label"], "summary": v["summary"], "always_on": v["always_on"], "enabled": k in on}
            for k, v in CRAFTS.items()]
