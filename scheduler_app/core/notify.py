"""Email: reminders and itinerary notices.

If SMTP is configured (see .env.example) messages can be sent from the app.
Otherwise the app only *prepares* messages (copy text / mailto link) and nothing
is sent. Nothing is ever sent without the coordinator clicking a send button.
"""
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote

from .config import Settings
from .timeutil import fmt_date


@dataclass
class Draft:
    to: str
    subject: str
    body: str

    @property
    def mailto(self) -> str:
        return f"mailto:{quote(self.to)}?subject={quote(self.subject)}&body={quote(self.body)}"


def invite_link(settings: Settings, token: str) -> str:
    return f"{settings.app_base_url}/?invite={token}"


def itinerary_link(settings: Settings, token: str) -> str:
    return f"{settings.app_base_url}/?itinerary={token}"


def availability_request(settings: Settings, search, participant, reminder: bool = False) -> Draft:
    dates = sorted({d.date for c in search.candidates for d in c.visit_days})
    date_text = "\n".join(f"  • {fmt_date(d)}" for d in dates)
    deadline = (f"Please respond by {fmt_date(search.availability_deadline)} at "
                f"{search.availability_deadline:%-I:%M %p}.") if search.availability_deadline else ""
    subj = ("Reminder: " if reminder else "") + f"Your availability for candidate visits — {search.department}"
    body = (
        f"Dear {participant.name},\n\n"
        f"We are scheduling individual meetings with our faculty candidates. The visit dates are:\n{date_text}\n\n"
        f"Please mark when you are free using your personal link (no password needed; please don't share it):\n"
        f"{invite_link(settings, participant.access_token)}\n\n"
        f"It takes about two minutes. {deadline}\n\n"
        f"Thank you,\n{search.coordinator_name or 'Search coordinator'}\n{search.department}"
    )
    return Draft(participant.email, subj, body)


def itinerary_notice(settings: Settings, search, candidate) -> Draft:
    subj = f"Your campus visit itinerary — {search.department}"
    body = (
        f"Dear {candidate.name},\n\n"
        f"Your itinerary for your campus visit is ready. You can view, print or download it here:\n"
        f"{itinerary_link(settings, candidate.share_token)}\n\n"
        f"If anything changes we will update the same link.\n\n"
        f"Best regards,\n{search.coordinator_name or 'Search coordinator'}\n{search.department}"
    )
    return Draft(candidate.email or "", subj, body)


def send(settings: Settings, draft: Draft, attachments: list[tuple[str, bytes, str]] | None = None) -> None:
    if not settings.email_enabled:
        raise RuntimeError("Email is not configured (set SMTP_HOST and SMTP_FROM).")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, draft.to, draft.subject
    msg.set_content(draft.body)
    for fname, data, mime in attachments or []:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fname)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(msg)
