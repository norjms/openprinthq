"""The person's look: a named theme and a "reduce transparency" choice, saved on their hub account.

The themes come from the shared design package (openprinthq-ui). glass-dark is the only one today; the names exist so
that a light or high-contrast theme can be added without changing anything that stores or reads the choice.

Earlier hubs saved a "skin" and a light or dark "mode". Those are gone: every
saved value, known or not, becomes glass-dark (migrate_prefs, run once at startup by db.init_db).
"""
from sqlalchemy import select, update

THEMES = ("glass-dark",)
DEFAULT_THEME = "glass-dark"
# Reduce transparency: "auto" follows the device's setting, "on" draws solid panels, "off" keeps glass.
TRANSPARENCY = ("auto", "on", "off")
OLD_KEYS = ("skin", "mode")


def resolve_theme(value) -> str:
    """A saved theme, or the default for anything that is not a known theme (an old skin, a typo, nothing)."""
    return value if value in THEMES else DEFAULT_THEME


def normalize(prefs: dict | None) -> dict:
    """The person's choices with the look in its current form: theme and reduce_transparency, no skin or mode.
    Other choices are kept as they are."""
    p = dict(prefs or {})
    out = {k: v for k, v in p.items() if k not in OLD_KEYS}
    out["theme"] = resolve_theme(p.get("theme"))
    rt = p.get("reduce_transparency")
    out["reduce_transparency"] = rt if rt in TRANSPARENCY else "auto"
    return out


def migrate_prefs(conn) -> int:
    """Rewrite every saved look to the current form (every old skin becomes glass-dark). Returns rows changed."""
    from ..models import User
    t = User.__table__
    changed = 0
    for uid, prefs in conn.execute(select(t.c.id, t.c.prefs).where(t.c.prefs.isnot(None))).all():
        if not isinstance(prefs, dict) or not prefs:      # nothing saved (JSON null): nothing to move
            continue
        new = normalize(prefs)
        if new != prefs:
            conn.execute(update(t).where(t.c.id == uid).values(prefs=new))
            changed += 1
    return changed
