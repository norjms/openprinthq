"""Teams: switching, members and roles, invites, connected services, and the platform admin view."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal, need, signed_in_user
from ..config import settings
from ..db import get_db
from ..jobs import event
from ..models import Connection, Invite, Membership, PERMS, ROLE_PERMS, ROLES, Team, User
from ..services import connections as conns, teams as tsvc
from ..tenant import bind_session, set_team

router = APIRouter(prefix="/api")


def _iso(d):
    return d.isoformat() if d else None


def _member_view(m: Membership, u: User) -> dict:
    return {"user_id": u.id, "name": u.label, "email": u.email, "username": u.username, "role": m.role,
            "perms": m.effective_perms, "custom": m.perms is not None and m.role != "owner",
            "joined_at": _iso(m.created_at), "last_login_at": _iso(u.last_login_at),
            # the farm's printers they may see and send to: "all", or the chosen printers' keys (owners: all)
            "printers": "all" if (m.role == "owner" or m.printers is None) else list(m.printers)}


def _push_printers(db: Session):
    """People changed: the farm learns who may use which printers (services/printer_access.py)."""
    from ..services import printer_access
    printer_access.push_soon(db)


def _owners_left(db: Session, team_id: int, excluding: int | None = None) -> int:
    q = select(func.count()).select_from(Membership).where(Membership.team_id == team_id, Membership.role == "owner")
    if excluding:
        q = q.where(Membership.user_id != excluding)
    return db.execute(q).scalar()


def _invite_view(i: Invite) -> dict:
    now = datetime.now(timezone.utc)
    exp = i.expires_at if i.expires_at.tzinfo else i.expires_at.replace(tzinfo=timezone.utc)
    return {"id": i.id, "email": i.email, "name": i.name, "kind": i.kind, "team_id": i.team_id,
            "team": tsvc.invite_target(i), "role": i.role, "perms": i.perms, "needs_account": bool(i.ak_url),
            "created_at": _iso(i.created_at), "expires_at": _iso(i.expires_at), "expired": exp < now}


def _send_invite(db: Session, inv: Invite, raw: str, by: User | None) -> dict:
    """Arrange the Authentik login, then return the hub link (and email it when SMTP is set up)."""
    try:
        login = tsvc.provision_login(inv, db)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(502, f"could not set up their sign-in in Authentik: {getattr(e, 'message', e)}")
    db.commit()
    link = f"{settings().public_url}/invite/{raw}"
    target = tsvc.invite_target(inv)
    mailed = False
    if settings().smtp_host:
        try:
            from ..services import emails
            from ..tasks import send_mail
            mail = emails.invite(by.label if by else "", target, inv.role if inv.kind == "join" else None, link,
                                 login.get("signup_url"), tsvc.INVITE_TTL.days, settings().public_url)
            send_mail(mail.subject, mail.text(), to=[inv.email], reply_to=by.email if by and by.email else None,
                      html=mail.html())
            mailed = True
        except Exception:  # noqa: BLE001
            mailed = False
    return {"link": link, "emailed": mailed, "account": login["account"], "signup_url": login.get("signup_url"),
            "note": login.get("note"), "invite": _invite_view(inv)}


def _check_grant(p: Principal, role: str, perms: list[str] | None) -> list[str] | None:
    """Nobody hands out more than they hold; only owners make owners."""
    if role not in ROLES:
        raise HTTPException(400, "unknown role")
    if role == "owner" and p.get("role") != "owner":
        raise HTTPException(403, "only an owner can make someone an owner")
    mine = list(p.get("perms") or [])
    over = [x for x in ROLE_PERMS[role] if x not in mine] if perms is None else []
    if over:
        raise HTTPException(403, f"you can't give the {role} role: it includes {', '.join(over)}, which you don't have")
    try:
        return tsvc.clean_perms(perms, role, mine)
    except PermissionError as e:
        raise HTTPException(403, str(e))


# ---------- switching ----------

class SwitchIn(BaseModel):
    team: str | int


@router.post("/me/team")
def switch_team(body: SwitchIn, request: Request, db: Session = Depends(get_db), user: User = Depends(signed_in_user)):
    if user is None:
        raise HTTPException(400, "not available in dev mode")
    picked = tsvc.pick_team(db, user, body.team, None)
    if not picked:
        raise HTTPException(403, "you are not a member of that team")
    team, m = picked
    request.session["team_id"] = team.id
    user.last_team_id = team.id
    db.commit()
    return {"team": {"id": team.id, "slug": team.slug, "name": team.name, "role": m.role}}


class TeamIn(BaseModel):
    name: str


# ---------- current team ----------

@router.get("/team")
def team_info(db: Session = Depends(get_db), p: Principal = Depends(need("read"))):
    t = db.get(Team, p.team_id)
    rows = db.execute(select(Membership, User).join(User, User.id == Membership.user_id)
                      .where(Membership.team_id == t.id).order_by(Membership.role.desc(), User.name)).all()
    out = {"id": t.id, "slug": t.slug, "name": t.name, "personal": t.personal, "role": p.get("role"),
           "my_perms": p.get("perms") or [], "perm_catalog": PERMS, "role_perms": ROLE_PERMS,
           "members": [_member_view(m, u) for m, u in rows]}
    if "invite" in (p.get("perms") or []):
        invs = db.execute(select(Invite).where(Invite.team_id == t.id, Invite.kind == "join", Invite.accepted_at.is_(None),
                                               Invite.revoked.is_(False)).order_by(Invite.id.desc())).scalars()
        out["invites"] = [_invite_view(i) for i in invs]
    return out


@router.patch("/team")
def rename_team(body: TeamIn, db: Session = Depends(get_db), p: Principal = Depends(need("team"))):
    t = db.get(Team, p.team_id)
    if not body.name.strip():
        raise HTTPException(400, "name the team")
    t.name = body.name.strip()[:120]
    db.commit()
    return {"id": t.id, "name": t.name}


class MemberIn(BaseModel):
    role: str | None = None
    perms: list[str] | None = None
    reset_perms: bool = False


@router.patch("/team/members/{user_id}")
def set_member(user_id: int, body: MemberIn, db: Session = Depends(get_db), p: Principal = Depends(need("people"))):
    m = tsvc.membership(db, p.team_id, user_id)
    if not m:
        raise HTTPException(404)
    if user_id == p.get("user_id"):
        raise HTTPException(400, "you can't change your own role or permissions")
    if m.role == "owner" and p.get("role") != "owner":
        raise HTTPException(403, "only an owner can change an owner")
    role = body.role or m.role
    if m.role == "owner" and role != "owner" and not _owners_left(db, p.team_id, excluding=user_id):
        raise HTTPException(400, "a team needs at least one owner")
    perms = None if body.reset_perms else (body.perms if body.perms is not None else (m.perms if role == m.role else None))
    perms = _check_grant(p, role, perms)
    old = (m.role, m.effective_perms)
    m.role, m.perms = role, perms
    u = db.get(User, user_id)
    if old[0] != role:
        event(db, "team", f"{u.label}: role {old[0]} -> {role}", actor=p.actor)
    if set(old[1]) != set(m.effective_perms):
        added = [x for x in m.effective_perms if x not in old[1]]
        dropped = [x for x in old[1] if x not in m.effective_perms]
        event(db, "team", f"{u.label}: permissions" + (f" +{','.join(added)}" if added else "")
              + (f" -{','.join(dropped)}" if dropped else ""), actor=p.actor)
    _push_printers(db)
    db.commit()
    return _member_view(m, u)


class PrintersIn(BaseModel):
    printers: list[str] | str = "all"


def _printer_owner(p: Principal):
    if p.get("role") != "owner" and not p.get("platform_admin"):
        raise HTTPException(403, "only an owner can choose who uses which printers")


@router.get("/team/printers")
def team_printers(p: Principal = Depends(need("people"))):
    """The farm's printers to choose from on People (id and name)."""
    from ..services import printer_access
    try:
        return {"printers": [{"id": printer_access.key(x), "name": x.get("name")} for x in printer_access.farm_printers()]}
    except Exception as e:  # noqa: BLE001
        return {"printers": [], "error": str(getattr(e, "message", e))[:200]}


@router.put("/team/members/{user_id}/printers")
def set_member_printers(user_id: int, body: PrintersIn, db: Session = Depends(get_db), p: Principal = Depends(need("people"))):
    """Which of the farm's printers a person may see and send to: all of them, or a chosen list (B32)."""
    from ..services import printer_access
    _printer_owner(p)
    m = tsvc.membership(db, p.team_id, user_id)
    if not m:
        raise HTTPException(404)
    if m.role == "owner":
        raise HTTPException(400, "owners always have every printer")
    try:
        m.printers = printer_access.clean(body.printers)
    except ValueError as e:
        raise HTTPException(400, str(e))
    u = db.get(User, user_id)
    event(db, "team", f"{u.label}: printers " + ("all" if m.printers is None else (", ".join(m.printers) or "none")),
          actor=p.actor)
    printer_access.push_soon(db)
    db.commit()
    return _member_view(m, u)


@router.delete("/team/members/{user_id}")
def remove_member(user_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("people"))):
    m = tsvc.membership(db, p.team_id, user_id)
    if not m:
        raise HTTPException(404)
    if user_id == p.get("user_id"):
        raise HTTPException(400, "use Leave team to remove yourself")
    if m.role == "owner" and p.get("role") != "owner":
        raise HTTPException(403, "only an owner can remove an owner")
    if m.role == "owner" and not _owners_left(db, p.team_id, excluding=user_id):
        raise HTTPException(400, "a team needs at least one owner")
    u = db.get(User, user_id)
    db.delete(m)
    _revoke_access(db, p.team_id, user_id)
    event(db, "team", f"Removed {u.label} from the team", actor=p.actor)
    _push_printers(db)
    db.commit()
    signout = tsvc.drop_hub_access_if_unused(db, u)
    return {"ok": True, "hub_access_removed": signout}


@router.post("/team/leave")
def leave(db: Session = Depends(get_db), p: Principal = Depends(need("read"))):
    uid = p.get("user_id")
    m = tsvc.membership(db, p.team_id, uid) if uid else None
    if not m:
        raise HTTPException(400, "not a member")
    if m.role == "owner" and not _owners_left(db, p.team_id, excluding=uid):
        raise HTTPException(400, "make someone else an owner before you leave")
    db.delete(m)
    _revoke_access(db, p.team_id, uid)
    _push_printers(db)
    db.commit()
    tsvc.drop_hub_access_if_unused(db, db.get(User, uid))
    return {"ok": True}


def _revoke_access(db: Session, team_id: int, user_id: int):
    """Someone leaving a team loses its API keys and connected apps right away."""
    from sqlalchemy import update
    from ..models import ApiKey, OAuthToken
    db.execute(update(ApiKey).where(ApiKey.team_id == team_id, ApiKey.user_id == user_id).values(revoked=True))
    db.execute(update(OAuthToken).where(OAuthToken.team_id == team_id, OAuthToken.user_id == user_id).values(revoked=True))


# ---------- invites (to this team) ----------

class InviteIn(BaseModel):
    email: str
    name: str | None = None
    role: str = "member"
    perms: list[str] | None = None


@router.post("/team/invites")
def invite(body: InviteIn, db: Session = Depends(get_db), p: Principal = Depends(need("invite"))):
    perms = _check_grant(p, body.role, body.perms)
    by = db.get(User, p["user_id"]) if p.get("user_id") else None
    try:
        raw, inv = tsvc.create_invite(db, p.team_id, body.email, body.role, by, name=body.name, perms=perms)
    except ValueError as e:
        raise HTTPException(400, str(e))
    event(db, "team", f"Invited {inv.email} as {body.role}", actor=p.actor)
    return _send_invite(db, inv, raw, by)


def _cancel(db: Session, inv: Invite):
    inv.revoked = True
    if inv.ak_invitation and inv.ak_invitation.startswith("lldap:"):
        # The login made for them is removed again, unless they had already chosen its password.
        from ..models import PasswordLink
        uid = inv.ak_invitation.split(":", 1)[1]
        links = db.execute(select(PasswordLink).where(PasswordLink.uid == uid)).scalars().all()
        try:
            from ..clients import lldap_api
            ld = lldap_api.client()
            if ld and not any(link.used_at for link in links):
                ld.delete_user(uid)
        except Exception:  # noqa: BLE001
            pass
        for link in links:
            if not link.used_at:
                db.delete(link)
    elif inv.ak_invitation:
        try:
            from ..clients import authentik_api
            ak = authentik_api.client()
            if ak:
                ak.delete_invitation(inv.ak_invitation)
        except Exception:  # noqa: BLE001
            pass
    db.commit()


@router.delete("/team/invites/{invite_id}")
def revoke_invite(invite_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("invite"))):
    inv = db.get(Invite, invite_id)
    if not inv or inv.team_id != p.team_id or inv.kind != "join":
        raise HTTPException(404)
    _cancel(db, inv)
    return {"ok": True}


# ---------- the invite link (works before sign-in) ----------

async def _maybe_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    from starlette.concurrency import run_in_threadpool
    uid = request.session.get("uid")
    return await run_in_threadpool(db.get, User, uid) if uid else None


@router.get("/invites/{token}")
def invite_info(token: str, db: Session = Depends(get_db), user: User | None = Depends(_maybe_user)):
    inv = tsvc.find_invite(db, token)
    if not inv:
        raise HTTPException(404, "this invite is no longer valid")
    return {"team": tsvc.invite_target(inv), "kind": inv.kind, "role": inv.role, "email": inv.email,
            "signup_url": inv.ak_url, "signed_in": bool(user),
            "matches": bool(user and (user.email or "").lower() == inv.email.lower()),
            "signed_in_as": user.email if user else None}


@router.post("/invites/{token}/accept")
def invite_accept(token: str, request: Request, db: Session = Depends(get_db), user: User = Depends(signed_in_user)):
    if user is None:
        raise HTTPException(400, "not available in dev mode")
    inv = tsvc.find_invite(db, token)
    if not inv:
        raise HTTPException(404, "this invite is no longer valid")
    try:
        m = tsvc.accept_invite(db, inv, user)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    bind_session(db, inv.team_id)
    event(db, "team", f"{user.label} joined as {m.role}", actor=user.username or user.email)
    _push_printers(db)
    db.commit()
    request.session["team_id"] = inv.team_id
    return {"team": {"id": inv.team_id, "name": tsvc.invite_target(inv), "role": m.role}}


# ---------- connected services ----------

@router.get("/connections")
def list_connections(db: Session = Depends(get_db), p: Principal = Depends(need("connections"))):
    from ..services import vision
    if vision.seed_from_legacy(db):
        db.commit()
    rows = {r.kind: r for r in db.execute(select(Connection)).scalars()}
    return [conns.view(rows[k]) if k in rows else conns.empty_view(k) for k in conns.KINDS]


class ConnIn(BaseModel):
    config: dict | None = None
    secrets: dict | None = None
    enabled: bool | None = None


@router.put("/connections/{kind}")
def save_connection(kind: str, body: ConnIn, db: Session = Depends(get_db), p: Principal = Depends(need("connections"))):
    if kind not in conns.KINDS:
        raise HTTPException(404)
    if body.config:
        for f, v in body.config.items():
            if f.endswith("url") and v and not str(v).startswith(("http://", "https://")):
                raise HTTPException(400, f"{f} must start with http:// or https://")
    try:
        row = conns.save(db, kind, body.config, body.secrets, body.enabled)
    except ValueError as e:
        raise HTTPException(400, str(e)[:300])
    event(db, "team", f"Updated connection: {conns.KINDS[kind][0]}", actor=p.actor)
    db.commit()
    return conns.view(row)


@router.post("/connections/{kind}/test")
def test_connection(kind: str, db: Session = Depends(get_db), p: Principal = Depends(need("connections"))):
    if kind not in conns.KINDS:
        raise HTTPException(404)
    row = db.execute(select(Connection).where(Connection.kind == kind)).scalar()
    if not row:
        raise HTTPException(400, "save the connection first")
    conns.forget(p.team_id, kind)
    ok, msg = conns.test(kind)
    row.last_test_ok, row.last_test_msg, row.last_test_at = ok, msg[:500], datetime.now(timezone.utc)
    db.commit()
    return conns.view(row)


@router.post("/connections/{kind}/models")
def connection_models(kind: str, body: ConnIn | None = None, db: Session = Depends(get_db),
                      p: Principal = Depends(need("connections"))):
    """The models an AI connection offers, using what is typed in the form over what is saved."""
    import httpx

    from ..services import vision
    if kind not in vision.KINDS:
        raise HTTPException(404)
    saved = conns.get(kind, required=False) or {}
    row = db.execute(select(Connection).where(Connection.kind == kind)).scalar()
    if row and not saved:           # saved but paused: still list with its values
        saved = {**(row.config or {}), **conns.decrypt(row.secrets_enc)}
    cfg = {**saved, **{k: v for k, v in ((body.config if body else None) or {}).items() if v not in (None, "")},
           **{k: v for k, v in ((body.secrets if body else None) or {}).items() if v}}
    try:
        return {"models": vision.models(kind, cfg)}
    except vision.ProviderError as e:
        raise HTTPException(400, str(e)[:300])
    except httpx.HTTPError as e:
        raise HTTPException(502, f"could not reach it: {type(e).__name__}")


class AiOrderIn(BaseModel):
    order: list[str]


def _ai_order_view(db: Session) -> dict:
    from ..services import vision
    rows = {r.kind: r for r in db.execute(select(Connection)).scalars()}
    out = []
    for k in vision.order(db):
        r = rows.get(k)
        out.append({"kind": k, "label": conns.KINDS[k][0], "connected": bool(r), "enabled": bool(r and r.enabled),
                    "model": (r.config or {}).get("model") if r else None,
                    "last_test_ok": r.last_test_ok if r else None})
    return {"order": out}


@router.get("/ai/order")
def ai_order(db: Session = Depends(get_db), p: Principal = Depends(need("connections"))):
    from ..services import vision
    if vision.seed_from_legacy(db):
        db.commit()
    return _ai_order_view(db)


@router.put("/ai/order")
def ai_order_put(body: AiOrderIn, db: Session = Depends(get_db), p: Principal = Depends(need("connections"))):
    from ..services import vision
    vision.save_order(db, body.order)
    event(db, "team", "Changed the label-reading AI order", actor=p.actor)
    db.commit()
    return _ai_order_view(db)


@router.delete("/connections/{kind}")
def delete_connection(kind: str, db: Session = Depends(get_db), p: Principal = Depends(need("connections"))):
    row = db.execute(select(Connection).where(Connection.kind == kind)).scalar()
    if row:
        db.delete(row)
        event(db, "team", f"Removed connection: {conns.KINDS.get(kind, (kind,))[0]}", actor=p.actor)
        db.commit()
        conns.forget(p.team_id, kind)
    return {"ok": True}


# ---------- platform admins ----------

@router.get("/platform/teams")
def platform_teams(db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    bind_session(db, None, True)
    counts = dict(db.execute(select(Membership.team_id, func.count()).group_by(Membership.team_id)).all())
    from ..models import Model
    models = dict(db.execute(select(Model.team_id, func.count()).group_by(Model.team_id)).all())
    out = [{"id": t.id, "slug": t.slug, "name": t.name, "personal": t.personal, "members": counts.get(t.id, 0),
            "models": models.get(t.id, 0), "source_dirs": t.source_dirs or {}, "storage_prefix": t.storage_prefix,
            "created_at": _iso(t.created_at)} for t in db.execute(select(Team).order_by(Team.id)).scalars()]
    bind_session(db, p.team_id)
    return {"teams": out, "source_root": settings().source_root}


class DirsIn(BaseModel):
    source_dirs: dict


@router.patch("/platform/teams/{team_id}")
def platform_set_dirs(team_id: int, body: DirsIn, db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    from pathlib import Path
    t = db.get(Team, team_id)
    if not t:
        raise HTTPException(404)
    if not settings().source_root and any((body.source_dirs or {}).values()):
        raise HTTPException(400, "this hub has no server folder (HUB_SOURCE_ROOT) to assign folders from")
    root = Path(settings().source_root).resolve()
    clean = {}
    for k in ("deltaprints", "inbox"):
        v = (body.source_dirs or {}).get(k)
        if not v:
            continue
        rp = Path(v).resolve()
        if rp != root and root not in rp.parents:
            raise HTTPException(400, f"{k} must be inside {root}")
        clean[k] = str(rp)
    t.source_dirs = clean
    db.commit()
    return {"id": t.id, "source_dirs": t.source_dirs}


@router.get("/platform/users")
def platform_users(db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    rows = db.execute(select(User).order_by(User.id)).scalars()
    return [{"id": u.id, "name": u.label, "email": u.email, "platform_admin": u.platform_admin,
             "teams": [{"team_id": m.team_id, "role": m.role} for m in u.memberships],
             "last_login_at": _iso(u.last_login_at)} for u in rows]


# ---------- the hub owner adds people ----------

class PersonIn(BaseModel):
    email: str
    name: str | None = None
    mode: str | None = None        # only "join" (B25: an installation has one team, which everyone added joins)
    team_name: str | None = None
    team_id: int | None = None
    role: str = "member"
    perms: list[str] | None = None


@router.post("/platform/people")
def add_person(body: PersonIn, db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    by = db.get(User, p["user_id"]) if p.get("user_id") else None
    # An installation has one team: everyone added joins it, with the role chosen.
    if body.mode not in (None, "", "join"):
        raise HTTPException(400, tsvc.ONE_TEAM_ONLY)
    if body.team_id not in (None, tsvc.ONE_TEAM):
        raise HTTPException(400, tsvc.ONE_TEAM_ONLY)
    if body.role not in ROLES:
        raise HTTPException(400, "unknown role")
    try:
        perms = tsvc.clean_perms(body.perms, body.role, list(PERMS))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    try:
        raw, inv = tsvc.create_invite(db, tsvc.ONE_TEAM, body.email, body.role, by, name=body.name, perms=perms)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _send_invite(db, inv, raw, by)


@router.get("/platform/invites")
def platform_invites(db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    rows = db.execute(select(Invite).where(Invite.accepted_at.is_(None), Invite.revoked.is_(False))
                      .order_by(Invite.id.desc())).scalars()
    return [_invite_view(i) for i in rows]


@router.delete("/platform/invites/{invite_id}")
def platform_cancel_invite(invite_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    inv = db.get(Invite, invite_id)
    if not inv:
        raise HTTPException(404)
    _cancel(db, inv)
    return {"ok": True}


# ---------- OrcaSlicer in the browser: who may open it ----------

ORCA = "OrcaSlicer in the browser"


def _orcaslicer():
    """The LLDAP group for OrcaSlicer in the browser and the LLDAP client, or (None, None) when this hub has none:
    the installation runs no OrcaSlicer in the browser (HUB_ORCASLICER_GROUP empty) or no LLDAP."""
    group = (settings().orcaslicer_group or "").strip()
    if not group:
        return None, None
    from ..clients import lldap_api
    ld = lldap_api.client()
    return (group, ld) if ld else (None, None)


def _directory_error(e: Exception) -> str:
    from ..clients.base import ApiError
    why = e.message if isinstance(e, ApiError) else type(e).__name__
    return f"The hub's user directory (LLDAP) did not take the change: {why}"


@router.get("/platform/apps/orcaslicer")
def orcaslicer_access(db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    """Who may open OrcaSlicer in the browser: the hub's owners always, and everyone in its LLDAP group."""
    group, ld = _orcaslicer()
    if not group:
        return {"available": False}
    # Each person's own desktop: People lists their files, from the installation itself, in the owner's browser.
    desktops = (settings().orcaslicer_desktops or "").strip() == "per-person"
    users = list(db.execute(select(User).order_by(User.id)).scalars())
    owners = [u.id for u in users if u.platform_admin]
    try:
        emails = {(x.get("email") or "").strip().lower() for x in ld.group_members(group)}
    except Exception as e:  # noqa: BLE001
        return {"available": True, "group": group, "owners": owners, "allowed": owners, "error": _directory_error(e),
                "desktops": desktops}
    allowed = [u.id for u in users if u.platform_admin or (u.email or "").strip().lower() in emails]
    return {"available": True, "group": group, "owners": owners, "allowed": allowed, "error": None,
            "desktops": desktops}


class OrcaIn(BaseModel):
    allowed: bool


@router.put("/platform/users/{user_id}/orcaslicer")
def set_orcaslicer_access(user_id: int, body: OrcaIn, db: Session = Depends(get_db),
                          p: Principal = Depends(need("platform"))):
    """Give someone OrcaSlicer in the browser, or take it away: they join or leave its LLDAP group. Asking for what
    is already so changes nothing."""
    group, ld = _orcaslicer()
    if not group:
        raise HTTPException(404, f"This hub doesn't run {ORCA}")
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)
    if u.platform_admin:
        raise HTTPException(400, f"Hub owners can always open {ORCA}")
    if not u.email:
        raise HTTPException(400, f"{u.label} has no email, so their login can't be found")
    try:
        login = ld.user_by_email(u.email)
        if not login:
            raise HTTPException(400, f"{u.label} has no login in the hub's user directory")
        has = group in {g.get("displayName") for g in login.get("groups") or []}
        if body.allowed and not has:
            ld.add_to_group(login["id"], group)
        elif has and not body.allowed:
            ld.remove_from_group(login["id"], group)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, _directory_error(e))
    changed = has != body.allowed
    if changed:
        event(db, "team", f"{u.label}: {ORCA} {'on' if body.allowed else 'off'}", actor=p.actor)
        db.commit()
    return {"user_id": u.id, "allowed": body.allowed, "changed": changed}


# ---------- Every other desktop app (docs/apps.md): who may open it ----------


def _app(app_id: str):
    """A desktop app the installer told the hub it runs, its group and the LLDAP client; 404 when there is none."""
    from ..services import apps
    found = next((a for a in apps.registered() if a["id"] == app_id), None)
    if found is None or app_id == "orcaslicer":
        raise HTTPException(404, "This hub doesn't run that app")
    from ..clients import lldap_api
    ld = lldap_api.client()
    if not ld:
        raise HTTPException(404, "This hub has no user directory to give apps through")
    return found, apps.group(app_id), ld


@router.get("/platform/apps")
def apps_access(db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    """Who may open each desktop app besides OrcaSlicer: the hub's owners always, and everyone in its group."""
    from ..services import apps
    users = list(db.execute(select(User).order_by(User.id)).scalars())
    owners = [u.id for u in users if u.platform_admin]
    out = []
    for a in apps.registered():
        if a["id"] == "orcaslicer":
            continue
        try:
            _, group, ld = _app(a["id"])
        except HTTPException:
            return {"apps": []}  # no user directory: no app can be given to anyone
        try:
            emails = {(x.get("email") or "").strip().lower() for x in ld.group_members(group)}
            error = None
        except Exception as e:  # noqa: BLE001
            emails, error = set(), _directory_error(e)
        allowed = [u.id for u in users if u.platform_admin or (u.email or "").strip().lower() in emails]
        out.append({**a, "group": group, "owners": owners, "allowed": allowed, "error": error})
    return {"apps": out}


@router.put("/platform/users/{user_id}/apps/{app_id}")
def set_app_access(user_id: int, app_id: str, body: OrcaIn, db: Session = Depends(get_db),
                   p: Principal = Depends(need("platform"))):
    """Give someone a desktop app, or take it away: they join or leave its group. Asking for what is already so
    changes nothing."""
    app, group, ld = _app(app_id)
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)
    if u.platform_admin:
        raise HTTPException(400, f"Hub owners can always open {app['title']}")
    if not u.email:
        raise HTTPException(400, f"{u.label} has no email, so their login can't be found")
    try:
        login = ld.user_by_email(u.email)
        if not login:
            raise HTTPException(400, f"{u.label} has no login in the hub's user directory")
        has = group in {g.get("displayName") for g in login.get("groups") or []}
        if body.allowed and not has:
            ld.add_to_group(login["id"], group)
        elif has and not body.allowed:
            ld.remove_from_group(login["id"], group)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, _directory_error(e))
    changed = has != body.allowed
    if changed:
        event(db, "team", f"{u.label}: {app['title']} {'on' if body.allowed else 'off'}", actor=p.actor)
        db.commit()
    return {"user_id": u.id, "app": app_id, "allowed": body.allowed, "changed": changed}


@router.post("/platform/users/{user_id}/remove")
def platform_remove_person(user_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    """Take someone out of every team and out of the hub-users group. Teams they alone own are kept, ownerless,
    for the hub owner to hand over."""
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)
    if u.id == p.get("user_id"):
        raise HTTPException(400, "you can't remove yourself")
    if u.platform_admin:
        raise HTTPException(400, f"hub owners are managed in the sign-in provider ({settings().admin_group})")
    for m in list(u.memberships):
        _revoke_access(db, m.team_id, u.id)
        db.delete(m)
    db.commit()
    removed = tsvc.drop_hub_access_if_unused(db, u)
    return {"ok": True, "hub_access_removed": removed}
