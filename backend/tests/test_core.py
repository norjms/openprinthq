import io
import json
import os
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("HUB_DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("HUB_DEV_AUTH_BYPASS", "true")



def make_png(path: Path, color=(200, 80, 40), size=(900, 700)):
    from PIL import Image
    Image.new("RGB", size, color).save(path)


def make_sliced_3mf(path: Path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", '<model><metadata name="Application">BambuStudio-02.03</metadata></model>')
        z.writestr("Metadata/plate_1.gcode", "; gcode")
        z.writestr("Metadata/project_settings.config", json.dumps({
            "printer_model": "Bambu Lab H2D", "layer_height": "0.2", "wall_loops": "3", "sparse_infill_density": "15%",
            "filament_type": ["PLA", "PLA"], "filament_colour": ["#FFFFFF", "#000000"], "nozzle_diameter": ["0.4"]}))
        z.writestr("Metadata/slice_info.config", """<config><header><header_item key="X-BBL-Client-Type" value="slicer"/></header>
<plate><metadata key="index" value="1"/><metadata key="prediction" value="3600"/><metadata key="weight" value="52.5"/>
<filament id="1" type="PLA" color="#FFFFFF" used_m="10" used_g="40.0"/><filament id="2" type="PLA" color="#000000" used_m="3" used_g="12.5"/></plate></config>""")


def test_threemf(tmp_path):
    from app.services.threemf import parse_3mf
    p = tmp_path / "Widget-BambuStudio.3mf"
    make_sliced_3mf(p)
    r = parse_3mf(str(p), p.name)
    assert r["sliced"] and r["plates"] == 1
    assert r["print_seconds"] == 3600 and r["grams"] == 52.5
    assert [f["grams"] for f in r["filament"]] == [40.0, 12.5]
    assert r["printer"] == "Bambu Lab H2D" and r["settings"]["walls"] == 3.0
    assert r["slicer"] == "BambuStudio"


def test_filament_text():
    from app.services.importer import parse_filament_used
    assert parse_filament_used('Anycubic PLA+ "Black"') == ("PLA+", "Black")
    assert parse_filament_used("Marble Grey PLA") == ("PLA", "Marble Grey")
    assert parse_filament_used(None) == ("PLA", None)


def test_import_and_availability(env, tmp_path):
    from app.db import session_scope
    from app.services import importer, availability
    from app.models import PartCache, SpoolCache, BomPart, Order, OrderLine
    folder = env / "inbox" / "Test Vase"
    folder.mkdir(parents=True)
    make_png(folder / "a.png")
    make_png(folder / "b.png", (10, 10, 200))
    make_sliced_3mf(folder / "vase.3mf")
    (folder / "vase.stl").write_bytes(b"solid x\nendsolid x\n")
    with session_scope() as db:
        m = importer.import_generic_folder(db, folder, {"name": "Test Vase", "category": "Vases", "filament": "Black PLA"})
        assert m.code.startswith("AP-")
        assert len(m.images) == 2 and any(i.role == "hero" for i in m.images)
        assert {f.kind for f in m.files} == {"3mf_sliced", "stl"}
        assert (Path(os.environ["HUB_LIBRARY_ROOT"]) / m.library_rel).is_dir()
        p = m.product
        assert p.filaments[0].grams == 52.5 and p.filaments[0].color_name == "Black"
        p.filaments[0].color_hex = "#000000"
        p.filaments[0].waste_pct = 0
        db.add(PartCache(inventree_part_id=7, name="M3 magnet", in_stock=10, allocated=0))
        p.parts.append(BomPart(inventree_part_id=7, name="M3 magnet", qty=4))
        db.add(SpoolCache(spool_id=1, material="PLA", color_hex="#050505", name="Black", remaining_g=200))
        db.add(SpoolCache(spool_id=2, material="PLA", color_hex="#FFFFFF", name="White", remaining_g=900))
        db.flush()
        a = availability.product_availability(db, p)
        assert a["units"] == 2               # magnets: 10 // 4 = 2 ; filament 200 // 52.5 = 3
        assert a["limiting"] == "M3 magnet"
        o = Order(woo_order_id=1, number="1", status="processing")
        o.lines.append(OrderLine(sku=p.sku, qty=1, product_id=p.id))
        db.add(o)
        db.flush()
        a = availability.product_availability(db, p)
        assert a["units"] == 1               # 6 magnets left after the open order
        p.live = True
        p.target_buffer = 3
        plan = availability.planner(db, "live")
        mag = plan["parts"][0]
        assert mag["required"] == 16 and mag["short"] == 6
        fil = plan["filament"][0]
        assert fil["required_g"] == round(52.5 * 4) and fil["short_g"] == round(52.5 * 4 - 200)
        assert availability.stock_for_woo(p, availability.product_availability(db, p)) == {"p": 1}


def test_api_flow(env):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/models", json={"name": "API Bowl", "category": "Bowls", "price": 24.0, "license_ok": True})
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    buf = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (800, 800), (0, 120, 0)).save(buf, "JPEG")
    r = c.post(f"/api/models/{mid}/images", files=[("files", ("one.jpg", buf.getvalue(), "image/jpeg"))])
    assert r.status_code == 200, r.text
    d = c.get(f"/api/models/{mid}").json()
    assert d["images_all"][0]["role"] == "hero"
    pid = d["product"]["id"]
    r = c.put(f"/api/products/{pid}/variants", json=[{"color_name": "Sage", "color_hex": "#9CAF88"},
                                                    {"color_name": "Black", "color_hex": "#000000"}])
    assert r.status_code == 200 and len(r.json()["variants"]) == 2
    assert r.json()["color_mode"] == "options"
    lst = c.get("/api/models?q=bowl").json()
    assert lst["total"] >= 1
    thumb = c.get(d["images_all"][0]["thumb"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    r = c.post("/api/v1/ingest", json={"name": "Ingested Thing", "category": "Desk", "price": 9.5,
                                       "images_b64": [{"filename": "x.jpg", "data": __import__("base64").b64encode(buf.getvalue()).decode()}]})
    assert r.status_code == 200, r.text
    assert r.json()["model"]["product"]["price"] == 9.5


def test_client_factories_not_shadowed():
    from app import clients
    from app.clients import woo, openprinthq, inventree, spoolman, manyfold, dam
    for f in (woo, openprinthq, inventree, spoolman, manyfold, dam):
        assert callable(f), f
    from app.db import session_scope
    from app.services import connections
    with session_scope() as db:
        connections.save(db, "woo", {"url": "https://shop.example", "verify_tls": True}, {"key": "ck", "secret": "cs"})
    assert callable(clients.woo) and hasattr(clients.woo(), "list_webhooks")



def test_heic_decoder_registered_without_gpl_encoder():
    import importlib.util
    from PIL import Image
    import app.services.images  # noqa: F401  (registers the opener)
    assert ".heic" in Image.registered_extensions()
    assert importlib.util.find_spec("pillow_heif") is None   # GPL x265 build must not be installed
