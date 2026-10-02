from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables (prefix ``IF_``)."""

    model_config = SettingsConfigDict(env_prefix="IF_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "InvoiceFlow"

    database_url: str = "postgresql+psycopg://invoiceflow:invoiceflow@localhost:5432/invoiceflow"
    redis_url: str | None = "redis://localhost:6379/0"

    # Used to sign download URLs and other tokens. MUST be overridden in production.
    secret_key: str = "dev-insecure-secret-change-me"  # noqa: S105
    api_base_url: str = "http://localhost:8000"
    # Fernet key(s) for secrets at rest (provider/payment credentials). Comma-separated; the
    # first encrypts, all decrypt (rotation). Derived from secret_key in development only.
    encryption_keys: str | None = None

    customer_app_url: str = "http://localhost:5173"
    admin_app_url: str = "http://localhost:5174"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:5174"]

    # Sessions
    session_cookie_name: str = "if_session"
    admin_session_cookie_name: str = "if_admin_session"
    session_ttl_hours: int = 24 * 14
    admin_session_ttl_hours: int = 12
    cookie_secure: bool = False
    csrf_header_name: str = "X-Requested-With"

    # Tokens
    email_verification_ttl_hours: int = 48
    password_reset_ttl_minutes: int = 60

    # Email
    email_driver: Literal["console", "smtp", "memory"] = "console"
    email_from: str = "InvoiceFlow <no-reply@invoiceflow.local>"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False

    # Object storage (original invoices and generated exports; always private)
    storage_driver: Literal["local", "s3"] = "local"
    storage_local_path: str = "./storage"
    s3_bucket: str = "invoiceflow"
    s3_endpoint_url: str | None = None  # e.g. http://localhost:9000 for MinIO
    s3_region: str = "us-east-1"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    signed_url_ttl_seconds: int = 300

    # Uploads (plan entitlements apply on top; these are platform hard limits)
    upload_hard_max_file_mb: int = 200
    upload_max_zip_uncompressed_mb: int = 1024
    upload_max_zip_ratio: int = 200
    upload_max_pdf_pages: int = 100

    # Processing worker
    extraction_handler: str = "ai"  # "fake" for local development without a provider
    worker_poll_seconds: float = 1.0
    job_lease_seconds: int = 300
    job_max_attempts: int = 3
    job_retry_base_seconds: int = 15
    max_concurrent_jobs_per_workspace: int = 3

    # Rate limiting
    rate_limit_enabled: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def model_post_init(self, __context) -> None:
        if self.is_production and self.secret_key.startswith("dev-insecure"):
            raise ValueError("IF_SECRET_KEY must be set in production")
        if self.is_production and not self.encryption_keys:
            raise ValueError("IF_ENCRYPTION_KEYS must be set in production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
