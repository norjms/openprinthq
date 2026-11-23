import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import init_db
from . import auth
from .routers import account, catalog, ops, sourcing, slicing, mcp, oauth, teams, farm, quotes, plugin, mobile, mobile_control, phone_pages, plates, manual_orders, clipper, setup, nav, crafts, brand, shares, filament_mapping
from .routers import push as push_router
from .routers import alerts as alerts_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="OpenPrintHQ", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(SessionMiddleware, secret_key=settings().session_secret, session_cookie="ahub",
                   max_age=60 * 60 * 24 * 14, same_site="lax", https_only=settings().public_url.startswith("https"))
app.include_router(auth.router)
app.include_router(account.router)       # /account/password/<token>, a one-time page
app.include_router(setup.router)         # first-run setup, /api/setup/v1 (docs/setup-api.md)
app.include_router(catalog.router)
app.include_router(ops.router)
app.include_router(sourcing.router)
app.include_router(slicing.router)
app.include_router(mcp.router)
app.include_router(oauth.router)
app.include_router(teams.router)
app.include_router(shares.router)     # shared folders for OrcaSlicer in the browser
app.include_router(filament_mapping.router)   # before farm: /api/farm/jobs/{id}/filament
app.include_router(push_router.router)
app.include_router(alerts_router.router)   # before farm: /api/farm/jobs/{id}/move
app.include_router(farm.router)
app.include_router(plates.router)
app.include_router(manual_orders.router)
app.include_router(quotes.router)
app.include_router(plugin.router)
app.include_router(mobile.router)
app.include_router(mobile_control.router)
app.include_router(clipper.router)        # browser add-on: add a model by URL, send files
app.include_router(nav.router)            # the sidebar as data, shared with the farm
app.include_router(crafts.router)         # crafts the installation shows tools for (docs/crafts.md)
app.include_router(brand.router)          # the shop's own logo (docs/brand.md)
app.include_router(phone_pages.pages)   # /pair, /get-app, /.well-known app links (public)

STATIC = Path(__file__).resolve().parent.parent / "static"


@app.on_event("startup")
def _startup():
    Path(settings().data_dir).mkdir(parents=True, exist_ok=True)
    init_db()


if (STATIC / "_app").exists():
    app.mount("/_app", StaticFiles(directory=STATIC / "_app"), name="app-assets")


# Pages that moved when the sidebar was regrouped (People, Devices, printer profiles under Slicing). Old links,
# bookmarks and QR codes keep working; the query string is kept (a TV's /tv?code=... still fills in its code).
MOVED = {"team": "/people", "platform": "/people?view=hub", "add-on": "/devices", "tv": "/devices",
         "printers": "/slicing?tab=printers"}


def moved(path: str, query: str) -> str | None:
    target = MOVED.get(path.strip("/"))
    if target is None:
        return None
    if query:
        target += ("&" if "?" in target else "?") + query
    return target


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str, request: Request):
    if path.startswith(("api/", "media/", "hooks/", "public/", "auth/")):
        return JSONResponse({"detail": "not found"}, status_code=404)
    target = moved(path, request.url.query)
    if target:
        return RedirectResponse(target, status_code=301)
    f = STATIC / path
    if path and f.is_file():
        if path.startswith("theme/"):    # shared look for the supporting apps; fonts are also used by the sign-in pages
            return FileResponse(f, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=300"})
        return FileResponse(f)
    if path != "setup" and not settings().dev_auth_bypass:
        from .services import installation
        if not installation.state()["setup_complete"]:
            return RedirectResponse("/setup")      # a new hub opens on its first-run page
    if (not request.session.get("uid") and not settings().dev_auth_bypass
            and not path.startswith(("invite/", "setup"))):
        return RedirectResponse(f"/auth/login?next=/{path}")
    index = STATIC / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "frontend not built"}, status_code=503)
