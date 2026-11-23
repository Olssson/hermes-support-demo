"""Wspólny klient HTTP/JSON dla adapterów REST."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import SecretStr

from ..errors import ExternalServiceError, NotConfiguredError


class HttpJsonClient:
    def __init__(
        self,
        *,
        system: str,
        base_url: str | None,
        token: SecretStr | None,
        not_configured_code: str,
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.system = system
        self.base_url = (base_url or "").rstrip("/")
        self._token = token
        self.not_configured_code = not_configured_code
        self.auth_header = auth_header
        self.auth_scheme = auth_scheme
        self.timeout = timeout
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def require_configured(self) -> None:
        if not self.configured:
            raise NotConfiguredError(
                f"{self.system}: brak konfiguracji API w .env",
                code=self.not_configured_code,
            )

    def url(self, path: str = "") -> str:
        if not path:
            return self.base_url
        return f"{self.base_url}/{path.lstrip('/')}"

    def request(
        self,
        method: str,
        path: str = "",
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        url: str | None = None,
    ) -> Any:
        self.require_configured()
        all_headers = {"Accept": "application/json"}
        if self._token:
            value = self._token.get_secret_value()
            scheme = "" if self.auth_scheme.lower() in {"none", "raw"} else self.auth_scheme
            all_headers[self.auth_header] = f"{scheme} {value}".strip() if scheme else value
            # KraftApp/Supabase accepts X-API-Key and some deployments also require Bearer.
            if self.auth_header.lower() != "authorization":
                all_headers.setdefault("Authorization", f"Bearer {value}")
        all_headers.update(headers or {})
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.request(method, url or self.url(path), params=params, json=json, headers=all_headers)
        except httpx.HTTPError as exc:
            raise ExternalServiceError(f"{self.system}: błąd połączenia: {exc}", code=f"{self.system}_UNREACHABLE") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ExternalServiceError(
                f"{self.system}: HTTP {response.status_code}",
                code=f"{self.system}_HTTP_{response.status_code}",
                details={"body": response.text[:2000]},
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ExternalServiceError(
                f"{self.system}: odpowiedź nie jest JSON-em",
                code=f"{self.system}_BAD_RESPONSE",
                details={"body": response.text[:2000]},
            ) from exc


def pick(data: Any, *keys: str) -> Any:
    """Pierwsza niepusta wartość spośród kluczy (szuka też w data['data'])."""
    if not isinstance(data, dict):
        return None
    for source in (data, data.get("data") if isinstance(data.get("data"), dict) else None):
        if not source:
            continue
        for key in keys:
            if source.get(key) is not None:
                return source[key]
    return None


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
