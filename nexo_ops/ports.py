"""Ports for business capabilities beyond mail: documents (read) and operations (write).

Production wires ``UNAVAILABLE`` for both: there is no reviewed adapter that issues invoices/corrections,
cancels orders or reads a document store, so those capabilities are structurally absent — the context lists
no operations, the policy rejects any requested operation (``OPERATION_UNAVAILABLE``) and attachments cannot
be verified. The owner-testable sandbox (``nexo_ops.sandbox``) wires isolated adapters that write labelled
test records only.

Operation contract (``OperationsPort``):

* ``supported()`` – operation types this adapter can execute;
* ``execute(op)`` – perform the effect for a PREPARED operation row; idempotent per ``op["id"]``; returns a
  receipt dict (``{"ok": bool, ...}``); raises ``OperationRefused`` when the effect cannot happen (state
  changed, not stoppable, …) — nothing was done then;
* ``read_back(op, receipt)`` – re-read the external state; ``{"verified": bool, "observed": {...}}``.
  An operation is reported as done only after a verified read-back.
"""

from __future__ import annotations

from typing import Any, Protocol


class OperationRefused(Exception):
    """The adapter refused the effect (nothing was done)."""

    def __init__(self, message: str, *, code: str = "OPERATION_REFUSED", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class OperationsPort(Protocol):
    available: bool
    adapter: str
    simulated: bool

    def supported(self) -> tuple[str, ...]: ...

    def execute(self, op: dict[str, Any]) -> dict[str, Any]: ...

    def read_back(self, op: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]: ...


class DocumentsPort(Protocol):
    available: bool

    def for_order(self, order_id: str) -> list[dict[str, Any]]: ...

    def manuals(self, sku: str) -> list[dict[str, Any]]: ...

    def get(self, document_id: str) -> dict[str, Any] | None: ...

    def verify_file(self, document: dict[str, Any]) -> bool: ...


class _Unavailable:
    """No adapter: nothing to read, nothing can be executed."""

    available = False
    adapter = "unavailable"
    simulated = False
    label = "unavailable (no reviewed adapter in this deployment)"

    def supported(self) -> tuple[str, ...]:
        return ()

    def execute(self, op: dict[str, Any]) -> dict[str, Any]:
        raise OperationRefused(f"operation {op.get('type')} is not available in this deployment",
                               code="OPERATION_UNAVAILABLE")

    def read_back(self, op: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
        return {"verified": False, "observed": {"reason": "unavailable"}}

    def for_order(self, order_id: str) -> list[dict[str, Any]]:
        return []

    def manuals(self, sku: str) -> list[dict[str, Any]]:
        return []

    def get(self, document_id: str) -> dict[str, Any] | None:
        return None

    def verify_file(self, document: dict[str, Any]) -> bool:
        return False


UNAVAILABLE = _Unavailable()
