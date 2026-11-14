"""The one-time page where a person chooses the password of their LLDAP login, /account/password/<token>, and the
page where someone who forgot theirs asks for such a link, /account/password.

Someone invited who had no login gets a link here; so does the hub's owner, from the installer, and anyone the
hub's owner makes one for from People. Someone who forgot their password asks for one at /account/password (the
sign-in page's "Forgot your password?" goes there): when the hub can send email and the login exists with an email
address, the link goes to that address. The answer on screen is the same either way, so the page cannot be used to
find out which logins exist. The password goes from the one-time page to LLDAP and nowhere else; each link works
once, and a new one cancels the older ones. Only a hash of a link is kept, and the access log never shows one.
Plain server-rendered HTML, so it works before sign-in and without the app's own screens.
"""
import html
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import need
from ..clients import lldap_api
from ..clients.base import ApiError
from ..config import settings
from ..db import get_db
from ..services.teams import find_password_link, new_password_link
from ..tenant import unscoped

router = APIRouter()
log = logging.getLogger(__name__)

RESET_TTL = timedelta(minutes=30)
SENT = "If that account exists, a link is on its way. It works once, for 30 minutes."
CLIENT_LIMIT = (5, 15 * 60)      # requests from one address in 15 minutes
ACCOUNT_LIMIT = (3, 60 * 60)     # links for one login in an hour
_client_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


class _NoTokens(logging.Filter):
    """The access log shows /account/password/*** in place of a link's token."""
    _token = re.compile(r"(/account/password/)[^\s?\"]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._token.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = self._token.sub(r"\1***", record.msg)
        return True


logging.getLogger("uvicorn.access").addFilter(_NoTokens())

TOKEN = r"^[A-Za-z0-9_-]{20,80}$"
MIN_LENGTH = 10

# The shared ArtisanHub look (artisanhub-ui glass-dark values), written out because this page is served on its own,
# without the hub's front end. Fonts are the hub's copies under /theme/fonts (SIL Open Font License).
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Choose your password - ArtisanHub</title>
<style>
@font-face {{ font-family: Manrope; font-weight: 400; src: url(/theme/fonts/manrope-latin-400-normal.woff2) format("woff2"); }}
@font-face {{ font-family: Manrope; font-weight: 600; src: url(/theme/fonts/manrope-latin-600-normal.woff2) format("woff2"); }}
@font-face {{ font-family: Sora; font-weight: 500; src: url(/theme/fonts/sora-latin-500-normal.woff2) format("woff2"); }}
:root {{ --text: #f1f6fa; --text-2: rgba(236,243,248,0.85); --accent: #8fd3ff; --on-accent: #1d2a34;
  --glass: rgba(255,255,255,0.08); --glass-line: rgba(255,255,255,0.16); --field: rgba(20,30,38,0.5);
  --field-line: rgba(255,255,255,0.2); --bad: #ffd0d0; }}
@media (prefers-reduced-transparency: reduce) {{ :root {{ --glass: #26343f; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; color: var(--text);
  background: radial-gradient(80% 75% at 55% 35%, #3f566a 0%, #273642 50%, #172028 100%) fixed #172028;
  font: 14px/1.5 Manrope, system-ui, sans-serif; -webkit-font-smoothing: antialiased; }}
.wrap {{ width: min(520px, 100%); }}
.brand {{ display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 18px;
  font: 600 20px Sora, Manrope, sans-serif; letter-spacing: -0.02em; }}
.tile {{ width: 36px; height: 36px; border-radius: 12px; background: var(--accent); display: grid; place-items: center; }}
main {{ display: flex; flex-direction: column; gap: 16px; padding: 28px; background: var(--glass);
  border: 1px solid var(--glass-line); border-radius: 22px; backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px); }}
h1 {{ font: 500 30px/1.1 Sora, Manrope, sans-serif; letter-spacing: -0.03em; margin: 0; }}
p {{ color: var(--text-2); margin: 0; }}
form {{ display: flex; flex-direction: column; gap: 16px; }}
label {{ display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--text-2); }}
input {{ height: 38px; padding: 0 12px; font: 14px Manrope, system-ui, sans-serif; color: var(--text); background: var(--field);
  border: 1px solid var(--field-line); border-radius: 10px; }}
input:focus {{ outline: none; box-shadow: 0 0 0 2px #172028, 0 0 0 4px var(--accent); }}
button {{ height: 38px; font: 600 14px Manrope, system-ui, sans-serif; color: var(--on-accent); background: var(--accent);
  border: 0; border-radius: 12px; cursor: pointer; }}
.error {{ color: var(--bad); }}
a {{ color: #bfe6ff; text-underline-offset: 2px; }}
.back {{ text-align: center; font-size: 13px; }}
</style></head>
<body><div class="wrap"><div class="brand"><span class="tile"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"
 stroke="#1d2a34" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2 3 7v10l9 5 9-5V7l-9-5z"/><path
 d="M3 7l9 5 9-5M12 12v10"/></svg></span>ArtisanHub</div><main>{body}</main></div></body></html>"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(body=body), status_code=status,
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _form(uid: str, error: str = "") -> HTMLResponse:
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return _page(
        "<h1>Choose your password</h1>"
        f"<p>For your ArtisanHub login, <strong>{html.escape(uid)}</strong>. At least {MIN_LENGTH} characters."
        " This link works once.</p>"
        '<form method="post" autocomplete="on">'
        f'<input type="text" name="username" value="{html.escape(uid)}" autocomplete="username" hidden>'
        '<label for="pw">New password'
        f'<input id="pw" name="password" type="password" minlength="{MIN_LENGTH}" autocomplete="new-password" required></label>'
        '<label for="pw2">The same again'
        '<input id="pw2" name="again" type="password" autocomplete="new-password" required></label>'
        f"{err}<button type=\"submit\">Save and sign in</button></form>",
        400 if error else 200)


def _gone() -> HTMLResponse:
    return _page("<h1>This link does not work</h1><p>It was used already or it expired. Ask whoever sent it for a"
                 " new one.</p>", 410)


# ---- Change your password, signed in (B35) --------------------------------------------------------------------------
# Defined before /account/password/{token}, whose pattern would refuse "change" rather than let it through.

CHANGE_PATH = "/account/password/change"


def _signed_in_login(request: Request, db: Session):
    """The signed-in person and their LLDAP login (the username Authelia gave the hub), or None if not signed in."""
    from ..models import User
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    return user, ((user.username or "").strip() if user else "")


def _change_form(uid: str, error: str = "", done: bool = False, status: int | None = None) -> HTMLResponse:
    if done:
        return _page("<h1>Password changed</h1><p>Your new password works everywhere you sign in as "
                     f"<strong>{html.escape(uid)}</strong> with ArtisanHub.</p>"
                     "<p class=\"back\"><a href=\"/\">Back to the hub</a></p>")
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return _page(
        "<h1>Change your password</h1>"
        f"<p>For your ArtisanHub login, <strong>{html.escape(uid)}</strong>: the one password for everything you sign"
        f" in to with ArtisanHub. At least {MIN_LENGTH} characters.</p>"
        '<form method="post" autocomplete="on">'
        f'<input type="text" name="username" value="{html.escape(uid)}" autocomplete="username" hidden>'
        '<label for="cur">Current password'
        '<input id="cur" name="current" type="password" autocomplete="current-password" required></label>'
        '<label for="pw">New password'
        f'<input id="pw" name="password" type="password" minlength="{MIN_LENGTH}" autocomplete="new-password" required></label>'
        '<label for="pw2">The same again'
        '<input id="pw2" name="again" type="password" autocomplete="new-password" required></label>'
        f'{err}<button type="submit">Change password</button></form>'
        '<p class="back"><a href="/">Back to the hub</a> &middot; <a href="/account/password">Forgot your current'
        " password?</a></p>",
        status or (400 if error else 200))


def _no_login() -> HTMLResponse:
    return _page("<h1>Passwords are not changed here</h1><p>This hub does not manage your login. Change your"
                 " password where you sign in.</p><p class=\"back\"><a href=\"/\">Back to the hub</a></p>", 409)


@router.get(CHANGE_PATH, include_in_schema=False)
def change_page(request: Request, db: Session = Depends(get_db)):
    with unscoped():
        user, uid = _signed_in_login(request, db)
    if user is None:
        return RedirectResponse(f"/auth/login?next={CHANGE_PATH}", status_code=303)
    if not uid or lldap_api.client() is None:
        return _no_login()
    return _change_form(uid)


@router.post(CHANGE_PATH, include_in_schema=False)
def change_set(request: Request, current: str = Form(""), password: str = Form(""), again: str = Form(""),
               db: Session = Depends(get_db)):
    with unscoped():
        user, uid = _signed_in_login(request, db)
    if user is None:
        return RedirectResponse(f"/auth/login?next={CHANGE_PATH}", status_code=303)
    ld = lldap_api.client()
    if not uid or ld is None:
        return _no_login()
    # Limited per login, as asking for a link is: guessing the current password here is no easier than at sign-in.
    if not _client_allowed(f"change:{uid}"):
        return _change_form(uid, "Too many tries. Wait a few minutes and try again.", status=429)
    if len(password) < MIN_LENGTH:
        return _change_form(uid, f"Use at least {MIN_LENGTH} characters for the new password.")
    if password != again:
        return _change_form(uid, "The two new passwords are not the same.")
    try:
        if not ld.check_password(uid, current):
            return _change_form(uid, "The current password is not right.")
        ld.set_password(uid, password)
    except ApiError:
        return _change_form(uid, "The password could not be changed. Try again in a moment.", status=503)
    log.info("password changed for %s", uid)
    return _change_form(uid, done=True)


@router.get("/account/password/{token}", include_in_schema=False)
def password_page(token: str = Path(pattern=TOKEN), db: Session = Depends(get_db)):
    with unscoped():
        link = find_password_link(db, token)
    return _form(link.uid) if link else _gone()


@router.post("/account/password/{token}", include_in_schema=False)
def password_set(token: str = Path(pattern=TOKEN), password: str = Form(""), again: str = Form(""),
                 db: Session = Depends(get_db)):
    with unscoped():
        link = find_password_link(db, token)
        if not link:
            return _gone()
        if len(password) < MIN_LENGTH:
            return _form(link.uid, f"Use at least {MIN_LENGTH} characters.")
        if password != again:
            return _form(link.uid, "The two passwords are not the same.")
        ld = lldap_api.client()
        if ld is None:
            return _page("<h1>Passwords are not set here</h1><p>This hub does not manage logins.</p>", 409)
        try:
            ld.set_password(link.uid, password)
        except ApiError:
            return _form(link.uid, "The password could not be saved. Try again in a moment.")
        link.used_at = datetime.now(timezone.utc)
        db.commit()
    return RedirectResponse("/auth/login", status_code=303)


# ---- Forgot your password: a link by email -------------------------------------------------------------------------

def reset_ready() -> bool:
    """The hub can send a reset link: it has mail set up and keeps logins in LLDAP."""
    return bool(settings().smtp_host) and lldap_api.client() is not None


def _back() -> str:
    return '<p class="back"><a href="/auth/login">Back to sign in</a></p>'


def _reset_form() -> HTMLResponse:
    return _page(
        "<h1>Reset your password</h1>"
        "<p>Enter the username or the email address of your ArtisanHub login. We send a link to choose a new"
        " password to the email address on the login.</p>"
        '<form method="post" action="/account/password" autocomplete="on">'
        '<label for="login">Username or email'
        '<input id="login" name="login" type="text" maxlength="320" autocomplete="username" autocapitalize="none"'
        ' spellcheck="false" required autofocus></label>'
        '<button type="submit">Send reset link</button></form>' + _back())


def _reset_off() -> HTMLResponse:
    return _page(
        "<h1>Reset your password</h1>"
        "<p>This hub cannot send email, so it cannot send you a link. Ask the hub's owner for a new link: they can"
        " make one for you from People.</p>" + _back())


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _client_allowed(ip: str) -> bool:
    n, window = CLIENT_LIMIT
    now = time.monotonic()
    with _hits_lock:
        hits = [t for t in _client_hits.get(ip, []) if now - t < window]
        if len(hits) >= n:
            _client_hits[ip] = hits
            return False
        hits.append(now)
        _client_hits[ip] = hits
        if len(_client_hits) > 10000:   # forget addresses that have gone quiet
            for k in [k for k, v in _client_hits.items() if not v or now - v[-1] >= window]:
                _client_hits.pop(k, None)
    return True


def _fresh_link(db: Session, uid: str, email: str) -> str:
    """A new one-time link for this login; older unused ones stop working."""
    from ..models import PasswordLink
    now = datetime.now(timezone.utc)
    for old in db.execute(select(PasswordLink).where(PasswordLink.uid == uid, PasswordLink.used_at.is_(None))).scalars():
        old.used_at = now
    return new_password_link(db, uid, email, RESET_TTL)


def _account_allowed(db: Session, uid: str) -> bool:
    from ..models import PasswordLink
    n, window = ACCOUNT_LIMIT
    since = datetime.now(timezone.utc) - timedelta(seconds=window)
    rows = db.execute(select(PasswordLink.created_at).where(PasswordLink.uid == uid)).scalars()
    recent = [c for c in rows if (c if c.tzinfo else c.replace(tzinfo=timezone.utc)) > since]
    return len(recent) < n


def send_reset_link(login: str) -> None:
    """Look the login up in LLDAP and, when it exists with an email address, email it a one-time link. Runs after
    the answer has gone, so the time the page takes says nothing about whether the login exists. Says nothing back."""
    from ..db import SessionLocal
    from ..services import emails
    from ..tasks import send_mail
    ld = lldap_api.client()
    if ld is None or not settings().smtp_host:
        return
    login = (login or "").strip().lower()[:320]
    try:
        found = ld.user_by_email(login) if "@" in login else ld.user(login)
    except ApiError:
        log.warning("password reset: LLDAP did not answer")
        return
    email = ((found or {}).get("email") or "").strip().lower()
    if not found or not email or found.get("id") == getattr(ld, "bind_user", None):
        return   # no such login, no address to send to, or the hub's own LLDAP account
    db = SessionLocal()
    try:
        with unscoped():
            if not _account_allowed(db, found["id"]):
                log.info("password reset: limit reached for one login; nothing sent")
                return
            link = _fresh_link(db, found["id"], email)
            db.commit()
    finally:
        db.close()
    mail = emails.password_reset(link, int(RESET_TTL.total_seconds() // 60), settings().public_url)
    try:
        send_mail(mail.subject, mail.text(), to=[email], html=mail.html())
    except Exception as e:  # noqa: BLE001
        log.warning("password reset: the email could not be sent (%s)", type(e).__name__)


@router.get("/account/password", include_in_schema=False)
def reset_page():
    return _reset_form() if reset_ready() else _reset_off()


@router.post("/account/password", include_in_schema=False)
def reset_request(request: Request, background: BackgroundTasks, login: str = Form("")):
    if not reset_ready():
        return _reset_off()
    if not _client_allowed(_client_ip(request)):
        return _page("<h1>Reset your password</h1><p class=\"error\" role=\"alert\">Too many requests from here."
                     " Try again in 15 minutes.</p>" + _back(), 429)
    if login.strip():
        background.add_task(send_reset_link, login)
    return _page("<h1>Reset your password</h1>"
                 f'<p role="status">{html.escape(SENT)}</p>'
                 "<p>Nothing came? Check the spam folder, or ask the hub's owner for a link.</p>" + _back())


# ---- The hub's owner makes a link for someone (People) --------------------------------------------------------------

@router.post("/api/account/password-link/{user_id}")
def owner_password_link(user_id: int, db: Session = Depends(get_db), p=Depends(need("platform"))):
    """A one-time link for this person to choose a new password, for the hub's owner to pass on. Needs LLDAP."""
    from ..models import User
    u = db.get(User, user_id)
    if not u or not u.email:
        raise HTTPException(404, "no one with an email address there")
    ld = lldap_api.client()
    if ld is None:
        raise HTTPException(409, "this hub does not manage logins in LLDAP")
    try:
        found = ld.user_by_email(u.email)
    except ApiError as e:
        raise HTTPException(502, f"LLDAP did not answer: {e.message}") from None
    if not found:
        raise HTTPException(404, f"no login in LLDAP has the email {u.email}")
    with unscoped():
        link = _fresh_link(db, found["id"], u.email)
        db.commit()
    return {"link": link, "user": found["id"], "expires_in_minutes": int(RESET_TTL.total_seconds() // 60)}
