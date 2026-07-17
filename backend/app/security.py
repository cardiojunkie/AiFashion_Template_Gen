import base64
import hashlib
import ipaddress
import json
import logging
import socket
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

from .settings import Settings, get_settings

SECRET_MARKERS = (
    "api_key",
    "access_key",
    "secret",
    "password",
    "token",
    "authorization",
    "database_url",
    "redis_url",
    "broker_url",
)


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _cipher(settings: Settings | None = None) -> Fernet:
    raw = (settings or get_settings()).encryption_key.get_secret_value().encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))


def encrypt_secret(value: str, settings: Settings | None = None) -> bytes:
    return _cipher(settings).encrypt(value.encode())


def decrypt_secret(value: bytes | None, settings: Settings | None = None) -> str | None:
    if value is None:
        return None
    try:
        return _cipher(settings).decrypt(value).decode()
    except InvalidToken as exc:
        raise ValueError("stored secret cannot be decrypted") from exc


def is_secret_key(key: object) -> bool:
    name = str(key).casefold()
    return any(marker in name for marker in SECRET_MARKERS)


def redact_secrets(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]" if is_secret_key(key) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    return value


def sanitize_snapshot(value: Mapping[str, object]) -> dict[str, object]:
    """Recursively omit secrets instead of freezing even redacted placeholders."""
    return {
        key: sanitize_snapshot(item)
        if isinstance(item, Mapping)
        else [sanitize_snapshot(entry) if isinstance(entry, Mapping) else entry for entry in item]
        if isinstance(item, list)
        else item
        for key, item in value.items()
        if not is_secret_key(key)
    }


def validate_llm_endpoint(
    url: str | None,
    settings: Settings | None = None,
    resolver=socket.getaddrinfo,
) -> None:
    if not url:
        return
    config = settings or get_settings()
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise ValueError("LLM endpoint URLs cannot contain credentials")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("LLM endpoint must be an absolute HTTP/HTTPS URL")
    allowlisted = parsed.hostname in config.allowed_private_llm_hosts
    try:
        addresses = {
            ipaddress.ip_address(item[4][0].split("%", 1)[0])
            for item in resolver(parsed.hostname, parsed.port or 0, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise ValueError("LLM endpoint host could not be resolved") from exc
    if not addresses:
        raise ValueError("LLM endpoint host could not be resolved")
    if any(not address.is_global for address in addresses) and not allowlisted:
        raise ValueError("private LLM endpoint hosts must be explicitly allowlisted")
    if parsed.scheme == "http" and not (allowlisted and config.allow_insecure_llm_endpoints):
        raise ValueError(
            "LLM endpoint must use HTTPS; explicitly allowlist private HTTP hosts to opt in"
        )


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, Mapping):
            record.msg = redact_secrets(record.msg)
        if isinstance(record.args, Mapping):
            record.args = redact_secrets(record.args)
        return True


class JSONLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            default=str,
        )
