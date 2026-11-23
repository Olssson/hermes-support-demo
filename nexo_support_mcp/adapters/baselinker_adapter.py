"""BaselinkerAdapter — Baselinker API (connector.php).

Każde wywołanie: POST {BASELINKER_API_URL}, nagłówek X-BLToken,
form-data: method=<nazwa>, parameters=<json>.
Dokumentacja: https://api.baselinker.com/
"""

from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..config import Settings
from ..errors import ExternalServiceError, NotConfiguredError, NotFoundError
from ..models import BLOrder, BLPackage, BLPickup, BLProduct
from .base import to_int

# Kody tracking_status z getOrderPackages / getCourierPackagesStatusHistory.
# TODO: potwierdzić mapowanie z aktualną dokumentacją Baselinkera dla używanych kurierów.
TRACKING_STATUSES: dict[int, str] = {
    0: "Nieznany",
    1: "Etykieta utworzona",
    2: "Nadana",
    3: "Niedoręczona",
    4: "W doręczeniu",
    5: "Doręczona",
    6: "Zwrot",
    7: "Awizo",
    8: "Czeka w punkcie",
    9: "Zagubiona",
    10: "Anulowana",
    11: "W drodze",
}
# Statusy oznaczające, że kurier fizycznie odebrał paczkę.
PICKED_UP_STATUSES = {2, 3, 4, 5, 6, 7, 8, 11}

STATUS_CACHE_TTL_SECONDS = 15 * 60


class BaselinkerAdapter(ABC):
    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def get_order(self, order_id: str) -> BLOrder: ...

    @abstractmethod
    def list_orders(self, *, date_from: int, status_ids: list[int] | None = None, limit: int = 100) -> list[BLOrder]: ...

    @abstractmethod
    def set_order_status(self, order_id: str, status_id: int) -> BLOrder: ...

    @abstractmethod
    def get_pickup(self, order_id: str) -> BLPickup: ...

    @abstractmethod
    def get_invoices(self, order_id: str) -> list[dict[str, Any]]: ...


def _order_from_raw(order: dict[str, Any], status_name: str | None) -> BLOrder:
    products = [
        BLProduct(
            sku=(p.get("sku") or None),
            name=p.get("name"),
            quantity=p.get("quantity") or 1,
            ean=(p.get("ean") or None),
            price_brutto=p.get("price_brutto"),
        )
        for p in order.get("products") or []
    ]
    return BLOrder(
        order_id=str(order.get("order_id")),
        status_id=to_int(order.get("order_status_id")),
        status_name=status_name,
        products=products,
        tracking=(order.get("delivery_package_nr") or None),
        courier=(order.get("delivery_package_module") or None),
        email=(order.get("email") or None),
        raw=order,
    )


def _pickup_from_raw(order_id: str, packages: list[dict[str, Any]], history: dict[str, Any]) -> BLPickup:
    result = []
    for p in packages:
        code = to_int(p.get("tracking_status"))
        package_id = str(p.get("package_id")) if p.get("package_id") is not None else None
        result.append(
            BLPackage(
                package_id=package_id,
                courier_code=p.get("courier_code"),
                tracking_number=(p.get("courier_package_nr") or None),
                tracking_status_code=code,
                tracking_status=TRACKING_STATUSES.get(code) if code is not None else None,
                tracking_status_date=str(p.get("tracking_status_date")) if p.get("tracking_status_date") else None,
                tracking_url=(p.get("tracking_url") or None),
                picked_up=(code in PICKED_UP_STATUSES) if code is not None else None,
                history=list(history.get(package_id, []) if package_id else []),
            )
        )
    picked = [p.picked_up for p in result if p.picked_up is not None]
    return BLPickup(
        order_id=order_id,
        packages=result,
        tracking_numbers=[p.tracking_number for p in result if p.tracking_number],
        picked_up=any(picked) if picked else None,
        raw={"packages": packages, "packages_history": history},
    )


class HttpBaselinkerAdapter(BaselinkerAdapter):
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.s = settings
        self._transport = transport
        self._status_cache: tuple[float, dict[int, str]] | None = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.s.baselinker_configured

    def _call(self, method: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise NotConfiguredError("Baselinker: brak BASELINKER_API_TOKEN", code="BASELINKER_NOT_CONFIGURED")
        headers = {"X-BLToken": self.s.baselinker_api_token.get_secret_value()}  # type: ignore[union-attr]
        data = {"method": method, "parameters": json.dumps(parameters)}
        try:
            with httpx.Client(timeout=self.s.http_timeout, transport=self._transport) as client:
                response = client.post(self.s.baselinker_api_url, headers=headers, data=data)
        except httpx.HTTPError as exc:
            raise ExternalServiceError(f"Baselinker: błąd połączenia: {exc}", code="BASELINKER_UNREACHABLE") from exc
        if response.status_code >= 400:
            raise ExternalServiceError(
                f"Baselinker {method}: HTTP {response.status_code}",
                code="BASELINKER_HTTP_ERROR",
                details={"body": response.text[:2000]},
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExternalServiceError(f"Baselinker {method}: odpowiedź nie jest JSON-em", code="BASELINKER_BAD_RESPONSE") from exc
        if payload.get("status") != "SUCCESS":
            raise ExternalServiceError(
                f"Baselinker {method}: {payload.get('error_code')} {payload.get('error_message')}",
                code="BASELINKER_ERROR",
                details={"error_code": payload.get("error_code"), "error_message": payload.get("error_message")},
            )
        return payload

    def _statuses(self) -> dict[int, str]:
        with self._lock:
            if self._status_cache and time.monotonic() - self._status_cache[0] < STATUS_CACHE_TTL_SECONDS:
                return self._status_cache[1]
        payload = self._call("getOrderStatusList", {})
        statuses = {int(s["id"]): s.get("name") or "" for s in payload.get("statuses") or [] if s.get("id") is not None}
        with self._lock:
            self._status_cache = (time.monotonic(), statuses)
        return statuses

    def get_order(self, order_id: str) -> BLOrder:
        payload = self._call("getOrders", {"order_id": int(order_id), "get_unconfirmed_orders": True})
        orders = payload.get("orders") or []
        if not orders:
            raise NotFoundError(f"Baselinker: brak zamówienia {order_id}", code="ORDER_NOT_FOUND")
        order = orders[0]
        status_id = to_int(order.get("order_status_id"))
        try:
            status_name = self._statuses().get(status_id) if status_id is not None else None
        except ExternalServiceError:
            status_name = None  # nazwa statusu jest tylko dodatkiem
        return _order_from_raw(order, status_name)

    def list_orders(self, *, date_from: int, status_ids: list[int] | None = None, limit: int = 100) -> list[BLOrder]:
        payload = self._call("getOrders", {"date_from": int(date_from), "get_unconfirmed_orders": True})
        raw_orders = payload.get("orders") or []
        allowed = set(status_ids or [])
        try:
            statuses = self._statuses()
        except ExternalServiceError:
            statuses = {}
        orders: list[BLOrder] = []
        for order in sorted(raw_orders, key=lambda o: o.get("date_add") or 0, reverse=True):
            status_id = to_int(order.get("order_status_id"))
            if allowed and status_id not in allowed:
                continue
            orders.append(_order_from_raw(order, statuses.get(status_id) if status_id is not None else None))
            if len(orders) >= limit:
                break
        return orders

    def set_order_status(self, order_id: str, status_id: int) -> BLOrder:
        self._call("setOrderStatus", {"order_id": int(order_id), "status_id": int(status_id)})
        after = self.get_order(order_id)
        if after.status_id != int(status_id):
            raise ExternalServiceError(
                f"Baselinker setOrderStatus: weryfikacja nieudana, oczekiwano {status_id}, jest {after.status_id}",
                code="BASELINKER_STATUS_VERIFY_FAILED",
                details={"order_id": order_id, "expected_status_id": status_id, "actual_status_id": after.status_id},
            )
        return after

    def get_pickup(self, order_id: str) -> BLPickup:
        payload = self._call("getOrderPackages", {"order_id": int(order_id)})
        packages = payload.get("packages") or []
        package_ids = [p["package_id"] for p in packages if p.get("package_id")]
        history: dict[str, Any] = {}
        if package_ids:
            try:
                hist = self._call("getCourierPackagesStatusHistory", {"package_ids": package_ids})
                history = {str(k): v for k, v in (hist.get("packages_history") or {}).items()}
            except ExternalServiceError:
                history = {}
        return _pickup_from_raw(order_id, packages, history)

    def get_invoices(self, order_id: str) -> list[dict[str, Any]]:
        payload = self._call("getInvoices", {"order_id": int(order_id)})
        invoices = payload.get("invoices")
        if not isinstance(invoices, list) or any(not isinstance(i, dict) for i in invoices):
            raise ExternalServiceError("Baselinker getInvoices: nieprawidłowa lista faktur", code="BASELINKER_BAD_RESPONSE")
        return invoices
