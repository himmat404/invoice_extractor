import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger("invoiceflow.email")


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    def send(self, message: EmailMessage) -> None:
        logger.info("Email to %s: %s\n%s", message.to, message.subject, message.text)


class MemoryEmailSender:
    """Collects messages in memory; used by tests."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)


class SmtpEmailSender:
    def send(self, message: EmailMessage) -> None:
        s = get_settings()
        mime = MimeMessage()
        mime["From"] = s.email_from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.text)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            if s.smtp_use_tls:
                smtp.starttls()
            if s.smtp_username and s.smtp_password:
                smtp.login(s.smtp_username, s.smtp_password)
            smtp.send_message(mime)


_memory_sender = MemoryEmailSender()


def get_email_sender() -> EmailSender:
    driver = get_settings().email_driver
    if driver == "smtp":
        return SmtpEmailSender()
    if driver == "memory":
        return _memory_sender
    return ConsoleEmailSender()


def memory_outbox() -> list[EmailMessage]:
    return _memory_sender.outbox


def send_email(message: EmailMessage) -> None:
    try:
        get_email_sender().send(message)
    except Exception:  # noqa: BLE001 - email failure must not break the request
        logger.exception("Failed to send email to %s", message.to)


def send_verification_email(to: str, token: str) -> None:
    s = get_settings()
    link = f"{s.customer_app_url}/verify-email?token={token}"
    send_email(
        EmailMessage(
            to=to,
            subject=f"Verify your {s.app_name} email",
            text=(
                f"Welcome to {s.app_name}!\n\nConfirm your email address by opening:\n{link}\n\n"
                f"This link expires in {s.email_verification_ttl_hours} hours."
            ),
        )
    )


def send_password_reset_email(to: str, token: str) -> None:
    s = get_settings()
    link = f"{s.customer_app_url}/reset-password?token={token}"
    send_email(
        EmailMessage(
            to=to,
            subject=f"Reset your {s.app_name} password",
            text=(
                f"We received a request to reset your password.\n\nOpen this link to choose a new "
                f"one:\n{link}\n\nThis link expires in {s.password_reset_ttl_minutes} minutes. "
                "If you didn't request this, you can ignore this email."
            ),
        )
    )
