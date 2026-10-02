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

    # Rate limiting
    rate_limit_enabled: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
