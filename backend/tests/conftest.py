import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("HUB_DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("HUB_DEV_AUTH_BYPASS", "true")


# One environment for the whole run, defined here and nowhere else. It used to live in test_core.py and be imported
# here, which registered it twice: it then ran twice, with a second data folder, and any module that sorted before
# test_core.py and imported the app saw paths from the first.
@pytest.fixture(scope="session", autouse=True)
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("hub")
    os.environ["HUB_DATA_DIR"] = str(d / "data")
    os.environ["HUB_LIBRARY_ROOT"] = str(d / "lib")
    os.environ["HUB_SOURCE_ROOT"] = str(d)
    os.environ["HUB_DATABASE_URL"] = f"sqlite:///{d}/test.db"
    os.environ["HUB_PUBLIC_URL"] = "http://test"
    (d / "data").mkdir()
    (d / "lib").mkdir()
    from app.config import settings
    settings.cache_clear()
    from app.db import init_db
    init_db()
    # These tests model an installation that predates the explicit farm choice, which keeps the per-team
    # inference (services.installation). tests/test_setup_api.py covers new installations.
    from app.db import SessionLocal
    from app.models import Installation
    from app.services import installation
    s = SessionLocal()
    try:
        s.get(Installation, 1).farm_source = "legacy"
        s.commit()
    finally:
        s.close()
    installation.forget()
    from app.tenant import set_team
    set_team(1)          # direct database use in tests acts as the first team
    yield d
