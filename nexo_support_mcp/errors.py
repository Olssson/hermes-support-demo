"""Błędy domenowe. Każdy trafia do agenta jako {"ok": false, "error": {...}}."""

from __future__ import annotations

from typing import Any


class NexoError(Exception):
    code = "ERROR"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"ok": False, "error": error}


class InvalidInputError(NexoError):
    code = "INVALID_INPUT"


class NotFoundError(NexoError):
    code = "NOT_FOUND"


class SafetyError(NexoError):
    """Akcja zablokowana przez reguły bezpieczeństwa."""

    code = "SAFETY_BLOCKED"


class NotConfiguredError(NexoError):
    """Integracja nie ma konfiguracji w .env."""

    code = "NOT_CONFIGURED"


class ExternalServiceError(NexoError):
    """Zewnętrzny system zwrócił błąd albo nie odpowiedział."""

    code = "EXTERNAL_ERROR"


class ConfigError(Exception):
    """Błędna konfiguracja — serwer nie powinien wystartować."""
