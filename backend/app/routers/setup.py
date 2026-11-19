"""First-run setup API, /api/setup/v1 (invariants 3 and 4).

A stable, versioned API so that first setup can be done from outside the hub: by the hub's own /setup page,
or by the OpenPrintHQ installer. It is authenticated by a one-time setup token: HUB_SETUP_TOKEN when the hub is
started with one, otherwise a token the hub generates and logs once at first start. Once setup is complete
the token stops working and the API refuses further setup. Documented in docs/setup-api.md.
"""

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import need, new_api_key
from ..db import get_db
from ..models import Installation, ReachCheck, Team
from ..services import installation
from ..services.teams import slugify
from ..tenant import unscoped, use_team

router = APIRouter(prefix="/api/setup/v1", tags=["setup"])

API_VERSION = 1
INSTALLER_SCOPES = ["read", "write", "admin"]   # admin includes connecting outside services
_tries: dict[str, list[float]] = {}


def _limit(request: Request) -> None:
    """At most 10 setup attempts a minute from one address, so the token cannot be guessed."""
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")
    now = time.time()
    hits = [t for t in _tries.get(ip, []) if now - t < 60]
    if len(hits) >= 10:
        raise HTTPException(429, "too many setup attempts; wait a minute")
    hits.append(now)
    _tries[ip] = hits
    if len(_tries) > 5000:
        _tries.clear()


class Status(BaseModel):
    api_version: int
    setup_complete: bool
    farm: str | None


@router.get("/status", response_model=Status)
def status(db: Session = Depends(get_db)) -> Status:
    row = db.get(Installation, 1)
    return Status(api_version=API_VERSION, setup_complete=bool(row and row.setup_completed_at),
                  farm=row.farm if row else None)


class InitializeIn(BaseModel):
    farm: Literal["openprinthq"] = "openprinthq"   # the farm, the only farm; kept for older installers
    team_name: str = Field(min_length=1, max_length=120)
    key_name: str = Field(default="OpenPrintHQ installer", min_length=1, max_length=80)


class TeamOut(BaseModel):
    id: int
    name: str
    slug: str


class InitializeOut(BaseModel):
    team: TeamOut
    farm: str
    api_key: str
    note: str


@router.post("/initialize", response_model=InitializeOut)
def initialize(body: InitializeIn, request: Request, db: Session = Depends(get_db)) -> InitializeOut:
    """Choose the installation's farm (invariant 1), name the first team, and mint an API key for whoever runs
    setup, so they can go on to connect services through the normal API. Works once."""
    _limit(request)
    row = db.get(Installation, 1)
    if row is None:
        raise HTTPException(503, "the hub is still starting; try again in a moment")
    if row.setup_completed_at:
        raise HTTPException(409, "setup is already complete")
    auth = request.headers.get("authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token or not installation.token_ok(row, token):
        raise HTTPException(401, "wrong setup token")

    name = body.team_name.strip()
    with unscoped():
        team = db.get(Team, 1)
        slug = slugify(name)
        if db.execute(select(Team.id).where(Team.slug == slug, Team.id != 1)).first():
            slug = f"{slug}-1"
    team.name, team.slug = name, slug
    with use_team(1):
        raw = new_api_key(db, body.key_name.strip(), INSTALLER_SCOPES, team_id=1)
        from ..models import ApiKey
        row.installer_key_id = db.execute(select(ApiKey.id).where(ApiKey.prefix == raw.split("_")[1])).scalar()
    row.farm, row.farm_source = body.farm, "setup"
    row.setup_completed_at = datetime.now(timezone.utc)
    row.setup_token_hash = None
    db.commit()
    installation.forget()
    return InitializeOut(team=TeamOut(id=1, name=name, slug=slug), farm=body.farm, api_key=raw,
                         note="Shown once. Use as: Authorization: Bearer <key>")


# ---------- reachability checks ----------
# Is the hub reachable through its public address? Whoever set the hub up (the installer, or a person with an
# API key) asks for a one-time token, requests /reach/<token> through the address being checked, from outside
# the network if that is the point, and asks whether it arrived. Answering only a token the hub itself issued
# proves the request reached this hub and not something else at that address.

REACH_TTL = timedelta(minutes=30)
REACH_TOKEN = r"^[A-Za-z0-9_-]{24,64}$"


def _reach_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ReachOut(BaseModel):
    token: str
    path: str
    expires_at: datetime


class ReachStatus(BaseModel):
    seen: bool
    seen_at: datetime | None
    via_proxy: bool | None


class PasswordLinkIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class PasswordLinkOut(BaseModel):
    link: str
    expires_in_minutes: int
    user: str


@router.post("/password-link", response_model=PasswordLinkOut)
def password_link(body: PasswordLinkIn, db: Session = Depends(get_db), p=Depends(need("admin"))) -> PasswordLinkOut:
    """A one-time link to the hub's page where the person with this email chooses the password of their LLDAP
    login. For the installer, which makes the owner's login but never sees its password: only the key setup
    minted may ask, since the link sets anyone's password. Needs LLDAP."""
    from ..clients import lldap_api
    row = db.get(Installation, 1)
    if not row or not row.installer_key_id or p.get("key_id") != row.installer_key_id:
        raise HTTPException(403, "only the key made at setup can ask for a password link")
    from ..services.teams import new_password_link
    ld = lldap_api.client()
    if ld is None:
        raise HTTPException(409, "this hub does not manage logins in LLDAP")
    found = ld.user_by_email(body.email)
    if not found:
        raise HTTPException(404, "no login with that email")
    link = new_password_link(db, found["id"], body.email, timedelta(minutes=30))
    db.commit()
    return PasswordLinkOut(link=link, expires_in_minutes=30, user=found["id"])


@router.post("/reach", response_model=ReachOut)
def reach_new(db: Session = Depends(get_db), p=Depends(need("connections"))) -> ReachOut:
    """A new one-time token. Expired ones are cleared out at the same time."""
    now_ = datetime.now(timezone.utc)
    with unscoped():
        for old in db.execute(select(ReachCheck).where(ReachCheck.expires_at < now_)).scalars():
            db.delete(old)
        token = secrets.token_urlsafe(24)
        row = ReachCheck(token_hash=_reach_hash(token), expires_at=now_ + REACH_TTL)
        db.add(row)
        db.commit()
    return ReachOut(token=token, path=f"/api/setup/v1/reach/{token}", expires_at=row.expires_at)


def _reach_row(db: Session, token: str) -> ReachCheck | None:
    with unscoped():
        row = db.execute(select(ReachCheck).where(ReachCheck.token_hash == _reach_hash(token))).scalar()
    if row is None:
        return None
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    return row if expires > datetime.now(timezone.utc) else None


@router.get("/reach/{token}")
def reach_hit(request: Request, token: str = Path(pattern=REACH_TOKEN), db: Session = Depends(get_db)) -> dict:
    """Public: the request being checked. Answers only a token this hub issued and that has not expired."""
    _limit(request)
    row = _reach_row(db, token)
    if row is None:
        raise HTTPException(404, "unknown or expired check")
    if row.seen_at is None:
        row.seen_at = datetime.now(timezone.utc)
        row.via_proxy = bool(request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-proto"))
        db.commit()
    return {"ok": True, "message": "OpenPrintHQ received this check. You can go back to the installer."}


@router.get("/reach/{token}/status", response_model=ReachStatus)
def reach_status(token: str = Path(pattern=REACH_TOKEN), db: Session = Depends(get_db),
                 p=Depends(need("connections"))) -> ReachStatus:
    row = _reach_row(db, token)
    if row is None:
        raise HTTPException(404, "unknown or expired check")
    return ReachStatus(seen=row.seen_at is not None, seen_at=row.seen_at, via_proxy=row.via_proxy)


class UpdateNotice(BaseModel):
    part: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    version: str = Field(min_length=1, max_length=40, pattern=r"^[0-9A-Za-z.+-]+$")
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(default="", max_length=1000)
    ready: bool = True


@router.post("/updates")
def update_notice(body: UpdateNotice, db: Session = Depends(get_db), p=Depends(need("admin"))) -> dict:
    """The installer has a new version of a part (the OrcaSlicer desktop, say) for this installation: an alert in the
    inbox, with a way to the installer's update page, until it is installed (ready false closes it). One open alert
    per part; a newer version updates it. Only the key made at setup may say so."""
    from ..services import alert_inbox, alerts
    row = db.get(Installation, 1)
    if not row or not row.installer_key_id or p.get("key_id") != row.installer_key_id:
        raise HTTPException(403, "only the key made at setup can announce updates")
    ref = f"alert:update:{body.part}"
    if body.ready:
        channels = alerts.notify(db, "update_ready", body.title, body.message, ref=ref,
                                 subject={"part": body.part, "version": body.version})
    else:
        alert_inbox.resolve(db, ref, f"{body.part} {body.version} installed")
        channels = []
    db.commit()
    return {"ok": True, "channels": channels}
