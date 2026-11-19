"""The hub's emails: plain text and HTML in the hub's look, built so email programs show them as designed."""
import re
from email import message_from_bytes

import pytest

from app.services import emails

URL = "https://hub.example.org"


def _all():
    return [
        emails.invite("Jane Doe", "Workshop", "member", f"{URL}/invite/abc", f"{URL}/account/password/xyz", 14, URL),
        emails.invite("", "Workshop", None, f"{URL}/invite/abc", None, 14, URL),
        emails.farm_alert("Print failed on Prusa MK4S", "The print stopped at layer 112.", f"{URL}/apps/farm", URL),
        emails.out_of_supplies("Gyroid vase", "GV-01", ["PETG black: need 120 per unit, available 40"], URL),
        emails.license_reminder(["Printables: 'Club' ends 2026-12-01 (20 days). 4 models, 2 live."], URL),
        emails.license_changes([("M-12 Dragon: CC-BY -> CC-BY-NC (live in the store)", f"{URL}/models/12")], URL),
        emails.password_reset(f"{URL}/account/password/xyz", 30, URL),
    ]


@pytest.mark.parametrize("mail", _all(), ids=lambda m: m.subject)
def test_every_email_is_tables_and_inline_styles_with_nothing_to_load(mail):
    html = mail.html()
    assert html.startswith("<!doctype html>")
    # Email programs do not blur or read style sheets: no blur, no <style>, nothing loaded, no scripts.
    assert not re.search(r"blur|backdrop|<style|<link|<script|<svg|@import|url\(|<img", html)
    assert 'role="presentation"' in html and "bgcolor=" in html
    # The card is solid where the screens have glass, with the same border.
    assert f"background-color:{emails.CARD};border:1px solid {emails.LINE}" in html
    assert "Manrope" in html and "Sora" in html
    assert "OpenPrintHQ" in html and "Open PrintHQ" not in html and "Open PrintHQ" not in mail.text()
    assert chr(0x2014) not in html + mail.text() + mail.subject


@pytest.mark.parametrize("mail", _all(), ids=lambda m: m.subject)
def test_the_plain_text_twin_says_the_same(mail):
    text = mail.text()
    assert text.startswith(mail.heading)
    for p in mail.paragraphs:
        assert p in text
    for label, url in mail.buttons:
        assert f"{label}: {url}" in text and f'href="{url}"' in mail.html()
    for item, link in mail.items:
        assert item in text and (link is None or link in text)
    assert "Sent by OpenPrintHQ at hub.example.org." in text


def _rgb(hex_):
    return tuple(int(hex_[i : i + 2], 16) for i in (1, 3, 5))


def _contrast(a, b):
    def lum(c):
        ch = [x / 255 for x in _rgb(c)]
        ch = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("fg,bg", [
    (emails.TEXT, emails.CARD), (emails.SECONDARY, emails.CARD), (emails.LINK, emails.CARD),
    (emails.ON_ACCENT, emails.ACCENT), (emails.MUTED_ON_PAGE, emails.PAGE), (emails.TEXT, emails.PAGE),
])
def test_every_text_colour_keeps_4_5_to_1(fg, bg):
    assert _contrast(fg, bg) >= 4.5


def test_nothing_a_person_typed_becomes_markup():
    mail = emails.invite("<b>Eve</b>", "A & B <script>", "member", f"{URL}/invite/a\"b", None, 14, URL)
    html = mail.html()
    assert "<b>Eve</b>" not in html and "&lt;b&gt;Eve&lt;/b&gt;" in html
    assert "<script>" not in html and "A &amp; B &lt;script&gt;" in html
    assert 'href="https://hub.example.org/invite/a&quot;b"' in html


def test_an_invite_without_a_login_asks_for_a_password_first():
    mail = emails.invite("Jane Doe", "Workshop", "member", f"{URL}/invite/abc", f"{URL}/account/password/xyz",
                         14, URL)
    assert mail.subject == "Jane Doe invited you to Workshop on OpenPrintHQ"
    assert mail.paragraphs[0] == "Jane Doe invited you to Workshop on OpenPrintHQ as a member."
    assert [b[0] for b in mail.buttons] == ["Choose your password", "Accept the invitation"]
    assert mail.html().index("Choose your password") < mail.html().index("Accept the invitation")
    assert "The links work for 14 days" in mail.text()
    owner = emails.invite("", "Workshop", "admin", f"{URL}/invite/abc", None, 14, URL)
    assert owner.paragraphs[0] == "The hub's owner invited you to Workshop on OpenPrintHQ as an admin."
    assert [b[0] for b in owner.buttons] == ["Accept the invitation"]


def test_send_mail_sends_the_text_and_the_html_together(monkeypatch):
    from app import tasks
    from app.config import settings

    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(tasks.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings(), "smtp_host", "smtp.example.org")
    mail = emails.farm_alert("Print done", "Gyroid vase finished.", f"{URL}/apps/farm", URL)
    tasks.send_mail(mail.subject, mail.text(), to=["a@example.org"], html=mail.html())
    (msg,) = sent
    parsed = message_from_bytes(msg.as_bytes())
    assert parsed.get_content_type() == "multipart/alternative"
    parts = [p.get_content_type() for p in parsed.get_payload()]
    assert parts == ["text/plain", "text/html"]
    # Without HTML, plain text alone, as before.
    tasks.send_mail("Plain", "Just text", to=["a@example.org"])
    assert sent[1].get_content_type() == "text/plain"
