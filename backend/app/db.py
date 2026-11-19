from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from .config import settings


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def engine():
    global _engine, _Session
    if _engine is None:
        url = settings().database_url
        kw = {"pool_pre_ping": True, "future": True}
        if url.startswith("postgresql"):
            kw.update(pool_size=10, max_overflow=10)
        _engine = create_engine(url, **kw)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def SessionLocal() -> Session:
    engine()
    return _Session()


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# Lightweight forward-only migrations: (version, sql). create_all handles new tables;
# use this list for ALTERs on existing tables.
MIGRATIONS: list[tuple[int, str]] = [
    (1, "ALTER TABLE job ADD COLUMN IF NOT EXISTS priority integer DEFAULT 5; "
        "UPDATE job SET priority = 8 WHERE kind = 'import.folder'; UPDATE job SET priority = 7 WHERE kind = 'dam.sync_model'"),
    (2, "ALTER TABLE model ADD COLUMN IF NOT EXISTS license_code varchar(30), ADD COLUMN IF NOT EXISTS license_checked_at timestamptz, "
        "ADD COLUMN IF NOT EXISTS credit_override text, ADD COLUMN IF NOT EXISTS makerworld_id integer, "
        "ADD COLUMN IF NOT EXISTS makerworld_instance_id integer; "
        "CREATE INDEX IF NOT EXISTS ix_model_makerworld_id ON model (makerworld_id); "
        "ALTER TABLE designer ADD COLUMN IF NOT EXISTS handle varchar(120), ADD COLUMN IF NOT EXISTS platform varchar(40), "
        "ADD COLUMN IF NOT EXISTS credit_template text, ADD COLUMN IF NOT EXISTS always_credit boolean DEFAULT false, "
        "ADD COLUMN IF NOT EXISTS notes text; "
        "UPDATE designer SET always_credit = false WHERE always_credit IS NULL; "
        "UPDATE model SET license_code = 'COMMERCIAL' WHERE source = 'deltaprints' AND license_code IS NULL; "
        "UPDATE model SET license_code = 'OWN' WHERE source = 'own' AND license_code IS NULL; "
        "UPDATE model SET license_code = CASE WHEN license_ok THEN 'COMMERCIAL' ELSE 'UNKNOWN' END WHERE license_code IS NULL"),
    (3, "ALTER TABLE model ADD COLUMN IF NOT EXISTS source_platform varchar(30), ADD COLUMN IF NOT EXISTS source_ext_id varchar(80); "
        "CREATE INDEX IF NOT EXISTS ix_model_source_ext_id ON model (source_ext_id); "
        "UPDATE model SET source_platform = 'makerworld', source_ext_id = makerworld_id::text WHERE makerworld_id IS NOT NULL"),
    (4, "INSERT INTO team (id, slug, name, personal, storage_prefix, source_dirs, created_at) VALUES (1, 'my-workshop', 'My workshop', false, '', '{}', now()) ON CONFLICT (id) DO NOTHING; SELECT setval(pg_get_serial_sequence('team', 'id'), (SELECT max(id) FROM team)); ALTER TABLE category ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE category SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE category ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_category_team_id ON category (team_id); ALTER TABLE designer ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE designer SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE designer ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_designer_team_id ON designer (team_id); ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE printer_profile SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE printer_profile ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_printer_profile_team_id ON printer_profile (team_id); ALTER TABLE license_grant ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE license_grant SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE license_grant ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_license_grant_team_id ON license_grant (team_id); ALTER TABLE model ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE model SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE model ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_model_team_id ON model (team_id); ALTER TABLE image ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE image SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE image ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_image_team_id ON image (team_id); ALTER TABLE license_evidence ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE license_evidence SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE license_evidence ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_license_evidence_team_id ON license_evidence (team_id); ALTER TABLE model_file ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE model_file SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE model_file ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_model_file_team_id ON model_file (team_id); ALTER TABLE product ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE product SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE product ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_product_team_id ON product (team_id); ALTER TABLE variant ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE variant SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE variant ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_variant_team_id ON variant (team_id); ALTER TABLE bom_filament ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE bom_filament SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE bom_filament ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_bom_filament_team_id ON bom_filament (team_id); ALTER TABLE bom_part ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE bom_part SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE bom_part ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_bom_part_team_id ON bom_part (team_id); ALTER TABLE shop_order ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE shop_order SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE shop_order ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_shop_order_team_id ON shop_order (team_id); ALTER TABLE shop_order_line ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE shop_order_line SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE shop_order_line ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_shop_order_line_team_id ON shop_order_line (team_id); ALTER TABLE job ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE job SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE job ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_job_team_id ON job (team_id); ALTER TABLE event ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE event SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE event ALTER COLUMN team_id SET NOT NULL; CREATE INDEX IF NOT EXISTS ix_event_team_id ON event (team_id); ALTER TABLE category DROP CONSTRAINT IF EXISTS category_name_key; ALTER TABLE category ADD CONSTRAINT uq_category_team_name UNIQUE (team_id, name); ALTER TABLE category DROP CONSTRAINT IF EXISTS category_slug_key; ALTER TABLE category ADD CONSTRAINT uq_category_team_slug UNIQUE (team_id, slug); ALTER TABLE designer DROP CONSTRAINT IF EXISTS designer_name_key; ALTER TABLE designer ADD CONSTRAINT uq_designer_team_name UNIQUE (team_id, name); ALTER TABLE designer DROP CONSTRAINT IF EXISTS designer_slug_key; ALTER TABLE designer ADD CONSTRAINT uq_designer_team_slug UNIQUE (team_id, slug); ALTER TABLE printer_profile DROP CONSTRAINT IF EXISTS printer_profile_name_key; ALTER TABLE printer_profile ADD CONSTRAINT uq_printer_profile_team_name UNIQUE (team_id, name); ALTER TABLE model DROP CONSTRAINT IF EXISTS model_code_key; ALTER TABLE model ADD CONSTRAINT uq_model_team_code UNIQUE (team_id, code); ALTER TABLE model DROP CONSTRAINT IF EXISTS model_slug_key; ALTER TABLE model ADD CONSTRAINT uq_model_team_slug UNIQUE (team_id, slug); ALTER TABLE product DROP CONSTRAINT IF EXISTS product_sku_key; ALTER TABLE product ADD CONSTRAINT uq_product_team_sku UNIQUE (team_id, sku); ALTER TABLE variant DROP CONSTRAINT IF EXISTS variant_sku_key; ALTER TABLE variant ADD CONSTRAINT uq_variant_team_sku UNIQUE (team_id, sku); ALTER TABLE shop_order DROP CONSTRAINT IF EXISTS shop_order_woo_order_id_key; ALTER TABLE shop_order ADD CONSTRAINT uq_shop_order_team_woo UNIQUE (team_id, woo_order_id); CREATE INDEX IF NOT EXISTS ix_job_team_state ON job (team_id, state); ALTER TABLE setting ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; UPDATE setting SET team_id = 1 WHERE team_id IS NULL; ALTER TABLE setting ALTER COLUMN team_id SET NOT NULL; ALTER TABLE setting DROP CONSTRAINT IF EXISTS setting_pkey; ALTER TABLE setting ADD COLUMN IF NOT EXISTS id serial PRIMARY KEY; ALTER TABLE setting ADD CONSTRAINT uq_setting_team_key UNIQUE (team_id, key); CREATE INDEX IF NOT EXISTS ix_setting_team_id ON setting (team_id); ALTER TABLE api_key ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; ALTER TABLE api_key ADD COLUMN IF NOT EXISTS user_id integer REFERENCES hub_user(id) ON DELETE SET NULL; UPDATE api_key SET team_id = 1 WHERE team_id IS NULL; CREATE INDEX IF NOT EXISTS ix_api_key_team_id ON api_key (team_id); ALTER TABLE oauth_token ADD COLUMN IF NOT EXISTS team_id integer REFERENCES team(id) ON DELETE CASCADE; ALTER TABLE oauth_token ADD COLUMN IF NOT EXISTS user_id integer REFERENCES hub_user(id) ON DELETE CASCADE; UPDATE oauth_token SET revoked = true WHERE team_id IS NULL; CREATE INDEX IF NOT EXISTS ix_oauth_token_team_id ON oauth_token (team_id); DROP TABLE IF EXISTS part_cache; DROP TABLE IF EXISTS spool_cache; DROP TABLE IF EXISTS filament_catalog"),
    (5, "ALTER TABLE membership ADD COLUMN IF NOT EXISTS perms json; ALTER TABLE invite ALTER COLUMN team_id DROP NOT NULL; ALTER TABLE invite ADD COLUMN IF NOT EXISTS kind varchar(12) DEFAULT 'join'; ALTER TABLE invite ADD COLUMN IF NOT EXISTS team_name varchar(120); ALTER TABLE invite ADD COLUMN IF NOT EXISTS name varchar(200); ALTER TABLE invite ADD COLUMN IF NOT EXISTS perms json; ALTER TABLE invite ADD COLUMN IF NOT EXISTS ak_invitation varchar(64); ALTER TABLE invite ADD COLUMN IF NOT EXISTS ak_url varchar(500); UPDATE invite SET kind = 'join' WHERE kind IS NULL"),
    (6, "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS engine varchar(10) DEFAULT 'orca'; "
        "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS machine_config text; "
        "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS tools json DEFAULT '[]'; "
        "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS dispatch varchar(20) DEFAULT 'none'; "
        "UPDATE printer_profile SET engine = 'orca' WHERE engine IS NULL; "
        "UPDATE printer_profile SET dispatch = CASE WHEN wt_printer IS NOT NULL THEN 'legacy_farm' ELSE 'none' END; "
        "ALTER TABLE model ADD COLUMN IF NOT EXISTS colors_needed integer; "
        "ALTER TABLE model ADD COLUMN IF NOT EXISTS inspect json DEFAULT '{}'"),
    (7, "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS hourly_rate double precision"),
    (8, "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS bed_type varchar(40)"),
    (9, "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS filament_slots integer"),
    # people with hand-picked permissions who can slice also get the new farm permission (plate checks, queue)
    (10, "UPDATE membership SET perms = (perms::jsonb || '[\"farm\"]'::jsonb)::json WHERE perms IS NOT NULL "
         "AND perms::jsonb ? 'slice' AND NOT perms::jsonb ? 'farm'"),
    (11, "ALTER TABLE shop_order ADD COLUMN IF NOT EXISTS routed_at timestamptz; "
         "ALTER TABLE shop_order ADD COLUMN IF NOT EXISTS printed_at timestamptz; "
         "ALTER TABLE shop_order_line ADD COLUMN IF NOT EXISTS wt_job_ids json DEFAULT '[]'; "
         "ALTER TABLE shop_order_line ADD COLUMN IF NOT EXISTS wt_error text; "
         "ALTER TABLE plate_check ADD COLUMN IF NOT EXISTS ready_due timestamptz; "
         "ALTER TABLE plate_check ADD COLUMN IF NOT EXISTS photo varchar(200)"),
    (12, "ALTER TABLE shop_order ADD COLUMN IF NOT EXISTS due_at timestamptz; "
         "ALTER TABLE shop_order ADD COLUMN IF NOT EXISTS late_alerted_at timestamptz"),
    (13, "ALTER TABLE maint_plan ADD COLUMN IF NOT EXISTS wt_schedule_id integer; "
         "ALTER TABLE maint_plan ADD COLUMN IF NOT EXISTS wt_status varchar(20); "
         "ALTER TABLE maint_plan ADD COLUMN IF NOT EXISTS wt_hours_since double precision; "
         "ALTER TABLE maint_plan ADD COLUMN IF NOT EXISTS category varchar(40); "
         "ALTER TABLE maint_task ADD COLUMN IF NOT EXISTS out_of_service boolean DEFAULT false; "
         "ALTER TABLE maint_task ADD COLUMN IF NOT EXISTS wt_note text"),
    (14, "ALTER TABLE hub_user ADD COLUMN IF NOT EXISTS prefs json"),
    (15, "ALTER TABLE farm_job ADD COLUMN IF NOT EXISTS fil_plan json; "
         "ALTER TABLE farm_job ADD COLUMN IF NOT EXISTS spools json; "
         "ALTER TABLE farm_job ADD COLUMN IF NOT EXISTS used_g double precision; "
         "ALTER TABLE farm_job ADD COLUMN IF NOT EXISTS used_source varchar(12)"),
    (16, "ALTER TABLE api_key ADD COLUMN IF NOT EXISTS expires_at timestamptz; "
         "ALTER TABLE api_key ADD COLUMN IF NOT EXISTS instance_id varchar(120); "
         "ALTER TABLE api_key ADD COLUMN IF NOT EXISTS bind_instance boolean DEFAULT false; "
         "ALTER TABLE api_key ADD COLUMN IF NOT EXISTS note varchar(300); "
         "ALTER TABLE api_key ADD COLUMN IF NOT EXISTS last_ip varchar(64)"),
    (17, "ALTER TABLE model_file ADD COLUMN IF NOT EXISTS farm_kind varchar(20); "
         "UPDATE model_file SET farm_kind = 'legacy_farm' WHERE legacy_farm_uuid IS NOT NULL AND farm_kind IS NULL"),
    # plate-aware reslicing: build_plate and plate_slice are new tables (create_all)
    (18, "ALTER TABLE printer_profile ADD COLUMN IF NOT EXISTS installed_plate_id integer "
         "REFERENCES build_plate(id) ON DELETE SET NULL; "
         "ALTER TABLE farm_job ADD COLUMN IF NOT EXISTS plate_reslice json"),
    (19, "ALTER TABLE build_plate ADD COLUMN IF NOT EXISTS maker varchar(60); "
         "ALTER TABLE build_plate ADD COLUMN IF NOT EXISTS surface varchar(40)"),
    # sliced files chosen by hand for a printer, and slices locked against re-slicing
    (20, "ALTER TABLE slice_result ADD COLUMN IF NOT EXISTS locked boolean DEFAULT false; "
         "ALTER TABLE slice_result ADD COLUMN IF NOT EXISTS source varchar(12)"),
    # password links (password_link is a new table, create_all) and the key setup minted
    (21, "ALTER TABLE installation ADD COLUMN IF NOT EXISTS installer_key_id integer"),
    (22, "ALTER TABLE membership ADD COLUMN IF NOT EXISTS printers json"),
]

# Data migrations written in Python, so they run the same on Postgres and in the tests' SQLite. Numbered from 1001
# so they never take a number the SQL list above may use next.
THEME_MIGRATION = 1001     # every saved skin and light/dark mode becomes the glass-dark theme (services.themes)
FARM_NAMES_MIGRATION = 1002   # columns named after the farm app the hub no longer supports get neutral names
STORES_MIGRATION = 1003   # orders from more than one store: shop_order.channel and ext_id (services.stores)

# (table, old column, new column)
FARM_RENAMES = [
    ("printer_profile", "wt_printer", "farm_printer"),
    ("model_file", "legacy_farm_uuid", "farm_file_ref"),
    ("model_file", "legacy_farm_error", "farm_file_error"),
    # databases from before the hub's code was published still use the old farm app's own name for these two
    ("model_file", "watchtower_uuid", "farm_file_ref"),
    ("model_file", "watchtower_error", "farm_file_error"),
    ("product", "wt_error", "farm_error"),
    ("shop_order_line", "wt_job_ids", "farm_job_ids"),
    ("shop_order_line", "wt_error", "farm_error"),
    ("maint_plan", "wt_schedule_id", "farm_schedule_id"),
    ("maint_plan", "wt_status", "farm_status"),
    ("maint_plan", "wt_hours_since", "farm_hours_since"),
    ("maint_task", "wt_note", "farm_note"),
    ("quote", "wt_file_uuid", "farm_file_ref"),
    ("quote", "wt_job_ids", "farm_job_ids"),
]
FARM_DROPS = [("product", "wt_mapping_ids")]
# (table, column, old value, new value)
# The old farm app's name in values: "legacy_farm" since the code was published, its own name before.
LEGACY = ("legacy_farm", "watchtower")
FARM_VALUES = [
    *[(t, col, old, "farm") for old in LEGACY
      for t, col in (("printer_profile", "dispatch"), ("print_record", "source"), ("plate_check", "checked_by"),
                     ("maint_task", "done_by"), ("maint_task", "created_by"))],
    *[("job", "kind", f"{old}.{k}", f"farm.{k}") for old in LEGACY for k in ("sync_product", "mirror_model")],
    ("setting", "key", "wt_mirror_library", "farm_mirror_library"),
]
# Background jobs that only served the old farm app: queued ones are dropped.
FARM_DROPPED_JOBS = ("spools.sync", "spoolman.import_legacy_farm", "spoolman.import_watchtower")
OLD = "old:"     # prefix for ids that belonged to the farm an installation moved away from


def migrate_farm_names(c) -> None:
    """Rename the old farm-named columns, drop the ones only that farm used, and neutral values for the rows that
    named it. Runs the same on Postgres and SQLite; columns already renamed (or never there) are skipped."""
    from sqlalchemy import inspect
    insp = inspect(c)
    tables = set(insp.get_table_names())

    def cols(t):
        return {x["name"] for x in insp.get_columns(t)} if t in tables else set()
    for t, old, new in FARM_RENAMES:
        have = cols(t)
        if old in have and new not in have:
            c.execute(text(f"ALTER TABLE {t} RENAME COLUMN {old} TO {new}"))
    for t, col in FARM_DROPS:
        if col in cols(t):
            c.execute(text(f"ALTER TABLE {t} DROP COLUMN {col}"))
    if "spool_link" in tables:
        c.execute(text("DROP TABLE spool_link"))
    insp = inspect(c)
    for t, col, old, new in FARM_VALUES:
        if col in cols(t):
            c.execute(text(f"UPDATE {t} SET {col} = :new WHERE {col} = :old"), {"new": new, "old": old})
    if "kind" in cols("job"):
        for k in FARM_DROPPED_JOBS:
            c.execute(text("DELETE FROM job WHERE kind = :k AND state = 'queued'"), {"k": k})
    if "farm_kind" in cols("model_file"):
        c.execute(text("UPDATE model_file SET farm_file_ref = NULL, farm_kind = NULL "
                       "WHERE farm_kind IS NULL OR farm_kind <> 'openprinthq'"))
    if "kind" in cols("connection"):     # stored credentials for the farm app the hub no longer talks to
        c.execute(text("DELETE FROM connection WHERE kind IN ('legacy_farm', 'watchtower')"))


def migrate_stores(c) -> None:
    """Orders carry the store they came from (channel) and their id there (ext_id), so Shopify and Etsy orders sit
    beside Woo's. Existing orders are Woo orders. Runs the same on Postgres and SQLite (where woo_order_id keeps its
    NOT NULL: SQLite is only for development)."""
    from sqlalchemy import inspect
    insp = inspect(c)
    if not insp.has_table("shop_order"):
        return
    pg = c.dialect.name == "postgresql"
    cols = {x["name"] for x in insp.get_columns("shop_order")}
    if "channel" not in cols:
        c.execute(text("ALTER TABLE shop_order ADD COLUMN channel VARCHAR(12) NOT NULL DEFAULT 'woo'"))
    if "ext_id" not in cols:
        c.execute(text("ALTER TABLE shop_order ADD COLUMN ext_id VARCHAR(40)"))
    c.execute(text("UPDATE shop_order SET ext_id = CAST(woo_order_id AS VARCHAR(40)) "
                   "WHERE ext_id IS NULL AND woo_order_id IS NOT NULL"))
    if "ext_line_id" not in {x["name"] for x in insp.get_columns("shop_order_line")}:
        c.execute(text("ALTER TABLE shop_order_line ADD COLUMN ext_line_id VARCHAR(60)"))
    if pg:
        c.execute(text("ALTER TABLE shop_order ALTER COLUMN woo_order_id DROP NOT NULL"))
        c.execute(text("ALTER TABLE shop_order DROP CONSTRAINT IF EXISTS uq_shop_order_team_woo"))
        c.execute(text("ALTER TABLE shop_order DROP CONSTRAINT IF EXISTS uq_shop_order_team_channel"))
        c.execute(text("ALTER TABLE shop_order ADD CONSTRAINT uq_shop_order_team_channel UNIQUE (team_id, channel, ext_id)"))
    else:
        c.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_shop_order_team_channel "
                       "ON shop_order (team_id, channel, ext_id)"))


def _installation_farm(c) -> str | None:
    from sqlalchemy import inspect
    if not inspect(c).has_table("installation"):
        return None
    return c.execute(text("SELECT farm FROM installation WHERE id = 1")).scalar()


def move_farm_data(c) -> None:
    """The installation moves to the farm from a farm the hub no longer supports (HUB_FARM_MOVE). Ids that
    belonged to the old farm are kept as history under the "old:" prefix, so they never match the new farm's ids;
    jobs still waiting or printing there are closed, since they are not in the new farm; links to its maintenance
    items and files are cleared. Runs after migrate_farm_names."""
    import json
    o = OLD
    c.execute(text("UPDATE farm_job SET status = 'cancelled', error = 'The print farm was changed: send it again.' "
                   "WHERE status NOT IN ('completed', 'failed', 'cancelled')"))
    c.execute(text(f"UPDATE farm_job SET external_id = '{o}' || external_id WHERE external_id NOT LIKE '{o}%'"))
    c.execute(text(f"UPDATE print_record SET external_id = '{o}' || external_id "
                   f"WHERE source = 'farm' AND external_id IS NOT NULL AND external_id NOT LIKE '{o}%'"))
    for col in ("external_job_id", "reprint_job_id"):
        c.execute(text(f"UPDATE plate_check SET {col} = '{o}' || {col} WHERE {col} IS NOT NULL AND {col} NOT LIKE '{o}%'"))
    c.execute(text("UPDATE maint_plan SET farm_schedule_id = NULL, farm_status = NULL, farm_hours_since = NULL"))
    c.execute(text("UPDATE quote SET farm_file_ref = NULL"))
    c.execute(text("UPDATE product SET farm_error = NULL"))
    for table in ("shop_order_line", "quote"):
        for rid, ids in c.execute(text(f"SELECT id, farm_job_ids FROM {table}")).all():
            ids = json.loads(ids) if isinstance(ids, str) else (ids or [])
            new = [x if str(x).startswith(o) else f"{o}{x}" for x in ids]
            if new != ids:
                val = "CAST(:v AS JSON)" if c.dialect.name == "postgresql" else ":v"
                c.execute(text(f"UPDATE {table} SET farm_job_ids = {val} WHERE id = :id"), {"v": json.dumps(new), "id": rid})
    c.execute(text("UPDATE installation SET farm = 'openprinthq', farm_source = 'moved' WHERE id = 1"))


def init_db():
    from . import models  # noqa: F401  (register tables)
    eng = engine()
    pg = eng.dialect.name == "postgresql"
    with eng.begin() as c:
        if pg:   # web workers and the job worker start together; serialize schema work
            c.execute(text("SELECT pg_advisory_xact_lock(424242)"))
        fresh = not eng.dialect.has_table(c, "job")
        # Before any change: an installation on a farm the hub no longer supports stops here untouched, unless the
        # operator asked to move it (services/installation.py, UnsupportedFarm).
        old_farm = None if fresh else _installation_farm(c)
        moving = False
        if old_farm and old_farm != "openprinthq":
            from .services.installation import UnsupportedFarm
            if settings().farm_move != "openprinthq":
                raise UnsupportedFarm(old_farm)
            moving = True
        Base.metadata.create_all(c)
        c.execute(text("CREATE TABLE IF NOT EXISTS schema_version (v integer primary key)"))
        done = {r[0] for r in c.execute(text("SELECT v FROM schema_version"))}
        for v, sql in MIGRATIONS:
            if v in done:
                continue
            if pg and not fresh:
                for stmt in [x for x in sql.split(";") if x.strip()]:
                    c.execute(text(stmt))
            c.execute(text("INSERT INTO schema_version (v) VALUES (:v)"), {"v": v})
        Base.metadata.create_all(c)      # tables a migration rebuilt
        if THEME_MIGRATION not in done:
            from .services.themes import migrate_prefs
            migrate_prefs(c)
            c.execute(text("INSERT INTO schema_version (v) VALUES (:v)"), {"v": THEME_MIGRATION})
        if FARM_NAMES_MIGRATION not in done:
            migrate_farm_names(c)
            c.execute(text("INSERT INTO schema_version (v) VALUES (:v)"), {"v": FARM_NAMES_MIGRATION})
        if STORES_MIGRATION not in done:
            migrate_stores(c)
            c.execute(text("INSERT INTO schema_version (v) VALUES (:v)"), {"v": STORES_MIGRATION})
        if moving:
            move_farm_data(c)
        from .services.teams import bootstrap
        bootstrap(c, fresh)
