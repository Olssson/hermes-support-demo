"""InvoiceCorrectionAdapter — korekty faktur.

Kontrakt HTTP (TODO: dopasuj do realnego systemu fakturowego):

    GET  {INVOICE_API_URL}/corrections?order_id=..&sku=..
         → {"corrections": [{"id", "number", "invoice_id", "type"}]}
    POST {INVOICE_API_URL}/corrections
         {order_id, sku, correction_type, reason}  + nagłówek Idempotency-Key
         → {"id", "number", "invoice_id"}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..config import Settings
from ..errors import NotConfiguredError
from ..models import CorrectionInfo
from .base import HttpJsonClient, pick


class InvoiceCorrectionAdapter(ABC):
    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def check(self, order_id: str, sku: str) -> list[CorrectionInfo]:
        """Korekty, które już istnieją w systemie fakturowym dla zamówienia+SKU."""

    @abstractmethod
    def create(self, *, order_id: str, sku: str, correction_type: str, reason: str, idempotency_key: str) -> CorrectionInfo: ...


def _info(item: dict[str, Any]) -> CorrectionInfo:
    def s(v: Any) -> str | None:
        return str(v) if v is not None else None

    return CorrectionInfo(
        correction_id=s(pick(item, "id", "correction_id")),
        correction_number=s(pick(item, "number", "correction_number")),
        invoice_id=s(pick(item, "invoice_id")),
        correction_type=s(pick(item, "type", "correction_type")),
        raw=item,
    )


class HttpInvoiceCorrectionAdapter(InvoiceCorrectionAdapter):
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.client = HttpJsonClient(
            system="INVOICE",
            base_url=settings.invoice_api_url,
            token=settings.invoice_api_token,
            not_configured_code="INVOICE_API_NOT_CONFIGURED",
            auth_header=settings.invoice_api_auth_header,
            auth_scheme=settings.invoice_api_auth_scheme,
            timeout=settings.http_timeout,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return self.client.configured

    def check(self, order_id: str, sku: str) -> list[CorrectionInfo]:
        data = self.client.request("GET", "corrections", params={"order_id": order_id, "sku": sku})
        if not data:
            return []
        items = data.get("corrections", []) if isinstance(data, dict) else data
        return [_info(i) for i in items if isinstance(i, dict)]

    def create(self, *, order_id, sku, correction_type, reason, idempotency_key) -> CorrectionInfo:
        data = self.client.request(
            "POST",
            "corrections",
            json={"order_id": order_id, "sku": sku, "correction_type": correction_type, "reason": reason},
            headers={"Idempotency-Key": idempotency_key},
        )
        return _info(data if isinstance(data, dict) else {"data": data})
