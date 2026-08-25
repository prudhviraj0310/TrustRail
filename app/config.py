"""Application configuration.

Settings are read from environment variables (and an optional ``.env`` file).
The default ``database_url`` is SQLite so the project runs with zero setup.
In production / docker-compose we point ``DATABASE_URL`` at PostgreSQL — the
code is identical because SQLAlchemy abstracts the backend.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "TrustRail"
    environment: str = "local"

    # SQLite by default (zero-setup). Override with e.g.
    #   postgresql+psycopg://trustrail:trustrail@localhost:5432/trustrail
    database_url: str = "sqlite:///./trustrail.db"

    # Auto-create tables on startup. Convenient for dev/SQLite; in production
    # prefer Alembic migrations (see migrations/) and set this to false.
    auto_create_tables: bool = True

    # Seed the synthetic merchant catalogue on startup.
    seed_merchant: bool = True

    # ----------------------------------------------------------------- #
    # Phase 2 — payment gateway selection.
    #
    # "mock"     -> deterministic in-process MockPaymentGateway (default).
    #               Keeps `make test` and local dev fully offline.
    # "razorpay" -> RazorpayGateway against Razorpay **Test Mode**. Requires
    #               the three razorpay_* credentials below to be set.
    #
    # The default MUST stay "mock" so the suite never needs the network or
    # real credentials.
    # ----------------------------------------------------------------- #
    payment_gateway: str = "mock"

    # Razorpay Test Mode credentials. Server-side ONLY — never exposed to the
    # AI agent, never returned in a response, never written to an audit event.
    # Leave blank to run in mock mode.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    @property
    def razorpay_configured(self) -> bool:
        """True only when every Razorpay credential is present."""
        return bool(
            self.razorpay_key_id
            and self.razorpay_key_secret
            and self.razorpay_webhook_secret
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
