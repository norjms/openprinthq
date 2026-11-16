"""Phone app notifications (services/push.py): the disclaimer, each person's choice, their phones, and the
installation's relay settings. The same routes under /api/push (web) and /api/mobile/v1/push (apps)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, need
from ..db import get_db
from ..jobs import event
from ..kv import set_setting
from ..models import PushDevice, User
from ..services import push

router = APIRouter(tags=["push"])
PREFIXES = ("/api/push", "/api/mobile/v1/push")


class DeviceIn(BaseModel):
    platform: str = Field(pattern="^(apns|fcm)$")
    token: str = Field(min_length=20, max_length=4096)
    public_key: str = Field(min_length=40, max_length=64)
    apns_env: str = Field(default="production", pattern="^(production|sandbox)$")
    apns_topic: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=120)
    app_version: str | None = Field(default=None, max_length=40)


class ChoiceIn(BaseModel):
    choice: str | None = Field(default=None, pattern="^(on|off)$")


class SettingsIn(BaseModel):
    mode: str | None = Field(default=None, pattern="^(on|off)$")
    relay_url: str | None = Field(default=None, max_length=300)


def _user(db: Session, p: Principal) -> User:
    u = db.get(User, p.get("user_id")) if p.get("user_id") else None
    if u is None:
        raise HTTPException(400, "phone notifications belong to a person: use a key made by a person, or sign in")
    return u


def _state(db: Session, p: Principal) -> dict:
    u = db.get(User, p.get("user_id")) if p.get("user_id") else None
    mine = db.execute(select(PushDevice).where(PushDevice.user_id == u.id)).scalars().all() if u else []
    url, mode = push.relay_url(db), push.mode(db)
    return {
        "mode": mode, "relay_url": url, "available": mode == "on" and bool(url),
        "choice": push.choice(u),
        # Opted out: alerts by email, and filament mapping stays on the hub's web pages (the app sends you there).
        "mapping_in_app": push.choice(u) != "off",
        "devices": [push.view(d) for d in mine],
        "this_device": next((d.id for d in mine if p.get("key_id") and d.api_key_id == p.get("key_id")), None),
        "disclaimer": push.disclaimer(db),
    }


def _get_state(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    return _state(db, p)


def _register(body: DeviceIn, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    u = _user(db, p)
    if push.mode(db) != "on" or not push.relay_url(db):
        raise HTTPException(409, "phone notifications are off for this installation")
    if not push.public_key_ok(body.public_key):
        raise HTTPException(422, "public_key is not an X25519 public key (32 bytes, base64url)")
    d = push.register(db, u.id, p.get("key_id"), body.model_dump())
    if push.choice(u) != "on":
        push.set_choice(db, u, "on")
    event(db, "push", f"{u.label} turned on phone notifications ({d.label or d.platform})", actor=u.label)
    db.commit()
    return _state(db, p)


def _set_choice(body: ChoiceIn, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    u = _user(db, p)
    push.set_choice(db, u, body.choice)
    what = {"on": "turned on phone notifications", "off": "chose email instead of phone notifications",
            None: "turned off phone notifications"}[body.choice]
    event(db, "push", f"{u.label} {what}", actor=u.label)
    db.commit()
    return _state(db, p)


def _remove(device_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    u = _user(db, p)
    d = db.get(PushDevice, device_id)
    if not d or d.user_id != u.id:
        raise HTTPException(404, "no such phone")
    db.delete(d)
    db.commit()
    return _state(db, p)


def _test(db: Session = Depends(get_db), p: Principal = Depends(need("view"))):
    u = _user(db, p)
    try:
        out = push.test(db, u)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return {"ok": True, "detail": out}


def _settings(body: SettingsIn, db: Session = Depends(get_db), p: Principal = Depends(need("settings"))):
    if body.mode is not None:
        set_setting(db, "push_mode", body.mode)
        event(db, "push", f"Phone notifications through the relay turned {body.mode}", actor=p.get("username"))
    if body.relay_url is not None:
        url = body.relay_url.strip().rstrip("/")
        if url and not url.startswith("https://") and not url.startswith("http://localhost"):
            raise HTTPException(422, "the relay address must start with https://")
        set_setting(db, "push_relay_url", url or None)
        push._status_cache.pop(url, None)
        event(db, "push", f"Push relay set to {url or 'the default'}", actor=p.get("username"))
    db.commit()
    return _state(db, p)


for _pre in PREFIXES:
    router.add_api_route(_pre + "/state", _get_state, methods=["GET"])
    router.add_api_route(_pre + "/devices", _register, methods=["POST"])
    router.add_api_route(_pre + "/devices/{device_id}", _remove, methods=["DELETE"])
    router.add_api_route(_pre + "/choice", _set_choice, methods=["PUT"])
    router.add_api_route(_pre + "/devices/test", _test, methods=["POST"])
    router.add_api_route(_pre + "/settings", _settings, methods=["PUT"])
