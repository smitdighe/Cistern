"""Application settings loaded from environment / .env.

Importing this module constructs the singleton ``settings``. If a required
variable (DATABASE_URL, DATABASE_ADMIN_URL) is missing, ``ValidationError`` is
raised at import time — the process fails fast rather than at first query.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The .env lives at the repository root, one level above this package. Anchored
# to this file rather than left as a bare ".env" so configuration does not
# depend on the working directory: `alembic -c alembic.ini ...` run from
# backend/ resolves the same file as `uvicorn backend.main:app` run from the
# root.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Environment-backed configuration."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Required — no defaults, so a missing value fails at import.
    DATABASE_URL: str = Field(description="Read-only Postgres role, used for query execution.")
    DATABASE_ADMIN_URL: str = Field(
        description="Admin Postgres role, used only for introspection and migrations."
    )

    # Optional — providers are not needed for DB-only code paths.
    GROQ_API_KEY: str = ""
    CEREBRAS_API_KEY: str = ""

    # Generation-tier model. Configurable because providers retire models on
    # their own schedule; a retirement should be an env change, not a deploy.
    # Keep it a different model family from Cerebras' gpt-oss-120b so the
    # corrector and judge never grade output from their own model.
    GROQ_MODEL: str = "qwen/qwen3.8-27b"

    ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    MAX_CORRECTION_ATTEMPTS: int = 3
    QUERY_TIMEOUT_SECONDS: int = 10
    DEFAULT_QUERY_LIMIT: int = 500

    # How long GET /health may serve a memoised probe result.
    #
    # Each refresh costs two authenticated provider calls and two database
    # connections, so without this the upstream bill scales with the number of
    # open dashboards times their poll rate. Sixty seconds decouples the two:
    # cost becomes a function of time, not of how many people are watching.
    # A degraded result is held far more briefly — see routes/health.py.
    HEALTH_CACHE_SECONDS: int = 60

    # Comma-separated rather than a list field: pydantic-settings parses complex
    # types as JSON, which would make the .env value `["http://..."]`. A plain
    # string keeps the env file readable. Default is the Vite dev server origin.
    CORS_ORIGINS: str = "http://localhost:5173"

    @property
    def cors_origins(self) -> list[str]:
        """Allowed browser origins, empty entries dropped."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
