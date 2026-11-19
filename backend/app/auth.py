"""Who is calling, and for which team.

People sign in with Authentik (OIDC); anyone in an allowed group (HUB_USER_GROUPS, plus HUB_ADMIN_GROUP) may use the
hub. Each person belongs to one or more teams with a role (viewer, member, admin, owner). Every request acts on one
team: the X-Hub-Team header (id or slug), else the team chosen in the session, else the last one used.
Machines use team API keys (Authorization: Bearer ahk_...). MCP clients use OAuth access tokens (aht_..., /mcp only).
"""
import hashlib
import secrets
import ssl
from datetime import datetime, timezone

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import ApiKey, Membership, Team, User
from . import tenant

router = APIRouter()
oauth = OAuth()
_registered = False


def oidc_verify():
    """How the hub checks Authentik's certificate: the system's authorities, plus HUB_OIDC_CA_PEM when set. That is
    for an Authentik whose certificate comes from a private authority, such as one the installer makes for a hub
    used only at home. Public authorities stay trusted either way."""
    pem = (settings().oidc_ca_pem or "").strip()
    if not pem:
        return True
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=pem)
    return ctx


def _client():
    global _registered
    s = settings()
    if not _registered:
        oauth.register("authentik", client_id=s.oidc_client_id, client_secret=s.oidc_client_secret,
                       server_metadata_url=s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
                       client_kwargs={"scope": "openid email profile groups offline_access",
                                      "code_challenge_method": "S256", "verify": oidc_verify()})
        _registered = True
    return oauth.authentik


def allowed_groups() -> set[str]:
    s = settings()
    g = {x.strip() for x in (s.user_groups or "").split(",") if x.strip()}
    if s.admin_group:
        g.add(s.admin_group)
    return g


def safe_next(nxt: str | None) -> str:
    return nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else "/"


@router.get("/auth/login")
async def login(request: Request, next: str = "/"):
    request.session["next"] = safe_next(next)
    return await _client().authorize_redirect(request, settings().public_url + "/auth/callback")


@router.get("/auth/callback")
async def callback(request: Request):
    from starlette.concurrency import run_in_threadpool
    token = await _client().authorize_access_token(request)
    info = token.get("userinfo") or await _client().userinfo(token=token)
    groups = info.get("groups") or []
    allowed = allowed_groups()
    if allowed and not (allowed & set(groups)) and not await run_in_threadpool(_has_invite, info.get("email")):
        raise HTTPException(403, "Your account is not allowed to use OpenPrintHQ yet. Ask a team admin to invite you.")
    user_id = await run_in_threadpool(_login_user, dict(info), list(groups))
    nxt = safe_next(request.session.get("next"))
    request.session.clear()          # new session on sign-in
    request.session["uid"] = user_id
    return RedirectResponse(nxt)


def _has_invite(email: str | None) -> bool:
    """Someone with an open invite may sign in even if they are not in an allowed Authentik group."""
    from .db import session_scope
    from .services.teams import pending_invites
    if not email:
        return False
    with session_scope() as db:
        tenant.bind_session(db, None, True)
        return bool(pending_invites(db, email))


def _login_user(info: dict, groups: list[str]) -> int:
    from .db import session_scope
    from .services.teams import upsert_user
    with session_scope() as db:
        tenant.bind_session(db, None, True)
        return upsert_user(db, info, groups).id


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    if settings().oidc_logout_url:
        return RedirectResponse(settings().oidc_logout_url)
    return RedirectResponse(settings().oidc_issuer.rstrip("/") + "/end-session/")


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_api_key(db: Session, name: str, scopes=None, team_id: int | None = None, user_id: int | None = None) -> str:
    prefix = secrets.token_hex(4)
    raw = f"ahk_{prefix}_{secrets.token_urlsafe(32)}"
    db.add(ApiKey(name=name, prefix=prefix, hash=hash_key(raw), scopes=scopes or ["read", "write"],
                  team_id=team_id or tenant.require_team(db), user_id=user_id))
    db.flush()
    return raw


class Principal(dict):
    @property
    def actor(self):
        return self.get("username") or self.get("email") or self.get("name") or "api"

    @property
    def team_id(self) -> int:
        return self["team_id"]


LEGACY = {"read": "view", "write": "models", "admin": "settings"}


def _perms(p: list[str]) -> dict:
    return {"perms": list(p), "scopes": list(p)}


def _bind(request: Request, db: Session, p: Principal) -> Principal:
    tenant.bind_session(db, p["team_id"])
    request.state.principal = p
    return p


def _dev_principal(db: Session) -> Principal:
    tid = 1
    team = db.get(Team, tid)
    from .models import PERMS
    return Principal(username="dev", name="dev", kind="user", user_id=None, team_id=tid, team_slug=team.slug if team else "",
                     team_name=team.name if team else "", role="owner", platform_admin=True, **_perms(list(PERMS)))


def current(request: Request, db: Session = Depends(get_db)) -> Principal:
    from .services.teams import membership, pick_team
    auth = request.headers.get("authorization") or ""
    raw = auth[7:] if auth.lower().startswith("bearer ") else request.headers.get("x-api-key")
    if raw and raw.startswith("ahk_"):
        parts = raw.split("_")
        if len(parts) >= 3:
            k = db.execute(select(ApiKey).where(ApiKey.prefix == parts[1], ApiKey.revoked.is_(False))).scalar()
            if k and secrets.compare_digest(k.hash, hash_key(raw)) and k.team_id:
                from .services.teams import perms_for_scopes
                now_ = datetime.now(timezone.utc)
                if k.expires_at and (k.expires_at if k.expires_at.tzinfo else k.expires_at.replace(tzinfo=timezone.utc)) <= now_:
                    raise HTTPException(401, "this API key has expired")
                inst = (request.headers.get("x-hub-instance") or "").strip()[:120]
                if k.instance_id:
                    if not inst or not secrets.compare_digest(inst, k.instance_id):
                        raise HTTPException(401, "this API key belongs to another app instance (X-Hub-Instance)")
                elif k.bind_instance:
                    if not inst:
                        raise HTTPException(401, "this key ties itself to the first app instance that uses it: send X-Hub-Instance")
                    k.instance_id = inst
                perms = perms_for_scopes(k.scopes or [])
                role = None
                if k.user_id:   # a person's key never outranks the person, and stops when they leave the team
                    m = membership(db, k.team_id, k.user_id)
                    if not m:
                        raise HTTPException(401, "the person who made this key is no longer in the team")
                    role = m.role
                    perms = [x for x in perms if x in m.effective_perms]
                # clip: the browser add-on's key. It can only add models by link and send their files
                # (routers/clipper.py), and only while its person may add models.
                if "clip" in (k.scopes or []) and k.user_id and "models" in (m.effective_perms if m else []):
                    perms = [*perms, "clip"]
                k.last_used_at = datetime.now(timezone.utc)
                k.last_ip = (request.client.host if request.client else None)
                # owner: the hub owner's own key may provision teams and people (the person must still be the owner)
                owner = False
                if "owner" in (k.scopes or []) and k.user_id:
                    u = db.get(User, k.user_id)
                    owner = bool(u and u.platform_admin)
                db.commit()
                team = db.get(Team, k.team_id)
                return _bind(request, db, Principal(username=f"api:{k.name}", kind="api", user_id=k.user_id, team_id=k.team_id,
                                                    team_slug=team.slug, team_name=team.name, role=role,
                                                    platform_admin=owner, key_id=k.id, key_scopes=list(k.scopes or []),
                                                    **_perms(perms)))
        raise HTTPException(401, "invalid API key")
    if raw and raw.startswith("aht_"):
        # OAuth access tokens are issued to MCP clients and are only accepted on the MCP endpoint.
        if request.url.path.rstrip("/") == "/mcp":
            from .routers.oauth import principal_for
            info = principal_for(db, raw)
            if info:
                return _bind(request, db, Principal(**info, kind="oauth"))
        raise HTTPException(401, "invalid or expired token")
    if settings().dev_auth_bypass and not request.session.get("uid"):
        return _bind(request, db, _dev_principal(db))
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(401, "sign in required")
    picked = pick_team(db, user, request.headers.get("x-hub-team"), request.session.get("team_id"))
    if not picked:
        if request.headers.get("x-hub-team"):
            raise HTTPException(403, "you are not a member of that team")
        raise HTTPException(403, "no_team")
    team, m = picked
    if request.session.get("team_id") != team.id and not request.headers.get("x-hub-team"):
        request.session["team_id"] = team.id
    return _bind(request, db, Principal(username=user.username or user.email, email=user.email, name=user.name, kind="user",
                                        user_id=user.id, team_id=team.id, team_slug=team.slug, team_name=team.name,
                                        role=m.role, platform_admin=user.platform_admin, **_perms(m.effective_perms)))


def need(scope: str):
    """Route guard. Also makes the caller's team the current team for everything the route does."""
    perm = LEGACY.get(scope, scope)

    async def dep(p: Principal = Depends(current)) -> Principal:
        if perm == "platform":
            if not p.get("platform_admin"):
                raise HTTPException(403, "only the hub owner can do this")
        elif perm not in (p.get("perms") or []):
            from .models import PERMS
            what = PERMS.get(perm, perm).lower()
            raise HTTPException(403, f"you don't have permission to {what[0].lower() + what[1:]}" if p.get("role")
                                else f"this key can't {what}")
        tenant.set_team(p["team_id"])
        return p
    return dep


async def signed_in_user(request: Request, db: Session = Depends(get_db)) -> User:
    """A signed-in person, before any team is picked (invites, team switching, creating a team)."""
    from starlette.concurrency import run_in_threadpool
    uid = request.session.get("uid")
    if not uid and settings().dev_auth_bypass:
        return None
    user = await run_in_threadpool(db.get, User, uid) if uid else None
    if not user:
        raise HTTPException(401, "sign in required")
    return user
