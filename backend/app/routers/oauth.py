"""OAuth 2.1 for the MCP endpoint, so an MCP client such as Claude can add the hub as a connector by URL.

- Discovery: /.well-known/oauth-protected-resource and /.well-known/oauth-authorization-server
- Dynamic client registration: POST /oauth/register
- Authorization code + PKCE (S256 only): GET/POST /oauth/authorize, sign-in is the normal hub Authentik login
- Tokens: POST /oauth/token (authorization_code, refresh_token with rotation), POST /oauth/revoke
Access tokens (aht_...) only work on /mcp. Only hashes are stored. Connected apps are listed and revoked in Settings.
"""
import base64
import hashlib
import html
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..auth import need, Principal
from ..config import settings
from ..db import get_db
from ..models import OAuthClient, OAuthToken

router = APIRouter()

SCOPES = ("read", "write")
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=10)
MAX_CLIENTS = 200


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _h(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def base() -> str:
    return settings().public_url.rstrip("/")


def resource_url() -> str:
    return base() + "/mcp"


def resource_metadata_url() -> str:
    return base() + "/.well-known/oauth-protected-resource"


def _err(code: str, desc: str, status: int = 400):
    return JSONResponse({"error": code, "error_description": desc}, status_code=status, headers={"Cache-Control": "no-store"})


def _redirect_ok(uri: str) -> bool:
    try:
        u = urlparse(uri)
    except ValueError:
        return False
    if u.fragment:
        return False
    if u.scheme == "https" and u.netloc:
        return True
    return u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1")


def _scopes(raw: str | None) -> list[str]:
    want = (raw or "read write").split()
    got = [s for s in SCOPES if s in want]
    return got or ["read"]


# ---------- discovery ----------

@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
def protected_resource():
    return {"resource": resource_url(), "authorization_servers": [base()], "bearer_methods_supported": ["header"],
            "scopes_supported": list(SCOPES), "resource_name": "OpenPrintHQ slicing"}


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
@router.get("/.well-known/oauth-authorization-server/mcp", include_in_schema=False)
@router.get("/.well-known/openid-configuration/mcp", include_in_schema=False)
def auth_server():
    b = base()
    return {"issuer": b, "authorization_endpoint": b + "/oauth/authorize", "token_endpoint": b + "/oauth/token",
            "registration_endpoint": b + "/oauth/register", "revocation_endpoint": b + "/oauth/revoke",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "scopes_supported": list(SCOPES),
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"]}


# ---------- registration ----------

@router.post("/oauth/register", include_in_schema=False)
async def register(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except ValueError:
        return _err("invalid_client_metadata", "JSON body required")
    uris = body.get("redirect_uris") or []
    if not isinstance(uris, list) or not uris or len(uris) > 10 or not all(isinstance(u, str) and _redirect_ok(u) for u in uris):
        return _err("invalid_redirect_uri", "redirect_uris must be https URLs (or http on localhost)")
    grants = body.get("grant_types") or ["authorization_code", "refresh_token"]
    if "authorization_code" not in grants:
        return _err("invalid_client_metadata", "authorization_code grant required")
    if db.execute(select(func.count()).select_from(OAuthClient)).scalar() >= MAX_CLIENTS:
        return _err("invalid_client_metadata", "too many registered clients; remove some in hub Settings", 403)
    method = body.get("token_endpoint_auth_method") or "none"
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        method = "none"
    cid = "mcp_" + secrets.token_urlsafe(18)
    secret = secrets.token_urlsafe(32) if method != "none" else None
    name = str(body.get("client_name") or urlparse(uris[0]).hostname or "MCP client")[:200]
    db.add(OAuthClient(client_id=cid, secret_hash=_h(secret) if secret else None, name=name, redirect_uris=uris))
    db.commit()
    out = {"client_id": cid, "client_id_issued_at": int(_now().timestamp()), "client_name": name, "redirect_uris": uris,
           "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
           "token_endpoint_auth_method": method}
    if secret:
        out["client_secret"] = secret
        out["client_secret_expires_at"] = 0
    return JSONResponse(out, status_code=201, headers={"Cache-Control": "no-store"})


# ---------- authorize ----------

def _who(db: Session, request: Request):
    """The signed-in person and their teams (dev mode: the first team)."""
    from ..models import Team, User
    from ..services.teams import user_teams
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user:
        return user, user_teams(db, user)
    if settings().dev_auth_bypass:
        t = db.get(Team, 1)
        return None, [{"id": t.id, "slug": t.slug, "name": t.name, "role": "owner"}] if t else []
    return None, []

def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
:root{{--bg:#f6f4f0;--card:#fff;--ink:#1f1d1a;--muted:#6b665e;--line:#e3ded5;--accent:#8a5a2b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161513;--card:#201e1b;--ink:#ece8e1;--muted:#a39d93;--line:#34312c;--accent:#d29a5f}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;padding:16px;box-sizing:border-box}}
.c{{background:var(--card);border:1px solid var(--line);border-radius:14px;max-width:440px;width:100%;padding:28px}}
h1{{font-size:20px;margin:0 0 12px}}p{{margin:8px 0;color:var(--muted)}}b{{color:var(--ink)}}ul{{color:var(--muted);padding-left:20px}}
.r{{display:flex;gap:10px;margin-top:22px}}button{{flex:1;font:inherit;padding:10px 14px;border-radius:9px;border:1px solid var(--line);background:transparent;color:var(--ink);cursor:pointer}}
button.ok{{background:var(--accent);border-color:var(--accent);color:#fff}}</style></head><body><div class="c">{body}</div></body></html>""",
                        status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


def _check_authz(db: Session, q: dict):
    """Validate an authorization request. Returns (client, error_page_or_None)."""
    client = db.execute(select(OAuthClient).where(OAuthClient.client_id == (q.get("client_id") or ""))).scalar()
    if not client:
        return None, _page("Unknown app", "<h1>Unknown app</h1><p>This app is not registered with OpenPrintHQ.</p>", 400)
    if (q.get("redirect_uri") or "") not in (client.redirect_uris or []):
        return None, _page("Bad redirect", "<h1>Redirect not allowed</h1><p>The app sent a return address it did not register.</p>", 400)
    return client, None


def _back(redirect_uri: str, **params) -> RedirectResponse:
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(redirect_uri + sep + urlencode({k: v for k, v in params.items() if v is not None}), status_code=302)


@router.get("/oauth/authorize", include_in_schema=False)
def authorize(request: Request, db: Session = Depends(get_db)):
    q = dict(request.query_params)
    client, bad = _check_authz(db, q)
    if bad:
        return bad
    ru, state = q["redirect_uri"], q.get("state")
    if q.get("response_type") != "code":
        return _back(ru, error="unsupported_response_type", state=state)
    if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
        return _back(ru, error="invalid_request", error_description="PKCE with S256 is required", state=state)
    if q.get("resource") and q["resource"].rstrip("/") not in (resource_url(), base()):
        return _back(ru, error="invalid_target", state=state)
    user, teams = _who(db, request)
    if not user and not settings().dev_auth_bypass:
        return RedirectResponse("/auth/login?" + urlencode({"next": str(request.url.path) + "?" + str(request.url.query)}))
    if not teams:
        return _page("No team", "<h1>No team</h1><p>You are not in any team yet.</p>", 403)
    csrf = secrets.token_urlsafe(24)
    request.session["oauth_csrf"] = csrf
    scopes = _scopes(q.get("scope"))
    hidden = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in q.items()
                     if k in ("client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "scope", "resource"))
    who = html.escape(user.label if user else "dev")
    pick = request.session.get("team_id")
    opts = "".join(f'<option value="{t["id"]}"{" selected" if t["id"] == pick else ""}>{html.escape(t["name"])} ({t["role"]})</option>'
                   for t in teams)
    team_field = (f'<p style="margin-top:14px">Team <select name="team_id" style="font:inherit;padding:6px;border-radius:8px">{opts}</select></p>'
                  if len(teams) > 1 else f'<input type="hidden" name="team_id" value="{teams[0]["id"]}">'
                  f'<p>Team: <b>{html.escape(teams[0]["name"])}</b></p>')
    host = html.escape(urlparse(ru).hostname or ru)
    can = "<li>see models, printers and slicing results</li>" + \
          ("<li>start slices and choose production files</li>" if "write" in scopes else "")
    return _page("Allow access", f"""<h1>Allow {html.escape(client.name)} to use OpenPrintHQ?</h1>
<p>Signed in as <b>{who}</b>. It will return to <b>{host}</b> and be able to:</p><ul>{can}</ul>
<p>You can disconnect it any time in hub Settings.</p>
<form method="post" action="/oauth/authorize">{hidden}{team_field}<input type="hidden" name="csrf" value="{csrf}">
<div class="r"><button name="decision" value="deny">Cancel</button><button class="ok" name="decision" value="allow">Allow</button></div></form>""")


@router.post("/oauth/authorize", include_in_schema=False)
async def authorize_post(request: Request, db: Session = Depends(get_db)):
    f = dict(await request.form())
    client, bad = _check_authz(db, f)
    if bad:
        return bad
    ru, state = f["redirect_uri"], f.get("state")
    expect = request.session.pop("oauth_csrf", None)
    if not expect or not secrets.compare_digest(expect, f.get("csrf") or ""):
        return _page("Expired", "<h1>This page expired</h1><p>Go back to the app and connect again.</p>", 400)
    user, teams = _who(db, request)
    if not user and not settings().dev_auth_bypass:
        return _page("Sign in", "<h1>Sign in first</h1><p>Go back to the app and connect again.</p>", 401)
    try:
        team_id = int(f.get("team_id") or 0)
    except ValueError:
        team_id = 0
    team = next((t for t in teams if t["id"] == team_id), None)
    if not team:
        return _page("No access", "<h1>Not your team</h1><p>Pick one of your teams.</p>", 403)
    if f.get("decision") != "allow":
        return _back(ru, error="access_denied", state=state)
    if f.get("code_challenge_method") != "S256" or not f.get("code_challenge"):
        return _back(ru, error="invalid_request", state=state)
    code = secrets.token_urlsafe(32)
    db.execute(delete(OAuthToken).where(OAuthToken.expires_at < _now() - timedelta(days=1)))
    db.add(OAuthToken(kind="code", hash=_h(code), client_id=client.client_id, grant=secrets.token_hex(12),
                      team_id=team["id"], user_id=user.id if user else None,
                      user={"username": user.username if user else "dev", "email": user.email if user else None,
                            "name": user.label if user else "dev"}, scopes=_scopes(f.get("scope")),
                      redirect_uri=ru, code_challenge=f["code_challenge"], resource=f.get("resource") or resource_url(),
                      expires_at=_now() + CODE_TTL))
    db.commit()
    return _back(ru, code=code, state=state, iss=base())


# ---------- token ----------

def _client_auth(db: Session, request: Request, form: dict):
    cid, secret = form.get("client_id"), form.get("client_secret")
    hdr = request.headers.get("authorization") or ""
    if hdr.lower().startswith("basic "):
        try:
            from urllib.parse import unquote
            a, b = base64.b64decode(hdr[6:]).decode().split(":", 1)
            cid, secret = unquote(a), unquote(b)
        except (ValueError, UnicodeDecodeError):
            return None
    client = db.execute(select(OAuthClient).where(OAuthClient.client_id == (cid or ""))).scalar()
    if not client:
        return None
    if client.secret_hash:
        if not secret or not secrets.compare_digest(client.secret_hash, _h(secret)):
            return None
    return client


def _issue(db: Session, src: OAuthToken, client_id: str) -> dict:
    access = "aht_" + secrets.token_urlsafe(32)
    refresh = "ahr_" + secrets.token_urlsafe(40)
    common = dict(client_id=client_id, grant=src.grant, user=src.user, scopes=src.scopes, resource=src.resource,
                  team_id=src.team_id, user_id=src.user_id)
    db.add(OAuthToken(kind="access", hash=_h(access), expires_at=_now() + ACCESS_TTL, **common))
    db.add(OAuthToken(kind="refresh", hash=_h(refresh), expires_at=_now() + REFRESH_TTL, **common))
    db.commit()
    return {"access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()),
            "refresh_token": refresh, "scope": " ".join(src.scopes)}


@router.post("/oauth/token", include_in_schema=False)
async def token(request: Request, db: Session = Depends(get_db)):
    f = dict(await request.form())
    client = _client_auth(db, request, f)
    if not client:
        return _err("invalid_client", "unknown client or bad secret", 401)
    gt = f.get("grant_type")
    if gt == "authorization_code":
        row = db.execute(select(OAuthToken).where(OAuthToken.kind == "code", OAuthToken.hash == _h(f.get("code") or ""))).scalar()
        if not row or row.client_id != client.client_id or row.revoked or _aware(row.expires_at) < _now():
            return _err("invalid_grant", "code is invalid or expired")
        if row.used:   # replayed code: kill everything issued from it
            db.execute(update(OAuthToken).where(OAuthToken.grant == row.grant).values(revoked=True))
            db.commit()
            return _err("invalid_grant", "code already used")
        if (f.get("redirect_uri") or row.redirect_uri) != row.redirect_uri:
            return _err("invalid_grant", "redirect_uri mismatch")
        verifier = f.get("code_verifier") or ""
        calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not verifier or not secrets.compare_digest(calc, row.code_challenge or ""):
            return _err("invalid_grant", "PKCE verification failed")
        row.used = True
        return JSONResponse(_issue(db, row, client.client_id), headers={"Cache-Control": "no-store"})
    if gt == "refresh_token":
        row = db.execute(select(OAuthToken).where(OAuthToken.kind == "refresh", OAuthToken.hash == _h(f.get("refresh_token") or ""))).scalar()
        if not row or row.client_id != client.client_id or row.revoked or _aware(row.expires_at) < _now():
            return _err("invalid_grant", "refresh token is invalid or expired")
        if row.used:   # a rotated refresh token came back: assume theft, revoke the grant
            db.execute(update(OAuthToken).where(OAuthToken.grant == row.grant).values(revoked=True))
            db.commit()
            return _err("invalid_grant", "refresh token already used")
        row.used = True
        if f.get("scope"):
            row.scopes = [s for s in _scopes(f["scope"]) if s in row.scopes] or row.scopes
        return JSONResponse(_issue(db, row, client.client_id), headers={"Cache-Control": "no-store"})
    return _err("unsupported_grant_type", "use authorization_code or refresh_token")


@router.post("/oauth/revoke", include_in_schema=False)
async def revoke(request: Request, db: Session = Depends(get_db)):
    f = dict(await request.form())
    client = _client_auth(db, request, f)
    row = db.execute(select(OAuthToken).where(OAuthToken.hash == _h(f.get("token") or ""))).scalar()
    if row and client and row.client_id == client.client_id:
        db.execute(update(OAuthToken).where(OAuthToken.grant == row.grant).values(revoked=True))
        db.commit()
    return JSONResponse({}, status_code=200)


# ---------- used by auth.current ----------

def principal_for(db: Session, raw: str):
    row = db.execute(select(OAuthToken).where(OAuthToken.kind == "access", OAuthToken.hash == _h(raw))).scalar()
    if not row or row.revoked or _aware(row.expires_at) < _now():
        return None
    if row.last_used_at is None or _aware(row.last_used_at) < _now() - timedelta(minutes=5):
        row.last_used_at = _now()
        db.commit()
    if not row.team_id:
        return None
    from ..models import Team
    from ..services.teams import membership, perms_for_scopes
    scopes = perms_for_scopes(row.scopes or [])
    role = None
    if row.user_id:     # never more than the person's current permissions, and nothing once they leave the team
        m = membership(db, row.team_id, row.user_id)
        if not m:
            return None
        role = m.role
        scopes = [s for s in scopes if s in m.effective_perms]
    team = db.get(Team, row.team_id)
    u = row.user or {}
    return {"username": u.get("username") or u.get("email") or "oauth", "name": u.get("name"),
            "email": u.get("email"), "scopes": scopes, "perms": scopes, "client_id": row.client_id, "user_id": row.user_id,
            "team_id": row.team_id, "team_slug": team.slug, "team_name": team.name, "role": role, "platform_admin": False}


# ---------- Settings: connected apps ----------

@router.get("/api/oauth/connections")
def connections(db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    names = {c.client_id: c.name for c in db.execute(select(OAuthClient)).scalars()}
    rows = db.execute(select(OAuthToken).where(OAuthToken.team_id == p.team_id,
                                               OAuthToken.kind.in_(("access", "refresh")), OAuthToken.revoked.is_(False),
                                               OAuthToken.expires_at > _now())).scalars().all()
    by = {}
    for r in rows:
        g = by.setdefault(r.grant, {"grant": r.grant, "app": names.get(r.client_id, r.client_id),
                                    "user": (r.user or {}).get("name") or (r.user or {}).get("username"),
                                    "scopes": r.scopes, "connected_at": r.created_at, "last_used_at": None})
        g["connected_at"] = min(_aware(g["connected_at"]), _aware(r.created_at))
        if r.last_used_at and (not g["last_used_at"] or _aware(r.last_used_at) > _aware(g["last_used_at"])):
            g["last_used_at"] = r.last_used_at
    return sorted(by.values(), key=lambda x: _aware(x["connected_at"]), reverse=True)


@router.delete("/api/oauth/connections/{grant}")
def disconnect(grant: str, db: Session = Depends(get_db), p: Principal = Depends(need("api_keys"))):
    n = db.execute(update(OAuthToken).where(OAuthToken.grant == grant, OAuthToken.team_id == p.team_id)
                   .values(revoked=True)).rowcount
    db.commit()
    if not n:
        raise HTTPException(404)
    return {"ok": True}
