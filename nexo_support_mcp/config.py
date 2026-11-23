"""Konfiguracja z `.env` i zmiennych środowiskowych.

Kolejność szukania pliku `.env`:
1. `NEXO_MCP_ENV_FILE` (jawna ścieżka),
2. `.env` w bieżącym katalogu,
3. `.env` w katalogu projektu (obok `pyproject.toml`).

Zmienne ustawione w środowisku mają pierwszeństwo przed `.env`.
Względna `NEXO_MCP_DB_PATH` jest liczona od katalogu znalezionego `.env`
(albo od bieżącego katalogu, jeśli `.env` nie ma).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, SecretStr

from .errors import ConfigError

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MOCKABLE_INTEGRATIONS = ("email", "baselinker", "stock", "warehouse", "invoice")

# Sanitized-export repair (vendor/PATCHES.md P1): placeholder only, production sets NEXO_SIGNATURE.
DEFAULT_SIGNATURE = "Pozdrawiamy,\nExample Shop\nsupport@example.invalid"


class MerchantConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    # Fragmenty domeny/adresu nadawcy, po których rozpoznajemy merchanta.
    patterns: tuple[str, ...]


DEFAULT_MERCHANTS: tuple[MerchantConfig, ...] = (
    MerchantConfig(code="SUPER_MERCHANT", name="SuperMerchant", patterns=("supermerchant",)),
    MerchantConfig(code="GREAT_STORE", name="GreatStore", patterns=("greatstore",)),
    MerchantConfig(code="EXTRA_STORE", name="ExtraStore", patterns=("extrastore",)),
)


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    # General
    env: str = "development"
    db_path: Path
    env_file: Path | None = None
    env_file_error: str | None = None
    log_level: str = "INFO"
    agent_actor: str = "agent"
    approvers: tuple[str, ...] = ()
    stock_max_age_hours: float = 24.0
    http_timeout: float = 20.0
    mock_adapters: frozenset[str] = frozenset()
    signature: str = DEFAULT_SIGNATURE
    merchants: tuple[MerchantConfig, ...] = DEFAULT_MERCHANTS

    # Email
    email_address: str = "support@example.invalid"
    email_from_name: str = "Example Shop"
    imap_host: str | None = None
    imap_port: int = 993
    imap_security: str = "ssl"
    imap_username: str | None = None
    imap_password: SecretStr | None = None
    imap_mailbox: str = "INBOX"
    imap_initial_lookback_days: int = 7
    imap_sent_folder: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_security: str = "ssl"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None

    # Baselinker
    baselinker_api_token: SecretStr | None = None
    baselinker_api_url: str = "https://api.baselinker.com/connector.php"

    # Stock / supplier
    stock_api_url: str | None = None
    stock_api_token: SecretStr | None = None
    stock_api_auth_header: str = "Authorization"
    stock_api_auth_scheme: str = "Bearer"

    # Warehouse
    warehouse_api_url: str | None = None
    warehouse_api_token: SecretStr | None = None
    warehouse_api_auth_header: str = "Authorization"
    warehouse_api_auth_scheme: str = "Bearer"

    # Invoice corrections
    invoice_api_url: str | None = None
    invoice_api_token: SecretStr | None = None
    invoice_api_auth_header: str = "Authorization"
    invoice_api_auth_scheme: str = "Bearer"

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}

    @property
    def imap_configured(self) -> bool:
        return bool(self.imap_host and self.imap_username and self.imap_password)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.email_address)

    @property
    def baselinker_configured(self) -> bool:
        return bool(self.baselinker_api_token and self.baselinker_api_url)

    @property
    def stock_configured(self) -> bool:
        return bool(self.stock_api_url)

    @property
    def warehouse_configured(self) -> bool:
        return bool(self.warehouse_api_url)

    @property
    def invoice_configured(self) -> bool:
        return bool(self.invoice_api_url)

    def missing_config(self) -> dict[str, list[str]]:
        """Brakujące zmienne per integracja (do nexo_health)."""
        missing: dict[str, list[str]] = {}

        def need(group: str, name: str, value: object) -> None:
            if not value:
                missing.setdefault(group, []).append(name)

        need("imap", "IMAP_HOST", self.imap_host)
        need("imap", "IMAP_USERNAME", self.imap_username)
        need("imap", "IMAP_PASSWORD", self.imap_password)
        need("smtp", "SMTP_HOST", self.smtp_host)
        need("baselinker", "BASELINKER_API_TOKEN", self.baselinker_api_token)
        need("stock", "STOCK_API_URL", self.stock_api_url)
        need("warehouse", "WAREHOUSE_API_URL", self.warehouse_api_url)
        need("invoice", "INVOICE_API_URL", self.invoice_api_url)
        return missing


def _find_env_file(environ: Mapping[str, str]) -> tuple[Path | None, str | None]:
    explicit = environ.get("NEXO_MCP_ENV_FILE")
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path.resolve(), None
        return None, f"NEXO_MCP_ENV_FILE wskazuje na nieistniejący plik: {explicit}"
    for candidate in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
        if candidate.is_file():
            return candidate.resolve(), None
    return None, None


def _str(env: Mapping[str, str], key: str, default: str | None = None) -> str | None:
    value = env.get(key)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _secret(env: Mapping[str, str], key: str) -> SecretStr | None:
    value = _str(env, key)
    return SecretStr(value) if value else None


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    value = _str(env, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{key} musi być liczbą całkowitą, jest: {value!r}") from exc


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    value = _str(env, key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{key} musi być liczbą, jest: {value!r}") from exc


def _list(env: Mapping[str, str], key: str) -> tuple[str, ...]:
    value = _str(env, key)
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _security(env: Mapping[str, str], key: str, port: int, ssl_port: int) -> str:
    value = (_str(env, key) or "").lower()
    if not value:
        return "ssl" if port == ssl_port else "starttls"
    if value not in {"ssl", "starttls", "none"}:
        raise ConfigError(f"{key} musi być ssl | starttls | none, jest: {value!r}")
    return value


def _merchants(env: Mapping[str, str]) -> tuple[MerchantConfig, ...]:
    """NEXO_MERCHANTS=KOD:Nazwa:wzorzec1|wzorzec2;KOD2:Nazwa2:wzorzec"""
    raw = _str(env, "NEXO_MERCHANTS")
    if not raw:
        return DEFAULT_MERCHANTS
    merchants = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) != 3:
            raise ConfigError(f"NEXO_MERCHANTS: zły wpis {chunk!r}, oczekiwano KOD:Nazwa:wzorzec1|wzorzec2")
        code, name, patterns = (p.strip() for p in parts)
        merchants.append(
            MerchantConfig(
                code=code.upper(),
                name=name,
                patterns=tuple(p.strip().lower() for p in patterns.split("|") if p.strip()),
            )
        )
    return tuple(merchants)


def load_settings(environ: Mapping[str, str] | None = None, *, load_env_file: bool = True) -> Settings:
    """Wczytuje ustawienia. `environ` pozwala testom podać własne wartości."""
    env_file: Path | None = None
    env_file_error: str | None = None
    if environ is None:
        env_file, env_file_error = _find_env_file(os.environ)
        if load_env_file and env_file:
            load_dotenv(env_file, override=False)
        environ = os.environ

    base_dir = env_file.parent if env_file else Path.cwd()
    db_path = Path(_str(environ, "NEXO_MCP_DB_PATH", "./data/nexo_support_ledger.sqlite")).expanduser()
    if not db_path.is_absolute():
        db_path = (base_dir / db_path).resolve()

    mock_raw = {m.lower() for m in _list(environ, "NEXO_MOCK_ADAPTERS")}
    mock_adapters = set(MOCKABLE_INTEGRATIONS) if "all" in mock_raw else mock_raw
    unknown = mock_adapters - set(MOCKABLE_INTEGRATIONS)
    if unknown:
        raise ConfigError(f"NEXO_MOCK_ADAPTERS: nieznane integracje {sorted(unknown)}")

    imap_port = _int(environ, "IMAP_PORT", 993)
    smtp_port = _int(environ, "SMTP_PORT", 465)
    signature = _str(environ, "NEXO_SIGNATURE")

    settings = Settings(
        env=_str(environ, "NEXO_MCP_ENV", "development"),
        db_path=db_path,
        env_file=env_file,
        env_file_error=env_file_error,
        log_level=(_str(environ, "LOG_LEVEL", "INFO") or "INFO").upper(),
        agent_actor=_str(environ, "NEXO_AGENT_ACTOR", "agent"),
        approvers=_list(environ, "NEXO_APPROVERS"),
        stock_max_age_hours=_float(environ, "NEXO_STOCK_MAX_AGE_HOURS", 24.0),
        http_timeout=_float(environ, "NEXO_HTTP_TIMEOUT", 20.0),
        mock_adapters=frozenset(mock_adapters),
        signature=signature.replace("\\n", "\n") if signature else DEFAULT_SIGNATURE,
        merchants=_merchants(environ),
        email_address=(_str(environ, "EMAIL_ADDRESS", "support@example.invalid") or "").lower(),
        email_from_name=_str(environ, "EMAIL_FROM_NAME", "Example Shop"),
        imap_host=_str(environ, "IMAP_HOST"),
        imap_port=imap_port,
        imap_security=_security(environ, "IMAP_SECURITY", imap_port, 993),
        imap_username=_str(environ, "IMAP_USERNAME"),
        imap_password=_secret(environ, "IMAP_PASSWORD"),
        imap_mailbox=_str(environ, "IMAP_MAILBOX", "INBOX"),
        imap_initial_lookback_days=_int(environ, "IMAP_INITIAL_LOOKBACK_DAYS", 7),
        imap_sent_folder=_str(environ, "IMAP_SENT_FOLDER"),
        smtp_host=_str(environ, "SMTP_HOST"),
        smtp_port=smtp_port,
        smtp_security=_security(environ, "SMTP_SECURITY", smtp_port, 465),
        smtp_username=_str(environ, "SMTP_USERNAME"),
        smtp_password=_secret(environ, "SMTP_PASSWORD"),
        baselinker_api_token=_secret(environ, "BASELINKER_API_TOKEN"),
        baselinker_api_url=_str(environ, "BASELINKER_API_URL", "https://api.baselinker.com/connector.php"),
        stock_api_url=_str(environ, "STOCK_API_URL"),
        stock_api_token=_secret(environ, "STOCK_API_TOKEN"),
        stock_api_auth_header=_str(environ, "STOCK_API_AUTH_HEADER", "Authorization"),
        stock_api_auth_scheme=_str(environ, "STOCK_API_AUTH_SCHEME", "Bearer") or "",
        warehouse_api_url=_str(environ, "WAREHOUSE_API_URL"),
        warehouse_api_token=_secret(environ, "WAREHOUSE_API_TOKEN"),
        warehouse_api_auth_header=_str(environ, "WAREHOUSE_API_AUTH_HEADER", "Authorization"),
        warehouse_api_auth_scheme=_str(environ, "WAREHOUSE_API_AUTH_SCHEME", "Bearer") or "",
        invoice_api_url=_str(environ, "INVOICE_API_URL"),
        invoice_api_token=_secret(environ, "INVOICE_API_TOKEN"),
        invoice_api_auth_header=_str(environ, "INVOICE_API_AUTH_HEADER", "Authorization"),
        invoice_api_auth_scheme=_str(environ, "INVOICE_API_AUTH_SCHEME", "Bearer") or "",
    )

    if settings.is_production and settings.mock_adapters:
        raise ConfigError("NEXO_MOCK_ADAPTERS jest niedozwolone przy NEXO_MCP_ENV=production")
    if env_file_error:
        log.warning(env_file_error)
    return settings
