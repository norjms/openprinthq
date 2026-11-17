"""db.migrate_farm_names: an installation from before the farm-neutral names keeps its data under the new names."""

import pytest
from sqlalchemy import create_engine, inspect, text


def _old_db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        for ddl in (
            "CREATE TABLE printer_profile (id INTEGER PRIMARY KEY, wt_printer TEXT, dispatch TEXT)",
            "CREATE TABLE model_file (id INTEGER PRIMARY KEY, legacy_farm_uuid TEXT, legacy_farm_error TEXT, farm_kind TEXT)",
            "CREATE TABLE product (id INTEGER PRIMARY KEY, wt_error TEXT, wt_mapping_ids TEXT)",
            "CREATE TABLE maint_plan (id INTEGER PRIMARY KEY, wt_schedule_id INTEGER, wt_status TEXT, wt_hours_since REAL)",
            "CREATE TABLE maint_task (id INTEGER PRIMARY KEY, wt_note TEXT, done_by TEXT, created_by TEXT)",
            "CREATE TABLE job (id INTEGER PRIMARY KEY, kind TEXT, state TEXT)",
            "CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT)",
            "CREATE TABLE spool_link (id INTEGER PRIMARY KEY)",
        ):
            c.execute(text(ddl))
        c.execute(text("INSERT INTO printer_profile VALUES (1, 'H2D', 'legacy_farm')"))
        c.execute(text("INSERT INTO model_file VALUES (1, 'u-1', NULL, 'legacy_farm'), (2, '42', NULL, 'openprinthq')"))
        c.execute(text("INSERT INTO product VALUES (1, 'boom', '[1]')"))
        c.execute(text("INSERT INTO maint_plan VALUES (1, 91, 'ok', 3.5)"))
        c.execute(text("INSERT INTO maint_task VALUES (1, 'done there', 'legacy_farm', 'legacy_farm')"))
        c.execute(text("INSERT INTO job VALUES (1, 'legacy_farm.sync_product', 'queued'), (2, 'spools.sync', 'queued'), "
                       "(3, 'spools.sync', 'done')"))
        c.execute(text("INSERT INTO setting VALUES ('wt_mirror_library', 'true')"))
    return eng


def test_old_names_and_values_move_over(tmp_path):
    from app.db import migrate_farm_names
    eng = _old_db(tmp_path)
    with eng.begin() as c:
        migrate_farm_names(c)
        migrate_farm_names(c)            # a second run changes nothing
    insp = inspect(eng)
    assert "spool_link" not in insp.get_table_names()
    cols = {t: {x["name"] for x in insp.get_columns(t)} for t in insp.get_table_names()}
    assert {"farm_printer", "dispatch"} <= cols["printer_profile"] and "wt_printer" not in cols["printer_profile"]
    assert cols["product"] == {"id", "farm_error"}
    assert {"farm_schedule_id", "farm_status", "farm_hours_since"} <= cols["maint_plan"]
    with eng.connect() as c:
        assert c.execute(text("SELECT farm_printer, dispatch FROM printer_profile")).one() == ("H2D", "farm")
        assert c.execute(text("SELECT farm_schedule_id, farm_hours_since FROM maint_plan")).one() == (91, 3.5)
        assert c.execute(text("SELECT farm_note, done_by, created_by FROM maint_task")).one() == ("done there", "farm", "farm")
        files = dict(c.execute(text("SELECT id, farm_file_ref FROM model_file")).all())
        assert files == {1: None, 2: "42"}          # only references into ArtisanHub Farm are kept
        assert dict(c.execute(text("SELECT id, kind FROM job")).all()) == {1: "farm.sync_product", 3: "spools.sync"}
        assert c.execute(text("SELECT key FROM setting")).scalar() == "farm_mirror_library"


def test_names_from_before_the_code_was_published(tmp_path):
    """A hub that ran the private code before it was published (schema 20) names the old farm app itself."""
    from app.db import migrate_farm_names
    eng = create_engine(f"sqlite:///{tmp_path / 'pre.db'}")
    with eng.begin() as c:
        for ddl in (
            "CREATE TABLE printer_profile (id INTEGER PRIMARY KEY, wt_printer TEXT, dispatch TEXT)",
            "CREATE TABLE model_file (id INTEGER PRIMARY KEY, watchtower_uuid TEXT, watchtower_error TEXT, farm_kind TEXT)",
            "CREATE TABLE print_record (id INTEGER PRIMARY KEY, source TEXT)",
            "CREATE TABLE connection (id INTEGER PRIMARY KEY, kind TEXT)",
            "CREATE TABLE job (id INTEGER PRIMARY KEY, kind TEXT, state TEXT)",
        ):
            c.execute(text(ddl))
        c.execute(text("INSERT INTO printer_profile VALUES (1, 'H2D', 'watchtower')"))
        c.execute(text("INSERT INTO model_file VALUES (1, 'u-1', 'gone', 'watchtower'), (2, '42', NULL, 'openprinthq')"))
        c.execute(text("INSERT INTO print_record VALUES (1, 'watchtower')"))
        c.execute(text("INSERT INTO connection VALUES (1, 'watchtower'), (2, 'openprinthq')"))
        c.execute(text("INSERT INTO job VALUES (1, 'watchtower.sync_product', 'done')"))
    with eng.begin() as c:
        migrate_farm_names(c)
        migrate_farm_names(c)
    cols = {x["name"] for x in inspect(eng).get_columns("model_file")}
    assert {"farm_file_ref", "farm_file_error"} <= cols and "watchtower_uuid" not in cols
    with eng.connect() as c:
        assert dict(c.execute(text("SELECT id, farm_file_ref FROM model_file")).all()) == {1: None, 2: "42"}
        assert c.execute(text("SELECT dispatch FROM printer_profile")).scalar() == "farm"
        assert c.execute(text("SELECT source FROM print_record")).scalar() == "farm"
        assert [r[0] for r in c.execute(text("SELECT kind FROM connection"))] == ["openprinthq"]
        assert c.execute(text("SELECT kind FROM job")).scalar() == "farm.sync_product"


@pytest.fixture
def own_db(tmp_path, monkeypatch):
    """A separate SQLite database for init_db, put back afterwards."""
    from sqlalchemy.orm import sessionmaker
    from app import db
    from app.services import installation
    eng = create_engine(f"sqlite:///{tmp_path / 'hub.db'}", future=True)
    monkeypatch.setattr(db, "_engine", eng)
    monkeypatch.setattr(db, "_Session", sessionmaker(bind=eng, expire_on_commit=False, future=True))
    installation.forget()
    db.init_db()
    yield eng
    installation.forget()


def _on_old_farm(eng):
    from app.db import SessionLocal
    from app.models import FarmJob, MaintPlan
    from app.tenant import use_team
    with eng.begin() as c:
        c.execute(text("UPDATE installation SET farm = 'legacy_farm', farm_source = 'setup' WHERE id = 1"))
    s = SessionLocal()
    try:
        with use_team(1):
            s.add_all([FarmJob(external_id="7", status="queued"), FarmJob(external_id="8", status="completed"),
                       MaintPlan(printer_name="MK4S", title="Bed", parts=[], farm_schedule_id=91)])
            s.commit()
    finally:
        s.close()


def test_an_installation_on_an_unsupported_farm_is_left_untouched(own_db, monkeypatch):
    from app import db
    from app.config import settings
    from app.services.installation import UnsupportedFarm
    _on_old_farm(own_db)
    monkeypatch.setattr(settings(), "farm_move", "")
    with pytest.raises(UnsupportedFarm):
        db.init_db()
    with own_db.connect() as c:
        assert c.execute(text("SELECT farm FROM installation")).scalar() == "legacy_farm"
        assert dict(c.execute(text("SELECT external_id, status FROM farm_job")).all()) == {"7": "queued", "8": "completed"}


def test_farm_move_keeps_history_under_old_ids(own_db, monkeypatch):
    from app import db
    from app.config import settings
    _on_old_farm(own_db)
    monkeypatch.setattr(settings(), "farm_move", "openprinthq")
    db.init_db()
    db.init_db()                         # the next start changes nothing more
    with own_db.connect() as c:
        assert c.execute(text("SELECT farm, farm_source FROM installation")).one() == ("openprinthq", "moved")
        jobs = {r[0]: r[1:] for r in c.execute(text("SELECT external_id, status, error FROM farm_job")).all()}
        assert jobs["old:7"][0] == "cancelled" and "send it again" in jobs["old:7"][1]
        assert jobs["old:8"] == ("completed", None)
        assert c.execute(text("SELECT farm_schedule_id FROM maint_plan")).scalar() is None
