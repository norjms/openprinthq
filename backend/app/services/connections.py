"""Per-team connected services. Each team stores its own URLs and credentials; teams may point at the same
instance (for example one InvenTree for two teams) or at their own. Secrets are encrypted with Fernet."""
import base64
import hashlib
import json
import threading
import time

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..tenant import current_team_id, unscoped

# kind -> (label, config fields, secret fields, help)
KINDS = {
    "woo": ("WooCommerce store", ["url", "verify_tls"], ["key", "secret", "webhook_secret"],
            "Store URL plus a REST API key (WooCommerce > Settings > Advanced > REST API, read/write)."),
    "shopify": ("Shopify store", ["shop", "location_id"], ["client_id", "client_secret", "access_token", "webhook_secret"],
                "The store's myshopify.com name, and the client ID and client secret of an app you made in your Shopify "
                "Dev Dashboard and installed on the store (scopes: read and write products, inventory, orders and "
                "merchant-managed fulfillments; read locations). A store with an older custom app can give its Admin "
                "API access token (shpat_...) and that app's API secret as the webhook secret instead. location_id is "
                "optional: the location that holds stock (the main location if empty)."),
    "etsy": ("Etsy shop", ["taxonomy_id", "shipping_profile_id", "return_policy_id", "readiness_state_id", "who_made",
                           "when_made"],
             ["keystring", "shared_secret", "webhook_secret"],
             "The keystring and shared secret of your app at etsy.com/developers/your-apps (add the hub's Etsy "
             "callback address, shown below, as the app's redirect URI). Save, then use Connect Etsy to sign in "
             "to the shop. The rest are the defaults every listing needs: Etsy category (taxonomy id), shipping "
             "profile, return policy, processing time (readiness state), who made it (i_did) and when "
             "(made_to_order). The webhook secret is the signing secret (whsec_...) from Etsy's Webhook portal."),
    "dam": ("ResourceSpace DAM", ["url", "host", "user"], ["key"],
            "API user and its private key (ResourceSpace > My account > API key). Host is the public name the DAM expects."),
    "openprinthq": ("Farm", ["url", "public_url", "target_group", "verify_tls"],
                    ["api_key"],
                    "The farm's address and an API key with control scope (the farm's Settings, API "
                    "keys). "
                    "Jobs go to the printer group named in target_group, or else to printers of the model a file was "
                    "sliced for."),
    "inventree": ("InvenTree", ["url", "host", "public_url"], ["token"], "API token of a user with part and stock access."),
    "spoolman": ("Spoolman", ["url", "public_url"], [], "Spoolman base URL."),
    "manyfold": ("Manyfold", ["url", "public_url", "library_path"], ["client_id", "client_secret"],
                 "OAuth app credentials; library_path is the team library as Manyfold sees it."),
    "alerts": ("Phone alerts", ["ntfy_url", "telegram_chat_id"], ["ntfy_token", "telegram_bot_token", "slack_webhook_url"],
               "Where farm alerts go besides email. ntfy: the full topic URL (https://ntfy.sh/your-topic or your own server) "
               "and an access token if the topic is protected. Telegram: a bot token from @BotFather and your chat id. "
               "Slack: an incoming webhook URL. Fill in any or all; which events go where is on the Farm page."),
    "labelprinter": ("Label printers", ["printers"], [],
                     "Network label printers for spool and location labels. printers is a list: name, kind (brother_ql "
                     "or zpl), host, port (9100), model (QL-820NWB, ...), tape (62x29, 62, 62x100 or 29x90 for Brother; "
                     "WxH in mm for ZPL) and dpi. The phone app adds them under Labels too."),
    "ai_ollama": ("Local AI (Ollama)", ["url", "model"], ["api_key"],
                  "An Ollama server with a model that reads images, for example http://ollama.local:11434. "
                  "Pick the model from the list; it must fit the server's graphics card (gemma4:e4b fits 16 GB). "
                  "The API key is only for an Ollama behind a proxy that asks for one."),
    "ai_openai": ("OpenAI (ChatGPT)", ["model", "base_url"], ["api_key"],
                  "An OpenAI API key (platform.openai.com, API keys; a ChatGPT subscription does not include one). Pick "
                  "a model that reads images from the list. base_url is only for another OpenAI-compatible service."),
    "ai_anthropic": ("Anthropic (Claude)", ["model"], ["api_key"],
                     "An Anthropic API key (console.anthropic.com, API keys). Pick a Claude model from the list."),
    "ai_gemini": ("Google (Gemini)", ["model"], ["api_key"],
                  "A Gemini API key (aistudio.google.com, Get API key). Pick a Gemini 3 or newer model from the list. "
                  "Besides reading labels, it searches Google for the product behind a barcode or a label the phone "
                  "read; that needs a paid-tier key (Google includes 5,000 searches a month, then $14 per 1,000)."),
    "slicer": ("Slicer service", ["url"], ["token"],
               "Optional. Leave unset to use the hub's shared slicer. Set it to send this team's slices to its own "
               "slicer service (the artisan-slicer container, run by you) at this URL with its bearer token."),
}
BOOL_FIELDS = {"verify_tls"}


class NotConnected(RuntimeError):
    def __init__(self, kind: str):
        label = KINDS.get(kind, (kind,))[0]
        super().__init__(f"{label} is not connected for this team (Settings > Connected services)")
        self.kind = kind


def _fernet() -> Fernet:
    raw = settings().connection_key or ("hub-connections:" + settings().session_secret)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def encrypt(d: dict) -> str:
    return _fernet().encrypt(json.dumps(d).encode()).decode()


def decrypt(tok: str | None) -> dict:
    if not tok:
        return {}
    try:
        return json.loads(_fernet().decrypt(tok.encode()))
    except (InvalidToken, ValueError):
        return {}


# ---------- lookup with a short cache (web and worker are separate processes) ----------

_cache: dict = {}
_lock = threading.Lock()
TTL = 20.0


def get(kind: str, team_id: int | None = None, required: bool = True) -> dict | None:
    """Config + secrets for the current team's connection, or None / NotConnected."""
    from ..db import SessionLocal
    from ..models import Connection
    tid = team_id or current_team_id()
    if not tid:
        if required:
            raise NotConnected(kind)
        return None
    key = (tid, kind)
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < TTL:
        cfg = hit[1]
    else:
        s = SessionLocal()
        try:
            with unscoped():
                row = s.execute(select(Connection).where(Connection.team_id == tid, Connection.kind == kind)).scalar()
            cfg = None
            if row and row.enabled:
                cfg = {**(row.config or {}), **decrypt(row.secrets_enc), "_version": row.updated_at.isoformat() if row.updated_at else ""}
        finally:
            s.close()
        with _lock:
            _cache[key] = (now, cfg)
    if not cfg and required:
        raise NotConnected(kind)
    return cfg


def forget(team_id: int, kind: str | None = None):
    with _lock:
        for k in list(_cache):
            if k[0] == team_id and (kind is None or k[1] == kind):
                _cache.pop(k, None)


def is_connected(kind: str, team_id: int | None = None) -> bool:
    return bool(get(kind, team_id, required=False))


# ---------- editing ----------

def view(row) -> dict:
    label, cfields, sfields, help_ = KINDS[row.kind]
    sec = decrypt(row.secrets_enc)
    return {"kind": row.kind, "label": label, "help": help_, "enabled": row.enabled,
            "config": {f: (row.config or {}).get(f) for f in cfields},
            "secrets": {f: bool(sec.get(f)) for f in sfields},
            "last_test_ok": row.last_test_ok, "last_test_msg": row.last_test_msg,
            "last_test_at": row.last_test_at.isoformat() if row.last_test_at else None}


def empty_view(kind: str) -> dict:
    label, cfields, sfields, help_ = KINDS[kind]
    return {"kind": kind, "label": label, "help": help_, "enabled": False, "config": {f: None for f in cfields},
            "secrets": {f: False for f in sfields}, "last_test_ok": None, "last_test_msg": None, "last_test_at": None,
            "missing": True}


def save(db: Session, kind: str, config: dict | None, secrets: dict | None, enabled: bool | None = None):
    """Secrets: a value replaces, "" clears, a missing key keeps the stored one."""
    from ..models import Connection
    if kind not in KINDS:
        raise ValueError(f"unknown service {kind}")
    _, cfields, sfields, _ = KINDS[kind]
    row = db.execute(select(Connection).where(Connection.kind == kind)).scalar()
    if not row:
        from .installation import connect_allowed
        connect_allowed(kind, existing=False)
        row = Connection(kind=kind, config={}, enabled=True)
        db.add(row)
    cfg = dict(row.config or {})
    for f in cfields:
        if config and f in config:
            v = config[f]
            if f in BOOL_FIELDS:
                v = bool(v) if v is not None else True
            elif f == "printers":
                if isinstance(v, str):
                    v = json.loads(v or "[]")
                if not isinstance(v, list):
                    raise ValueError("printers is a list of label printers")
                from .labels import validate_printer
                v = [{**validate_printer(x), "id": int(x.get("id") or i)} for i, x in enumerate(v, 1)]
            elif isinstance(v, str):
                v = v.strip().rstrip("/") if f.endswith("url") else v.strip()
            cfg[f] = v
    row.config = cfg
    sec = decrypt(row.secrets_enc)
    for f in sfields:
        if secrets and f in secrets:
            if secrets[f]:
                sec[f] = secrets[f]
            else:
                sec.pop(f, None)
    row.secrets_enc = encrypt(sec) if sec else None
    if enabled is not None:
        row.enabled = enabled
    row.last_test_ok = None
    row.last_test_msg = None
    db.flush()
    forget(row.team_id, kind)
    if kind == "openprinthq" and cfg.get("url"):
        from .installation import record_farm
        record_farm(db)
    return row


def env_seed() -> dict:
    """The single-team env configuration, used once to fill the first team's connections."""
    s = settings()
    out = {}
    if s.woo_key:
        out["woo"] = ({"url": s.woo_url, "verify_tls": s.woo_verify_tls},
                      {"key": s.woo_key, "secret": s.woo_secret, "webhook_secret": s.woo_webhook_secret})
    if s.rs_key:
        out["dam"] = ({"url": s.rs_url, "host": s.rs_host, "user": s.rs_user}, {"key": s.rs_key})
    if s.inventree_token:
        out["inventree"] = ({"url": s.inventree_url, "host": s.inventree_host, "public_url": s.inventree_public_url},
                            {"token": s.inventree_token})
    if s.spoolman_url:
        out["spoolman"] = ({"url": s.spoolman_url, "public_url": s.spoolman_public_url}, {})
    if s.manyfold_client_id:
        out["manyfold"] = ({"url": s.manyfold_url, "public_url": s.manyfold_public_url, "library_path": s.manyfold_library_path},
                           {"client_id": s.manyfold_client_id, "client_secret": s.manyfold_client_secret})
    return out


def test(kind: str) -> tuple[bool, str]:
    """Try the current team's connection. Returns (ok, message)."""
    from .. import clients
    if kind.startswith("ai_"):
        from .vision import test as ai_test
        return ai_test(kind)
    try:
        if kind == "woo":
            clients.woo().system_status()
        elif kind in ("shopify", "etsy"):
            from .stores import test as store_test
            return store_test(kind)
        elif kind == "dam":
            clients.dam().fields()
        elif kind == "openprinthq":
            clients.openprinthq().ping()
        elif kind == "inventree":
            clients.inventree().info()
        elif kind == "spoolman":
            clients.spoolman().info()
        elif kind == "slicer":
            h = clients.slicer().health()
            if not h.get("ok"):
                return False, "the slicer answered but is not healthy"
            return True, "connected: " + ", ".join(f"{k} {v}" for k, v in (h.get("engines") or {}).items())
        elif kind == "labelprinter":
            from .labels import printers, probe
            ps = printers()
            if not ps:
                return False, "no label printers in the list"
            res = [(p["name"], *probe(p)) for p in ps]
            bad = [f"{n}: {m}" for n, ok, m in res if not ok]
            return (not bad), ("; ".join(bad) if bad else f"{len(res)} label printer(s) reachable")
        elif kind == "alerts":
            from .alerts import send_test
            return send_test()
        elif kind == "manyfold":
            if not clients.manyfold().healthy():
                return False, "Manyfold did not answer"
        return True, "connected"
    except NotConnected as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {getattr(e, 'message', e)}"[:400]
