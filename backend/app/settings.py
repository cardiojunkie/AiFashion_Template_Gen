from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Catalog Enrichment Studio"
    environment: Literal["development", "test", "production"] = Field(
        "development", validation_alias=AliasChoices("ENVIRONMENT", "APP_ENV")
    )
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./catalog.db"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: SecretStr = SecretStr("minioadmin")
    s3_bucket: str = "catalog-enrichment"
    s3_region: str = "us-east-1"
    secret_key: SecretStr = SecretStr("dev-only-secret-change-me")
    encryption_key: SecretStr = Field(
        SecretStr("dev-only-encryption-key-change-me"),
        validation_alias=AliasChoices("ENCRYPTION_KEY", "FERNET_KEY"),
    )
    cors_origins: list[str] = ["http://localhost:5173"]
    allow_insecure_llm_endpoints: bool = False
    allowed_private_llm_hosts: list[str] = []
    task_always_eager: bool = False
    max_upload_bytes: int = 50 * 1024 * 1024
    soft_delete_days: int = 30

    @model_validator(mode="after")
    def reject_development_secrets_in_production(self) -> "Settings":
        if self.environment != "production":
            return self
        placeholders = {"change-me", "minioadmin"}
        insecure = {
            "DATABASE_URL": "change-me" in self.database_url
            or self.database_url.startswith("sqlite"),
            "S3_ACCESS_KEY": self.s3_access_key in placeholders,
            "S3_SECRET_KEY": self.s3_secret_key.get_secret_value() in placeholders,
            "SECRET_KEY": self.secret_key.get_secret_value()
            in placeholders | {"dev-only-secret-change-me"},
            "ENCRYPTION_KEY": self.encryption_key.get_secret_value()
            in placeholders | {"dev-only-encryption-key-change-me"},
        }
        bad = [name for name, is_bad in insecure.items() if is_bad]
        if bad:
            raise ValueError(f"production requires non-default values for: {', '.join(bad)}")
        return self

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    def public_snapshot(self) -> dict[str, object]:
        """Return reproducible non-secret runtime settings."""
        hidden = {
            "database_url",
            "redis_url",
            "celery_broker_url",
            "celery_result_backend",
            "s3_access_key",
            "s3_secret_key",
            "secret_key",
            "encryption_key",
        }
        return {
            key: value for key, value in self.model_dump(mode="json").items() if key not in hidden
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
