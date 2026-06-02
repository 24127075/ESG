"""Central configuration loaded from the environment (SDAD §2.1, §4.1).

A single typed :class:`Settings` object so modules don't scatter ``os.getenv``
calls. In Production the sensitive fields should be hydrated from a secrets
manager (see :mod:`esg_pipeline.common.secrets`), not a plaintext ``.env``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    # --- Redis (rate limit / quota / cache / circuit breaker) ---
    redis_host: str = field(default_factory=lambda: _env("REDIS_HOST", "localhost"))
    redis_port: int = field(default_factory=lambda: int(_env("REDIS_PORT", "6379")))
    redis_db: int = field(default_factory=lambda: int(_env("REDIS_DB", "0")))

    # --- Tier 2: Google Custom Search Engine ---
    google_api_key: str | None = field(default_factory=lambda: _env("GOOGLE_API_KEY"))
    google_cx_id: str | None = field(default_factory=lambda: _env("GOOGLE_CX_ID"))

    # --- Monitoring (§5) ---
    slack_webhook: str | None = field(default_factory=lambda: _env("SLACK_ALERT_WEBHOOK"))

    # --- Storage ---
    local_storage_root: str = field(default_factory=lambda: _env("LOCAL_STORAGE_ROOT", "./data"))
    static_fallback_root: str = field(
        default_factory=lambda: _env("STATIC_FALLBACK_ROOT", "./data/static_fallback")
    )
    checkpoint_db: str = field(default_factory=lambda: _env("CHECKPOINT_DB", "./data/checkpoint.db"))
    s3_bucket: str | None = field(default_factory=lambda: _env("S3_BUCKET"))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "ap-southeast-1"))

    # --- Metadata / state machine DB (§4.2) ---
    database_url: str = field(
        default_factory=lambda: _env("DATABASE_URL", "sqlite:///./data/documents.db")
    )

    # --- Celery (§1) ---
    celery_broker_url: str = field(
        default_factory=lambda: _env("CELERY_BROKER_URL", "redis://localhost:6379/1")
    )
    celery_result_backend: str = field(
        default_factory=lambda: _env("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")
    )

    # --- Phase 2 ---
    taxonomy_path: str = field(
        default_factory=lambda: _env("TAXONOMY_PATH", "config/esg_taxonomy.json")
    )

    @classmethod
    def load(cls) -> "Settings":
        """Build settings from the current environment."""
        return cls()


# Importable default instance.
settings = Settings.load()
