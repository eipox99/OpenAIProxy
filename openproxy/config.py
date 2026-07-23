from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Server ----
    host: str = "0.0.0.0"
    port: int = 8000

    # ---- Database ----
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/openproxy.db"

    # ---- Encryption ----
    # Used to encrypt API keys at rest. Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = ""

    # ---- Circuit breaker defaults ----
    circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown: int = 30  # seconds

    # ---- Global defaults ----
    default_timeout: int = 60

    # ---- Authentication ----
    # Set to a strong random password to enable authentication.
    # Leave empty to disable authentication (not recommended for production).
    auth_token: str = ""


settings = Settings()
