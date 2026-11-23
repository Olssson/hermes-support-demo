"""Outbound transports and Sent-folder evidence.

``SmtpTransport`` drives smtplib step by step so a failure can be classified precisely:

* definite (``DefiniteSendFailure``) – nothing can have been accepted: connect/EHLO/TLS/AUTH/MAIL/RCPT
  errors, the server refusing the DATA command, or an explicit non-250 reply after the body;
* ambiguous (``AmbiguousSendFailure``) – the body was transmitted but no final reply arrived
  (timeout, reset, disconnect). The server may or may not have queued it → reconciliation.

``ShadowTransport`` has no network capability at all; shadow mode never constructs an SMTP client.
"""

from __future__ import annotations

import imaplib
import smtplib
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from nexo_support_mcp.adapters.email_adapter import ImapSmtpEmailAdapter, build_mime
from nexo_support_mcp.config import Settings
from nexo_support_mcp.models import OutgoingEmail


@dataclass
class SendOutcome:
    message_id: str
    accepted: list[str]
    refused: dict[str, Any] = field(default_factory=dict)
    saved_to_sent: bool | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class DefiniteSendFailure(Exception):
    def __init__(self, message: str, *, phase: str, retryable: bool):
        super().__init__(message)
        self.phase = phase
        self.retryable = retryable


class AmbiguousSendFailure(Exception):
    def __init__(self, message: str, *, phase: str):
        super().__init__(message)
        self.phase = phase


class Transport(Protocol):
    live: bool

    def send(self, message: OutgoingEmail) -> SendOutcome: ...


class SentLookup(Protocol):
    def find(self, message_id: str) -> dict[str, Any] | None: ...


class ShadowTransport:
    """Records what would be sent. No sockets, no SMTP client."""

    live = False

    def __init__(self) -> None:
        self.recorded: list[OutgoingEmail] = []

    def send(self, message: OutgoingEmail) -> SendOutcome:
        self.recorded.append(message)
        return SendOutcome(message_id=message.message_id, accepted=[], detail={"shadow": True})


class NullSentLookup:
    def find(self, message_id: str) -> dict[str, Any] | None:
        return None


def _retryable(code: int) -> bool:
    return 400 <= code < 500


class SmtpTransport:
    live = True

    def __init__(self, *, host: str, port: int, security: str = "ssl", username: str | None = None,
                 password: str | None = None, timeout: float = 20.0, sent_folder: ImapSentFolder | None = None):
        self.host, self.port, self.security = host, port, security
        self.username, self.password, self.timeout = username, password, timeout
        self.sent_folder = sent_folder

    @classmethod
    def from_settings(cls, s: Settings, *, sent_folder: ImapSentFolder | None = None) -> SmtpTransport:
        return cls(host=s.smtp_host or "", port=s.smtp_port, security=s.smtp_security, username=s.smtp_username,
                   password=s.smtp_password.get_secret_value() if s.smtp_password else None,
                   timeout=s.http_timeout, sent_folder=sent_folder)

    def _connect(self) -> smtplib.SMTP:
        context = ssl.create_default_context()
        if self.security == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        smtp.ehlo_or_helo_if_needed()
        if self.security == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        return smtp

    @staticmethod
    def wire_bytes(message: OutgoingEmail) -> bytes:
        """RFC 5322 bytes as transmitted: 7-bit safe (quoted-printable UTF-8 body), CRLF line ends.
        The vendored ``build_mime`` emits an 8bit body without BODY=8BITMIME negotiation."""
        mime = build_mime(message)
        mime.set_content(message.body, charset="utf-8", cte="quoted-printable")
        return mime.as_bytes(policy=mime.policy.clone(linesep="\r\n"))

    def send(self, message: OutgoingEmail) -> SendOutcome:  # noqa: C901 — explicit phases on purpose
        raw = self.wire_bytes(message)
        phase = "connect"
        smtp: smtplib.SMTP | None = None
        try:
            try:
                smtp = self._connect()
                if self.username and self.password:
                    phase = "auth"
                    smtp.login(self.username, self.password)
                phase = "mail"
                code, reply = smtp.mail(message.from_email)
                if code != 250:
                    raise DefiniteSendFailure(f"MAIL FROM refused: {code} {reply!r}", phase=phase, retryable=_retryable(code))
                phase = "rcpt"
                code, reply = smtp.rcpt(message.to_email)
                if code not in (250, 251):
                    raise DefiniteSendFailure(f"RCPT TO refused: {code} {reply!r}", phase=phase, retryable=_retryable(code))
                phase = "data"
                smtp.putcmd("data")
                code, reply = smtp.getreply()
                if code != 354:
                    raise DefiniteSendFailure(f"DATA refused: {code} {reply!r}", phase=phase, retryable=_retryable(code))
            except DefiniteSendFailure:
                raise
            except (smtplib.SMTPException, OSError) as exc:
                # Before the body was transmitted nothing can have been accepted.
                retryable = not isinstance(exc, smtplib.SMTPAuthenticationError)
                raise DefiniteSendFailure(f"{phase}: {type(exc).__name__}: {exc}", phase=phase, retryable=retryable) from exc

            payload = smtplib._quote_periods(raw)  # same framing smtplib.SMTP.data uses
            if payload[-2:] != b"\r\n":
                payload += b"\r\n"
            payload += b".\r\n"
            try:
                smtp.send(payload)
                code, reply = smtp.getreply()
            except (smtplib.SMTPException, OSError) as exc:
                raise AmbiguousSendFailure(f"no final reply after DATA body: {type(exc).__name__}: {exc}",
                                           phase="data") from exc
            if code != 250:
                raise DefiniteSendFailure(f"message rejected after DATA: {code} {reply!r}", phase="data",
                                          retryable=_retryable(code))
            final_reply = reply.decode(errors="replace") if isinstance(reply, bytes) else str(reply)
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except Exception:  # noqa: BLE001 — after a 250 the message is queued; QUIT errors are irrelevant
                    try:
                        smtp.close()
                    except Exception:  # noqa: BLE001
                        pass

        saved: bool | None = None
        detail: dict[str, Any] = {"final_reply": final_reply, "phase": "accepted"}
        if self.sent_folder is not None:
            try:
                self.sent_folder.append(raw)
                saved = True
            except Exception as exc:  # noqa: BLE001 — best effort, recorded as evidence gap
                saved = False
                detail["save_to_sent_error"] = f"{type(exc).__name__}: {exc}"
        return SendOutcome(message_id=message.message_id, accepted=[message.to_email], saved_to_sent=saved, detail=detail)


class ImapSentFolder:
    """IMAP Sent folder: best-effort append after SMTP acceptance and Message-ID lookup for reconciliation."""

    def __init__(self, settings: Settings, folder: str | None = None):
        self.settings = settings
        self.folder = folder or settings.imap_sent_folder or "Sent"
        self._adapter = ImapSmtpEmailAdapter(settings)

    def _quoted(self) -> str:
        return f'"{self.folder}"' if " " in self.folder and not self.folder.startswith('"') else self.folder

    def append(self, raw: bytes) -> None:
        with self._adapter._imap() as imap:
            typ, data = imap.append(self._quoted(), "\\Seen", imaplib.Time2Internaldate(time.time()), raw)
            if typ != "OK":
                raise RuntimeError(f"IMAP APPEND {self.folder}: {typ} {data!r}")

    def find(self, message_id: str) -> dict[str, Any] | None:
        with self._adapter._imap() as imap:
            typ, _ = imap.select(self._quoted(), readonly=True)
            if typ != "OK":
                raise RuntimeError(f"IMAP SELECT {self.folder}: {typ}")
            typ, data = imap.uid("SEARCH", None, "HEADER", "Message-ID", f'"{message_id}"')
            if typ != "OK":
                raise RuntimeError(f"IMAP SEARCH {self.folder}: {typ}")
            uids = (data[0] or b"").split() if data else []
            if not uids:
                return None
            uid = uids[-1].decode() if isinstance(uids[-1], bytes) else str(uids[-1])
            return {"folder": self.folder, "uid": uid, "message_id": message_id}
