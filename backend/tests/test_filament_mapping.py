"""Filament mapping: the options a waiting job shows, mapping it (start, or reslice first), and the alert link."""
import pytest

from tests.test_teams import as_user, people, real_auth  # noqa: F401  (fixtures)

REQS = [{"slot_id": 1, "type": "PLA", "color": "#000000"}, {"slot_id": 2, "type": "PETG", "color": "#FFFFFF"}]


def _printer(pid, name, model, loaded, same=True):
    return {"printer_id": pid, "name": name, "model": model, "connected": True, "reports_filament": True,
            "same_model": same, "loaded": loaded, "match": {"ok": False},
            "slots": [{"slot_id": 1, "suggested": loaded[0]["global_tray_id"], "grade": "close", "delta_e": 3.1},
                      {"slot_id": 2, "suggested": loaded[1]["global_tray_id"], "grade": "different_material",
                       "delta_e": None}]}


class FakeFarm:
    kind = "openprinthq"

    def __init__(self):
        self.maps = []
        self.level = None

    def printers(self):
        return {"printers": [{"id": 1, "name": "H2D"}, {"id": 2, "name": "H2C"}]}

    def filament_options(self, job_id, all_printers=False):
        h2d = _printer(1, "H2D", "H2D", [{"global_tray_id": 0, "type": "PLA", "color": "#101010"},
                                          {"global_tray_id": 1, "type": "PLA", "color": "#FFFFFF"},
                                          {"global_tray_id": 2, "type": "PETG", "color": "#FFFFFF"}])
        h2c = _printer(2, "H2C", "H2C", [{"global_tray_id": 4, "type": "PLA", "color": "#000000"},
                                          {"global_tray_id": 5, "type": "PETG", "color": "#F0F0F0"}], same=False)
        return {"item_id": int(job_id), "job_name": "Gear housing", "status": "pending", "level": "close",
                "sliced_for_model": "H2D", "requirements": REQS, "printers": [h2d] + ([h2c] if all_printers else [])}

    def filament_map(self, job_id, printer_id, slots, remember=True, file_ref=None, by=None):
        self.maps.append((int(job_id), printer_id, slots, remember, file_ref))
        return {"item_id": int(job_id), "confirmed": True}

    def filament_unmatched(self):
        return [{"item_id": 7, "job_name": "Gear housing", "printers": [{"printer_id": 1, "printer_name": "H2D"}]},
                {"item_id": 8, "job_name": "Other", "printers": [{"printer_id": 2, "printer_name": "H2C"}]}]

    def set_job_match_level(self, job_id, level):
        self.level = level
        return {}

    def filament_settings(self):
        return {"level": "close"}


@pytest.fixture
def fake(monkeypatch):
    from app import clients
    from app.services import farm, printer_access
    f = FakeFarm()
    monkeypatch.setattr(clients, "farm", lambda: f)
    monkeypatch.setattr(clients, "farm_kind", lambda team_id=None: "openprinthq")
    monkeypatch.setattr(farm, "_client", lambda: f)
    printer_access._cache.update(at=0.0, printers=[])
    yield f
    printer_access._cache.update(at=0.0, printers=[])


def _job(ext="7", model=False):
    from app.db import session_scope
    from app.models import FarmJob, Model
    import secrets
    with session_scope() as db:
        mid = None
        if model:
            tag = secrets.token_hex(3)
            m = Model(code=f"FM-{tag}", name="Gear housing", slug=f"gear-{tag}")
            db.add(m)
            db.flush()
            mid = m.id
        db.add(FarmJob(external_id=ext, name="Gear housing", status="waiting_filament", model_id=mid,
                       waiting_reason="Filament doesn't match: H2D: filament 2 PETG #FFFFFF"))


def test_options_and_a_same_material_mapping_starts(fake, people, real_auth):
    _job("7")
    c = as_user(people["owner"])
    r = c.get("/api/farm/jobs/7/filament")
    assert r.status_code == 200, r.text
    o = r.json()
    assert [p["name"] for p in o["printers"]] == ["H2D"] and o["can_reslice"] is False
    assert [s["needs_reslice"] for s in o["printers"][0]["slots"]] == [False, True]   # PLA tray offered for PETG
    r = c.post("/api/farm/jobs/7/filament", json={"printer_id": 1, "slots": [{"slot_id": 1, "global_tray_id": 0},
                                                                            {"slot_id": 2, "global_tray_id": 2}]})
    assert r.status_code == 200, r.text
    assert r.json()["started"] is True and r.json()["reslice"] is False
    assert fake.maps == [(7, 1, [{"slot_id": 1, "global_tray_id": 0}, {"slot_id": 2, "global_tray_id": 2}], True, None)]
    # the phone app has the same routes
    assert as_user(people["member"]).get("/api/mobile/v1/farm/jobs/7/filament").status_code == 200


def test_mistakes_are_refused(fake, people, real_auth):
    _job("17")
    c = as_user(people["owner"])
    one = [{"slot_id": 1, "global_tray_id": 0}]
    assert c.post("/api/farm/jobs/17/filament", json={"printer_id": 1, "slots": one}).status_code == 400  # slot 2 missing
    twice = one + [{"slot_id": 2, "global_tray_id": 0}]
    r = c.post("/api/farm/jobs/17/filament", json={"printer_id": 1, "slots": twice})
    assert r.status_code == 400 and "one of the model" in r.json()["detail"]
    assert c.post("/api/farm/jobs/17/filament", json={"printer_id": 9, "slots": one}).status_code == 404
    # A material change needs a reslice, and this job has no model in the hub.
    pla_for_petg = one + [{"slot_id": 2, "global_tray_id": 1}]
    r = c.post("/api/farm/jobs/17/filament", json={"printer_id": 1, "slots": pla_for_petg})
    assert r.status_code == 409 and "sliced again" in r.json()["detail"]
    assert fake.maps == []
    # viewers can look but not map
    assert as_user(people["viewer"]).post("/api/farm/jobs/17/filament",
                                          json={"printer_id": 1, "slots": twice}).status_code == 403


def test_a_material_change_reslices_first(fake, people, real_auth, monkeypatch):
    from types import SimpleNamespace
    from app.db import session_scope
    from app.models import FarmJob, Job
    from app.services import plate_reslice, slicing
    monkeypatch.setattr(slicing, "choose_sources", lambda model, file_id=None: [])
    monkeypatch.setattr(plate_reslice, "profile_for", lambda db, name: SimpleNamespace(id=42, name=name))
    _job("27", model=True)
    c = as_user(people["owner"])
    assert c.get("/api/farm/jobs/27/filament").json()["can_reslice"] is True
    # Another printer model also needs a reslice, even with the same materials.
    r = c.post("/api/farm/jobs/27/filament", json={"printer_id": 2, "slots": [{"slot_id": 1, "global_tray_id": 4},
                                                                             {"slot_id": 2, "global_tray_id": 5}]})
    assert r.status_code == 200, r.text
    assert r.json()["reslice"] is True and "H2C is a H2C" in r.json()["why"][0]
    assert fake.maps == []
    with session_scope() as db:
        job = db.query(Job).filter(Job.kind == "farm.filament_reslice").order_by(Job.id.desc()).first()
        assert job.payload["printer_id"] == 2 and job.payload["profile_id"] == 42
        assert job.payload["materials"] == [{"slot_id": 1, "material": "PLA", "color": "#000000"},
                                            {"slot_id": 2, "material": "PETG", "color": "#F0F0F0"}]
        fj = db.query(FarmJob).filter(FarmJob.external_id == "27").one()
        assert fj.plate_reslice["state"] == "slicing" and fj.plate_reslice["kind"] == "filament"


def test_unmatched_list_level_and_settings(fake, people, real_auth):
    c = as_user(people["owner"])
    assert [j["item_id"] for j in c.get("/api/farm/filament-matching/unmatched").json()["jobs"]] == [7, 8]
    assert c.put("/api/farm/jobs/7/match-level", json={"level": "family"}).json() == {"ok": True, "level": "family"}
    assert fake.level == "family"
    assert c.put("/api/farm/jobs/7/match-level", json={"level": "loose"}).status_code == 400
    assert c.get("/api/mobile/v1/farm/filament-matching/settings").json() == {"level": "close"}


def test_the_alert_opens_the_mapping_screen(monkeypatch):
    from app.db import session_scope
    from app.models import FarmJob
    from app.services import alerts, farm
    sent = []
    monkeypatch.setattr(alerts, "notify", lambda db, kind, title, message, link=None, photo=None, ref=None, subject=None:
                        sent.append((kind, title, message, link)) or ["ntfy"])
    with session_scope() as db:
        fj = FarmJob(external_id="77", name="Gear housing", status="waiting_filament",
                     waiting_reason="Filament doesn't match: H2D: filament 2 PETG #FFFFFF")
        db.add(fj)
        db.flush()
        farm.transition(db, fj, "queued", live=True)
    kind, title, message, link = sent[-1]
    assert kind == "waiting_filament" and title == "Filament needs mapping: Gear housing"
    assert link.endswith("/farm/jobs/77/filament") and "Tap to choose" in message


def test_the_reslice_task_swaps_the_file_in_and_maps_it(fake, monkeypatch):
    from types import SimpleNamespace
    from app.db import session_scope
    from app.models import FarmJob, PlateSlice, PrinterProfile
    from app.services import filament_mapping as fmap, plate_reslice, shop, slicing
    seen = {}

    def slice_into(db, model, prof, ps, opts, actor):
        seen["opts"] = opts
        ps.problems = []
        return {"files": [1]}

    monkeypatch.setattr(slicing, "_slice_into", slice_into)
    monkeypatch.setattr(plate_reslice, "approve", lambda db, ps, actor: None)
    monkeypatch.setattr(plate_reslice, "pick_file", lambda db, fj, ps: SimpleNamespace(id=5))
    monkeypatch.setattr(shop, "ensure_farm_file", lambda f: "88")
    _job("37", model=True)
    with session_scope() as db:
        prof = PrinterProfile(name="H2C", farm_printer="H2C", machine_preset="Bambu Lab H2C 0.4 nozzle")
        db.add(prof)
        db.flush()
        fj = db.query(FarmJob).filter(FarmJob.external_id == "37").one()
        mats = [{"slot_id": 1, "material": "PLA", "color": "#000000"}, {"slot_id": 2, "material": "PETG", "color": "#F0F0F0"}]
        slots = [{"slot_id": 1, "global_tray_id": 4}, {"slot_id": 2, "global_tray_id": 5}]
        out = fmap.run_reslice(db, {"farm_job_id": fj.id, "profile_id": prof.id, "printer_id": 2, "printer_name": "H2C",
                                    "slots": slots, "materials": mats, "remember": True, "actor": "ann"})
        assert out["mapped"] is True and out["file_ref"] == "88"
        assert seen["opts"]["filament_materials"] == [{"material": "PLA", "color": "#000000"},
                                                       {"material": "PETG", "color": "#F0F0F0"}]
        assert fake.maps == [(37, 2, slots, True, "88")]
        assert fj.plate_reslice["state"] == "mapped" and fj.file_ref == "88"
        assert db.query(PlateSlice).filter(PlateSlice.model_id == fj.model_id).one().plate.startswith("std#")
        # leave no printer profile behind for other tests that pick one
        db.query(PlateSlice).filter(PlateSlice.model_id == fj.model_id).delete()
        db.delete(prof)


def test_filament_spec_takes_the_mapped_filaments(monkeypatch):
    from types import SimpleNamespace
    from app.services import slicing
    prof = SimpleNamespace(tools=[{"nozzle": 0.4}], filament_presets={"PLA": "Generic PLA", "PETG": "Generic PETG"},
                           machine_preset="m", engine="orca")
    model = SimpleNamespace(product=SimpleNamespace(filaments=[SimpleNamespace(slot=1, material="PLA", color_hex="#111111"),
                                                               SimpleNamespace(slot=2, material="PLA", color_hex="#EEEEEE")]))
    monkeypatch.setattr(slicing, "ensure_tools", lambda p: p.tools)
    names, colors, _, _ = slicing.filament_spec(model, prof, None)
    assert names == ["Generic PLA", "Generic PLA"] and colors == ["#111111", "#EEEEEE"]
    names, colors, _, _ = slicing.filament_spec(model, prof, None, [{"material": "PLA", "color": "#000000"},
                                                                    {"material": "PETG", "color": "#FFFFFF"}])
    assert names == ["Generic PLA", "Generic PETG"] and colors == ["#000000", "#FFFFFF"]
