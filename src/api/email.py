"""Sending emails (step B): the verification link and the password-reset link.

Emails go through Resend's HTTP API (https://resend.com/docs/api-reference/emails/send-email) with httpx, so there is
no extra dependency. Without RESEND_API_KEY (local runs, tests), the email is written to the log instead (with its
link only when the site is local), so the flow can still be followed by hand. Sending never raises: a failed email must not fail the request
(and a password-reset request must answer the same whether or not the email has an account).
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass

import httpx

from src.config import get_email_settings

log = logging.getLogger("eureka.email")

RESEND_URL = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    link: str
    """The one button in the email."""
    button: str


def _html(email: Email) -> str:
    body = "".join(f"<p style=\"margin:0 0 14px\">{html.escape(line)}</p>" for line in email.text.split("\n\n"))
    return (
        "<div style=\"font-family:Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5;color:#17171a;"
        "max-width:520px;margin:0 auto;padding:24px\">"
        "<p style=\"font-family:Georgia,serif;font-size:22px;font-style:italic;margin:0 0 20px\">Eureka</p>"
        f"{body}"
        f"<p style=\"margin:22px 0\"><a href=\"{html.escape(email.link)}\" style=\"background:#17171a;color:#ffffff;"
        f"padding:12px 20px;border-radius:999px;text-decoration:none;font-weight:600\">{html.escape(email.button)}</a></p>"
        f"<p style=\"color:#77756f;font-size:13px\">Or open this link: {html.escape(email.link)}</p></div>"
    )


async def send(email: Email, client: httpx.AsyncClient | None = None) -> bool:
    """Sends the email; True if Resend accepted it (or it was logged because no key is set)."""
    settings = get_email_settings()
    if not settings.resend_api_key:
        # the link is a one-time secret: it is logged only on a local site (to follow the flow by hand), never online
        local = settings.site_url.startswith(("http://localhost", "http://127.0.0.1"))
        log.info("email (RESEND_API_KEY not set, so not sent) to %s: %s%s", email.to, email.subject,
                 f" | {email.link}" if local else "")
        return True
    payload = {"from": settings.email_from, "to": [email.to], "subject": email.subject,
               "text": f"{email.text}\n\n{email.button}: {email.link}", "html": _html(email)}
    headers = {"Authorization": f"Bearer {settings.resend_api_key}"}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as own:
                response = await own.post(RESEND_URL, json=payload, headers=headers)
        else:
            response = await client.post(RESEND_URL, json=payload, headers=headers)
    except httpx.HTTPError as error:
        log.warning("email to %s failed: %s", email.to, type(error).__name__)
        return False
    if response.status_code >= 300:
        # Resend's error body names the problem (e.g. sending to an address other than the account's own)
        log.warning("email to %s refused by Resend (%s): %s", email.to, response.status_code, response.text[:300])
        return False
    return True


def verification_email(to: str, name: str | None, link: str) -> Email:
    hello = f"Hi {name}," if name else "Hi,"
    return Email(to=to, subject="Verify your email for Eureka", link=link, button="Verify my email",
                 text=f"{hello}\n\nConfirm this is your email to start your free evaluation. The link works once, "
                      "for 24 hours.\n\nIf you didn't sign up for Eureka, you can ignore this email.")


def reset_email(to: str, name: str | None, link: str) -> Email:
    hello = f"Hi {name}," if name else "Hi,"
    return Email(to=to, subject="Set a new password for Eureka", link=link, button="Set a new password",
                 text=f"{hello}\n\nSomeone (hopefully you) asked to reset your Eureka password. The link works once, "
                      "for an hour, and logs you out on your other devices.\n\nIf it wasn't you, ignore this email: "
                      "your password stays as it is.")
