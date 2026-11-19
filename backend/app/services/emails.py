"""The emails the hub sends, each as plain text and as HTML in the hub's look.

Email programs do not blur or load styles, so the HTML is tables with inline styles: the page's dark blue,
a solid card (#2f3d48) where the screens have glass, the same border, type and accent. Fonts are named, never
loaded; each program falls back to its own. Every colour of text keeps 4.5:1 on what it sits on. The plain
text says the same, for programs that show only text.

Every email the hub sends is made here: invitations (with the link to choose a password when the person has
no login yet), password resets, farm alerts, running out of supplies, and the two license notices.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from urllib.parse import urlparse

# Colours, from the hub's theme, made solid where email programs cannot mix them.
PAGE = "#273642"
OUTER = "#172028"
CARD = "#2f3d48"
LINE = "#4a5660"  # rgba(255, 255, 255, 0.16) over the page
TEXT = "#f1f6fa"
SECONDARY = "#cfd8de"  # rgba(236, 243, 248, 0.85) over the card
MUTED_ON_PAGE = "#ced7dd"  # rgba(236, 243, 248, 0.85) over the page
LINK = "#bfe6ff"
ACCENT = "#8fd3ff"
ON_ACCENT = "#1d2a34"
UI_FONT = "Manrope, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
TITLE_FONT = "Sora, Manrope, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


@dataclass
class Email:
    subject: str
    heading: str
    paragraphs: list[str]
    buttons: list[tuple[str, str]] = field(default_factory=list)
    items: list[tuple[str, str | None]] = field(default_factory=list)  # (text, link or None)
    note: str = ""
    reason: str = ""
    sender: str = ""

    def text(self) -> str:
        out = [self.heading, ""]
        for p in self.paragraphs:
            out += [p, ""]
        for text, link in self.items:
            out.append(f"- {text}" + (f"\n  {link}" if link else ""))
        if self.items:
            out.append("")
        for label, url in self.buttons:
            out.append(f"{label}: {url}")
        if self.buttons:
            out.append("")
        if self.note:
            out += [self.note, ""]
        out.append(_footer(self.sender, self.reason))
        return "\n".join(out).rstrip("\n") + "\n"

    def html(self) -> str:
        return _html(self)


def _footer(sender: str, reason: str) -> str:
    where = f"Sent by OpenPrintHQ at {sender}." if sender else "Sent by your OpenPrintHQ."
    return f"{where} {reason}".strip()


def _host(url: str) -> str:
    return urlparse(url).hostname or url


def _button(label: str, url: str, primary: bool) -> str:
    bg, color, border = (ACCENT, ON_ACCENT, ACCENT) if primary else (CARD, TEXT, "#5f6b75")
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate">'
        f'<tr><td bgcolor="{bg}" style="background-color:{bg};border:1px solid {border};border-radius:12px">'
        f'<a href="{escape(url)}" style="display:inline-block;padding:12px 18px;font-family:{UI_FONT};'
        f'font-size:14px;font-weight:600;line-height:18px;color:{color};text-decoration:none;'
        f'border-radius:12px">{escape(label)}</a></td></tr></table>'
    )


def _html(e: Email) -> str:
    p_style = f"margin:0 0 14px;font-family:{UI_FONT};font-size:15px;line-height:22px;color:{SECONDARY}"
    body = "".join(f'<p style="{p_style}">{escape(p)}</p>' for p in e.paragraphs)
    if e.items:
        rows = []
        for text, link in e.items:
            linked = (
                f'<br><a href="{escape(link)}" style="color:{LINK};font-size:13px;word-break:break-all">'
                f"{escape(link)}</a>"
                if link
                else ""
            )
            rows.append(
                f'<tr><td style="padding:10px 0;border-top:1px solid {LINE};font-family:{UI_FONT};'
                f'font-size:14px;line-height:20px;color:{TEXT}">{escape(text)}{linked}</td></tr>'
            )
        body += (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:0 0 14px">{"".join(rows)}</table>'
        )
    for n, (label, url) in enumerate(e.buttons):
        body += f'<div style="margin:0 0 10px">{_button(label, url, n == 0)}</div>'
    if e.note:
        body += (
            f'<p style="margin:8px 0 0;font-family:{UI_FONT};font-size:12px;line-height:18px;color:{SECONDARY}">'
            f"{escape(e.note)}</p>"
        )
    preheader = escape(e.paragraphs[0] if e.paragraphs else e.heading)
    footer = escape(_footer(e.sender, e.reason))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title>{escape(e.subject)}</title>
</head>
<body style="margin:0;padding:0;background-color:{OUTER}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{OUTER}">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{OUTER}" style="background-color:{OUTER}">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAGE}" style="max-width:600px;background-color:{PAGE};border-radius:22px">
<tr><td style="padding:32px 28px">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 22px"><tr>
<td width="36" height="36" align="center" valign="middle" bgcolor="{ACCENT}" style="width:36px;height:36px;background-color:{ACCENT};border-radius:12px;font-family:{UI_FONT};font-size:13px;font-weight:700;color:{ON_ACCENT}">AH</td>
<td style="padding-left:10px;font-family:{TITLE_FONT};font-size:20px;font-weight:600;letter-spacing:-0.4px;color:{TEXT}">OpenPrintHQ</td>
</tr></table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{CARD}" style="background-color:{CARD};border:1px solid {LINE};border-radius:22px">
<tr><td style="padding:28px">
<h1 style="margin:0 0 14px;font-family:{TITLE_FONT};font-size:28px;line-height:32px;font-weight:500;letter-spacing:-0.8px;color:{TEXT}">{escape(e.heading)}</h1>
{body}
</td></tr>
</table>
<p style="margin:18px 0 0;font-family:{UI_FONT};font-size:12px;line-height:18px;color:{MUTED_ON_PAGE}">{footer}</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


# The emails


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def invite(by: str, target: str, role: str | None, link: str, signup_url: str | None, days: int,
           public_url: str) -> Email:
    """An invitation. Someone with no login yet first chooses a password (LLDAP) or makes their login
    (Authentik), then accepts."""
    who = by or "The hub's owner"
    as_role = f" as {_article(role)} {role}" if role else ""
    paragraphs = [f"{who} invited you to {target} on OpenPrintHQ{as_role}."]
    if signup_url:
        paragraphs.append("First choose the password for your login, then accept the invitation.")
        buttons = [("Choose your password", signup_url), ("Accept the invitation", link)]
    else:
        buttons = [("Accept the invitation", link)]
    return Email(
        subject=f"{who} invited you to {target} on OpenPrintHQ",
        heading="You are invited",
        paragraphs=paragraphs,
        buttons=buttons,
        note=f"Use the email address this was sent to. The links work for {days} days.",
        reason="You get this because someone invited you to this hub.",
        sender=_host(public_url),
    )


def password_reset(link: str, minutes: int, public_url: str) -> Email:
    """A one-time link to choose a new password, for someone who asked for one on the hub's reset page."""
    return Email(
        subject="Reset your OpenPrintHQ password",
        heading="Reset your password",
        paragraphs=[
            "Someone asked for a new password for your OpenPrintHQ login. If it was you, choose one with the link below.",
            "If it was not you, ignore this email: your password stays as it is.",
        ],
        buttons=[("Choose a new password", link)],
        note=f"The link works once, for {minutes} minutes. Asking again replaces it with a new one.",
        reason="You get this because someone asked to reset the password of your login.",
        sender=_host(public_url),
    )


_ALERTS = "You get this because you are on this hub's alert list."


def farm_alert(title: str, message: str, link: str | None, public_url: str) -> Email:
    return Email(
        subject=f"[Farm] {title}",
        heading=title,
        paragraphs=[p for p in message.split("\n\n") if p.strip()] or [title],
        buttons=[("Open the farm", link)] if link else [],
        reason=_ALERTS,
        sender=_host(public_url),
    )


def out_of_supplies(product: str, sku: str, lines: list[str], public_url: str) -> Email:
    return Email(
        subject=f"Out of supplies: {product}",
        heading="Out of supplies",
        paragraphs=[
            f"{product} ({sku}) is live on the store but you are out of supplies for it.",
            "Its store stock was set to 0, so no new orders can come in.",
        ],
        items=[(line, None) for line in lines],
        buttons=[("Open the planner", f"{public_url}/planner")],
        reason=_ALERTS,
        sender=_host(public_url),
    )


def license_reminder(notes: list[str], public_url: str) -> Email:
    return Email(
        subject="OpenPrintHQ: commercial license reminder",
        heading="Commercial licenses",
        paragraphs=["Commercial licenses you sell under need attention:"],
        items=[(note, None) for note in notes],
        buttons=[("Manage licenses", f"{public_url}/licenses")],
        reason=_ALERTS,
        sender=_host(public_url),
    )


def license_changes(changes: list[tuple[str, str]], public_url: str) -> Email:
    """``changes``: (what changed, the model's page) for each model."""
    n = len(changes)
    return Email(
        subject=f"OpenPrintHQ: {n} model license change{'' if n == 1 else 's'}",
        heading="Model licenses changed",
        paragraphs=[
            "These designs changed license since you downloaded them. Your saved license record shows the terms "
            "at download time; review whether you want to keep selling."
        ],
        items=changes,
        reason=_ALERTS,
        sender=_host(public_url),
    )
