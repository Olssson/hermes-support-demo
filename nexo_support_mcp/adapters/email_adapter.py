"""EmailAdapter: IMAP (pobieranie) + SMTP (wysyłka). Konfiguracja wyłącznie z .env."""

from __future__ import annotations

import imaplib
import logging
import smtplib
import ssl
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr, formatdate, getaddresses, parsedate_to_datetime

from ..config import Settings
from ..errors import ExternalServiceError, NotConfiguredError
from ..models import FetchedEmail, FetchResult, OutgoingEmail, SmtpResult

log = logging.getLogger(__name__)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ---------------------------------------------------------------------------
# Parsowanie MIME
# ---------------------------------------------------------------------------


def _header(msg: EmailMessage, name: str) -> str | None:
    try:
        value = msg.get(name)
    except Exception:  # noqa: BLE001 — uszkodzony nagłówek nie może zatrzymać importu
        value = msg.get_all(name, [None])[0] if hasattr(msg, "get_all") else None
    return str(value).strip() if value is not None else None


def _first_address(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    pairs = getaddresses([value])
    if not pairs:
        return None, None
    name, address = pairs[0]
    return (name or None), (address.lower() or None)


def _part_text(part: EmailMessage) -> str | None:
    try:
        content = part.get_content()
        return content if isinstance(content, str) else None
    except Exception:  # noqa: BLE001
        payload = part.get_payload(decode=True)
        if not payload:
            return None
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")


def _msgids(value: str | None) -> list[str]:
    if not value:
        return []
    return [token for token in value.replace("\n", " ").split() if token.startswith("<") and token.endswith(">")]


def parse_raw_email(
    raw: bytes,
    *,
    mailbox: str = "INBOX",
    uidvalidity: str | None = None,
    uid: str | None = None,
    internaldate: datetime | None = None,
    flags: tuple[str, ...] | list[str] = (),
) -> FetchedEmail:
    msg: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)  # type: ignore[assignment]
    text_body: str | None = None
    html_body: str | None = None
    attachments: list[str] = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_disposition() == "attachment":
            attachments.append(part.get_filename() or "attachment")
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and text_body is None:
            text_body = _part_text(part)
        elif ctype == "text/html" and html_body is None:
            html_body = _part_text(part)

    from_name, from_email = _first_address(_header(msg, "From"))
    _, reply_to = _first_address(_header(msg, "Reply-To"))
    _, to_email = _first_address(_header(msg, "To"))

    sent_at: datetime | None = None
    date_header = _header(msg, "Date")
    if date_header:
        try:
            sent_at = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            sent_at = None
    if sent_at is not None and sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)

    return FetchedEmail(
        mailbox=mailbox,
        uidvalidity=uidvalidity,
        uid=uid,
        message_id=(_header(msg, "Message-ID") or None),
        in_reply_to=(_msgids(_header(msg, "In-Reply-To")) or [None])[0],
        references=_msgids(_header(msg, "References")),
        from_email=from_email,
        from_name=from_name,
        reply_to=reply_to,
        to_email=to_email,
        cc=_header(msg, "Cc"),
        subject=_header(msg, "Subject") or "",
        date=sent_at or internaldate,
        text_body=text_body,
        html_body=html_body,
        attachments=attachments,
        flags=list(flags),
    )


def build_mime(message: OutgoingEmail) -> EmailMessage:
    em = EmailMessage()
    em["From"] = formataddr((message.from_name or "", message.from_email))
    em["To"] = message.to_email
    em["Subject"] = message.subject
    em["Date"] = formatdate(usegmt=True)
    em["Message-ID"] = message.message_id
    if message.in_reply_to:
        em["In-Reply-To"] = message.in_reply_to
        refs = [r for r in message.references if r != message.in_reply_to] + [message.in_reply_to]
        em["References"] = " ".join(refs)
    em.set_content(message.body, charset="utf-8")
    return em


# ---------------------------------------------------------------------------
# Interfejs
# ---------------------------------------------------------------------------


class EmailAdapter(ABC):
    @property
    @abstractmethod
    def can_fetch(self) -> bool: ...

    @property
    @abstractmethod
    def can_send(self) -> bool: ...

    @abstractmethod
    def fetch_new(
        self, *, since_uid: int | None, uidvalidity: str | None, limit: int, lookback_days: int
    ) -> FetchResult:
        """Nowe maile (UID > since_uid) albo — przy pierwszym uruchomieniu — z ostatnich N dni."""

    @abstractmethod
    def mark_seen(self, uids: list[str]) -> None: ...

    @abstractmethod
    def send(self, message: OutgoingEmail) -> SmtpResult: ...

    def search(self, query: str, limit: int) -> FetchResult:
        raise NotConfiguredError("Ten adapter nie obsługuje wyszukiwania w skrzynce", code="EMAIL_SEARCH_UNSUPPORTED")


# ---------------------------------------------------------------------------
# IMAP + SMTP
# ---------------------------------------------------------------------------


class ImapSmtpEmailAdapter(EmailAdapter):
    def __init__(self, settings: Settings):
        self.s = settings

    @property
    def can_fetch(self) -> bool:
        return self.s.imap_configured

    @property
    def can_send(self) -> bool:
        return self.s.smtp_configured

    def _require_imap(self) -> None:
        if not self.can_fetch:
            missing = self.s.missing_config().get("imap", [])
            raise NotConfiguredError(
                "IMAP nie jest skonfigurowany", code="EMAIL_NOT_CONFIGURED", details={"missing": missing}
            )

    @contextmanager
    def _imap(self) -> Iterator[imaplib.IMAP4]:
        self._require_imap()
        timeout = self.s.http_timeout
        context = ssl.create_default_context()
        try:
            if self.s.imap_security == "ssl":
                imap: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                    self.s.imap_host, self.s.imap_port, ssl_context=context, timeout=timeout
                )
            else:
                imap = imaplib.IMAP4(self.s.imap_host, self.s.imap_port, timeout=timeout)
                if self.s.imap_security == "starttls":
                    imap.starttls(ssl_context=context)
            imap.login(self.s.imap_username, self.s.imap_password.get_secret_value())  # type: ignore[union-attr]
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ExternalServiceError(f"IMAP: nie udało się zalogować: {exc}", code="IMAP_LOGIN_FAILED") from exc
        try:
            yield imap
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass

    def _mailbox(self) -> str:
        mailbox = self.s.imap_mailbox
        return f'"{mailbox}"' if " " in mailbox and not mailbox.startswith('"') else mailbox

    def _select(self, imap: imaplib.IMAP4, readonly: bool) -> str | None:
        typ, _ = imap.select(self._mailbox(), readonly=readonly)
        if typ != "OK":
            raise ExternalServiceError(f"IMAP: nie można otworzyć {self.s.imap_mailbox}", code="IMAP_SELECT_FAILED")
        _, data = imap.response("UIDVALIDITY")
        if data and data[0]:
            return data[0].decode() if isinstance(data[0], bytes) else str(data[0])
        return None

    def _fetch_uids(self, imap: imaplib.IMAP4, uids: list[int], uidvalidity: str | None) -> list[FetchedEmail]:
        messages = []
        for uid in uids:
            typ, resp = imap.uid("FETCH", str(uid), "(BODY.PEEK[] INTERNALDATE FLAGS)")
            if typ != "OK" or not resp:
                log.warning("IMAP: FETCH uid=%s zwrócił %s", uid, typ)
                continue
            raw, meta = None, b""
            for item in resp:
                if isinstance(item, tuple):
                    meta, raw = item[0], item[1]
                elif isinstance(item, bytes):
                    meta += item
            if raw is None:
                continue
            internaldate = None
            parsed = imaplib.Internaldate2tuple(meta)
            if parsed:
                internaldate = datetime.fromtimestamp(time.mktime(parsed), tz=UTC)
            flags = tuple(f.decode() for f in imaplib.ParseFlags(meta))
            messages.append(
                parse_raw_email(
                    raw,
                    mailbox=self.s.imap_mailbox,
                    uidvalidity=uidvalidity,
                    uid=str(uid),
                    internaldate=internaldate,
                    flags=flags,
                )
            )
        return messages

    def fetch_new(
        self, *, since_uid: int | None, uidvalidity: str | None, limit: int, lookback_days: int
    ) -> FetchResult:
        with self._imap() as imap:
            server_uidvalidity = self._select(imap, readonly=True)
            incremental = since_uid is not None and uidvalidity == server_uidvalidity
            if incremental:
                typ, data = imap.uid("SEARCH", None, f"UID {since_uid + 1}:*")
            else:
                since = date.today() - timedelta(days=lookback_days)
                typ, data = imap.uid("SEARCH", None, f"SINCE {since.day:02d}-{_MONTHS[since.month - 1]}-{since.year}")
            if typ != "OK":
                raise ExternalServiceError("IMAP: SEARCH nie powiódł się", code="IMAP_SEARCH_FAILED")
            uids = sorted({int(x) for x in (data[0] or b"").split()})
            if incremental:
                # "UID n:*" zawsze zwraca co najmniej ostatnią wiadomość — odfiltrowujemy.
                uids = [u for u in uids if u > since_uid]
            batch = uids[:limit]
            return FetchResult(
                mailbox=self.s.imap_mailbox,
                uidvalidity=server_uidvalidity,
                messages=self._fetch_uids(imap, batch, server_uidvalidity),
                has_more=len(uids) > limit,
                total_candidates=len(uids),
            )

    def mark_seen(self, uids: list[str]) -> None:
        if not uids:
            return
        with self._imap() as imap:
            self._select(imap, readonly=False)
            typ, _ = imap.uid("STORE", ",".join(uids), "+FLAGS", "(\\Seen)")
            if typ != "OK":
                raise ExternalServiceError("IMAP: nie udało się oznaczyć jako przeczytane", code="IMAP_STORE_FAILED")

    def search(self, query: str, limit: int) -> FetchResult:
        with self._imap() as imap:
            uidvalidity = self._select(imap, readonly=True)
            imap.literal = query.encode("utf-8")
            typ, data = imap.uid("SEARCH", "CHARSET", "UTF-8", "TEXT")
            if typ != "OK":
                raise ExternalServiceError("IMAP: SEARCH nie powiódł się", code="IMAP_SEARCH_FAILED")
            uids = sorted({int(x) for x in (data[0] or b"").split()})
            batch = uids[-limit:]
            return FetchResult(
                mailbox=self.s.imap_mailbox,
                uidvalidity=uidvalidity,
                messages=self._fetch_uids(imap, batch, uidvalidity),
                has_more=len(uids) > limit,
                total_candidates=len(uids),
            )

    def send(self, message: OutgoingEmail) -> SmtpResult:
        if not self.can_send:
            raise NotConfiguredError(
                "SMTP nie jest skonfigurowany",
                code="EMAIL_NOT_CONFIGURED",
                details={"missing": self.s.missing_config().get("smtp", [])},
            )
        mime = build_mime(message)
        context = ssl.create_default_context()
        timeout = self.s.http_timeout
        try:
            if self.s.smtp_security == "ssl":
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, timeout=timeout, context=context)
            else:
                smtp = smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=timeout)
                if self.s.smtp_security == "starttls":
                    smtp.starttls(context=context)
            try:
                if self.s.smtp_username and self.s.smtp_password:
                    smtp.login(self.s.smtp_username, self.s.smtp_password.get_secret_value())
                refused = smtp.send_message(mime)
            finally:
                try:
                    smtp.quit()
                except Exception:  # noqa: BLE001 — wiadomość już przyjęta, błąd QUIT nieistotny
                    pass
        except (smtplib.SMTPException, OSError) as exc:
            raise ExternalServiceError(f"SMTP: wysyłka nie powiodła się: {exc}", code="SMTP_SEND_FAILED") from exc

        result = SmtpResult(
            message_id=message.message_id,
            accepted=[message.to_email] if message.to_email not in refused else [],
            refused={k: list(v) if isinstance(v, tuple) else v for k, v in refused.items()},
        )
        if self.s.imap_sent_folder and self.can_fetch:
            try:
                with self._imap() as imap:
                    folder = self.s.imap_sent_folder
                    folder = f'"{folder}"' if " " in folder else folder
                    imap.append(folder, "\\Seen", imaplib.Time2Internaldate(time.time()), mime.as_bytes())
                result.saved_to_sent = True
            except Exception as exc:  # noqa: BLE001 — wysyłka się udała; zapis do Sent jest best-effort
                result.saved_to_sent = False
                result.save_to_sent_error = str(exc)
        return result


# ---------------------------------------------------------------------------
# Mock (testy / tryb demo)
# ---------------------------------------------------------------------------
