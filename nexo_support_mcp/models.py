"""Enumy domenowe i modele wyników adapterów."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .errors import InvalidInputError


class EmailType(StrEnum):
    CANCELLATION = "CANCELLATION"
    CANCELLATION_CLARIFICATION = "CANCELLATION_CLARIFICATION"
    DELAY = "DELAY"
    TRACKING = "TRACKING"
    CLAIM = "CLAIM"
    RETURN = "RETURN"
    REPLACEMENT = "REPLACEMENT"
    INVOICE_CORRECTION = "INVOICE_CORRECTION"
    STOCK_ISSUE = "STOCK_ISSUE"
    OTHER = "OTHER"


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    WAITING_FOR_MERCHANT = "WAITING_FOR_MERCHANT"
    WAITING_FOR_CUSTOMER = "WAITING_FOR_CUSTOMER"
    WAITING_FOR_RETURN = "WAITING_FOR_RETURN"
    WAITING_FOR_WAREHOUSE = "WAITING_FOR_WAREHOUSE"
    WAITING_FOR_SUPPLIER = "WAITING_FOR_SUPPLIER"
    WAITING_FOR_CORRECTION = "WAITING_FOR_CORRECTION"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


CLOSED_CASE_STATUSES = (CaseStatus.RESOLVED.value, CaseStatus.CLOSED.value)


class WaitingFor(StrEnum):
    MERCHANT = "MERCHANT"
    CUSTOMER = "CUSTOMER"
    WAREHOUSE = "WAREHOUSE"
    SUPPLIER = "SUPPLIER"
    RETURN = "RETURN"
    INVOICE = "INVOICE"
    INTERNAL = "INTERNAL"


class DraftStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    # Stan przejściowy na czas rozmowy z SMTP — chroni przed podwójną wysyłką.
    SENDING = "SENDING"
    SENT = "SENT"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ExternalSystem(StrEnum):
    EMAIL = "EMAIL"
    BASELINKER = "BASELINKER"
    STOCK = "STOCK"
    WAREHOUSE = "WAREHOUSE"
    SMTP = "SMTP"
    INVOICE_CORRECTION = "INVOICE_CORRECTION"


class ClaimType(StrEnum):
    DAMAGED_IN_TRANSPORT = "DAMAGED_IN_TRANSPORT"
    DEFECTIVE = "DEFECTIVE"
    MISSING_PARTS = "MISSING_PARTS"
    WRONG_ITEM = "WRONG_ITEM"
    NOT_AS_DESCRIBED = "NOT_AS_DESCRIBED"
    OTHER = "OTHER"


class ClaimStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


ACTIVE_CLAIM_STATUSES = (ClaimStatus.OPEN.value, ClaimStatus.IN_PROGRESS.value)


class ClaimResolution(StrEnum):
    REPLACEMENT = "REPLACEMENT"
    SPARE_PART = "SPARE_PART"
    REPAIR = "REPAIR"
    # Dopuszczalne dopiero po wyczerpaniu powyższych (wymaga uzasadnienia).
    RETURN_REFUND = "RETURN_REFUND"
    REJECTED = "REJECTED"


class ReturnStatus(StrEnum):
    REGISTERED = "REGISTERED"
    IN_TRANSIT = "IN_TRANSIT"
    RECEIVED = "RECEIVED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


class CorrectionType(StrEnum):
    RETURN = "RETURN"
    CLAIM = "CLAIM"
    CANCELLATION = "CANCELLATION"
    PRICE = "PRICE"
    OTHER = "OTHER"


class CorrectionStatus(StrEnum):
    # Rezerwacja przed wywołaniem API — blokuje równoległy duplikat.
    REQUESTED = "REQUESTED"
    CREATED = "CREATED"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


BLOCKING_CORRECTION_STATUSES = (CorrectionStatus.REQUESTED.value, CorrectionStatus.CREATED.value)


class SyncStatus(StrEnum):
    """Status lokalnego rekordu, który ma odpowiednik w systemie zewnętrznym."""

    PENDING = "PENDING"
    CREATED = "CREATED"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    LOCAL_ONLY = "LOCAL_ONLY"


class EventType(StrEnum):
    CASE_CREATED = "CASE_CREATED"
    CASE_UPDATED = "CASE_UPDATED"
    CASE_STATUS_UPDATED = "CASE_STATUS_UPDATED"
    EMAIL_IMPORTED = "EMAIL_IMPORTED"
    EMAIL_SEND_STARTED = "EMAIL_SEND_STARTED"
    EMAIL_SENT = "EMAIL_SENT"
    EMAIL_SEND_FAILED = "EMAIL_SEND_FAILED"
    DRAFT_CREATED = "DRAFT_CREATED"
    DRAFT_APPROVED = "DRAFT_APPROVED"
    DRAFT_REJECTED = "DRAFT_REJECTED"
    DRAFT_CANCELLED = "DRAFT_CANCELLED"
    BASELINKER_ORDER_CHECKED = "BASELINKER_ORDER_CHECKED"
    BASELINKER_PICKUP_CHECKED = "BASELINKER_PICKUP_CHECKED"
    STOCK_CHECKED = "STOCK_CHECKED"
    WAREHOUSE_PRODUCT_CHECKED = "WAREHOUSE_PRODUCT_CHECKED"
    WAREHOUSE_DEMAND_REQUESTED = "WAREHOUSE_DEMAND_REQUESTED"
    WAREHOUSE_DEMAND_CREATED = "WAREHOUSE_DEMAND_CREATED"
    WAREHOUSE_DEMAND_FAILED = "WAREHOUSE_DEMAND_FAILED"
    CLAIM_CREATED = "CLAIM_CREATED"
    CLAIM_UPDATED = "CLAIM_UPDATED"
    CLAIM_SYNCED = "CLAIM_SYNCED"
    REPLACEMENT_REQUESTED = "REPLACEMENT_REQUESTED"
    REPLACEMENT_CREATED = "REPLACEMENT_CREATED"
    REPLACEMENT_FAILED = "REPLACEMENT_FAILED"
    RETURN_REGISTERED = "RETURN_REGISTERED"
    RETURN_UPDATED = "RETURN_UPDATED"
    RETURN_SYNCED = "RETURN_SYNCED"
    INVOICE_CORRECTION_REQUESTED = "INVOICE_CORRECTION_REQUESTED"
    INVOICE_CORRECTION_CREATED = "INVOICE_CORRECTION_CREATED"
    INVOICE_CORRECTION_FAILED = "INVOICE_CORRECTION_FAILED"
    INVOICE_CORRECTION_NOT_SENT = "INVOICE_CORRECTION_NOT_SENT"
    INVOICE_CORRECTION_HANDOFF_QUEUED = "INVOICE_CORRECTION_HANDOFF_QUEUED"
    INVOICE_CORRECTION_HANDOFF_CONFIRMED = "INVOICE_CORRECTION_HANDOFF_CONFIRMED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"


# Typów systemowych nie wolno dodawać ręcznie przez event_add — fałszowałoby to ledger.
RESERVED_EVENT_TYPES = frozenset(e.value for e in EventType)


def parse_enum(enum_cls: type[StrEnum], value: Any, field: str) -> str:
    """Waliduje wartość enuma (case-insensitive) i zwraca jej string."""
    if isinstance(value, enum_cls):
        return value.value
    normalized = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    try:
        return enum_cls(normalized).value
    except ValueError:
        allowed = ", ".join(e.value for e in enum_cls)
        raise InvalidInputError(f"Nieprawidłowe {field}: {value!r}. Dozwolone: {allowed}") from None


# ---------------------------------------------------------------------------
# Modele wyników adapterów
# ---------------------------------------------------------------------------


class FetchedEmail(BaseModel):
    mailbox: str = "INBOX"
    uidvalidity: str | None = None
    uid: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)
    from_email: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    to_email: str | None = None
    cc: str | None = None
    subject: str = ""
    date: datetime | None = None
    text_body: str | None = None
    html_body: str | None = None
    attachments: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class FetchResult(BaseModel):
    mailbox: str
    uidvalidity: str | None = None
    messages: list[FetchedEmail] = Field(default_factory=list)
    has_more: bool = False
    total_candidates: int = 0


class OutgoingEmail(BaseModel):
    to_email: str
    subject: str
    body: str
    message_id: str
    from_email: str
    from_name: str | None = None
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)


class SmtpResult(BaseModel):
    message_id: str
    accepted: list[str] = Field(default_factory=list)
    refused: dict[str, Any] = Field(default_factory=dict)
    saved_to_sent: bool | None = None
    save_to_sent_error: str | None = None


class BLProduct(BaseModel):
    sku: str | None = None
    name: str | None = None
    quantity: float | int = 1
    ean: str | None = None
    price_brutto: float | None = None


class BLOrder(BaseModel):
    order_id: str
    status_id: int | None = None
    status_name: str | None = None
    products: list[BLProduct] = Field(default_factory=list)
    tracking: str | None = None
    courier: str | None = None
    email: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class BLPackage(BaseModel):
    package_id: str | None = None
    courier_code: str | None = None
    tracking_number: str | None = None
    tracking_status_code: int | None = None
    tracking_status: str | None = None
    tracking_status_date: str | None = None
    tracking_url: str | None = None
    picked_up: bool | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class BLPickup(BaseModel):
    order_id: str
    packages: list[BLPackage] = Field(default_factory=list)
    tracking_numbers: list[str] = Field(default_factory=list)
    picked_up: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class StockResult(BaseModel):
    sku: str
    quantity: int | None = None
    notes: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class WarehouseProduct(BaseModel):
    sku: str
    stock: int | None = None
    name: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ExternalRef(BaseModel):
    """Identyfikator rekordu utworzonego w zewnętrznym systemie."""

    external_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class CorrectionInfo(BaseModel):
    correction_id: str | None = None
    correction_number: str | None = None
    invoice_id: str | None = None
    correction_type: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
