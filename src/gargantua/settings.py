"""Application settings, loaded from environment + ``.env``.

A single ``Settings`` instance is the source of truth for runtime configuration.
We use ``pydantic-settings`` so values are typed, validated, and override-able
via env vars without scattering ``os.environ.get(...)`` calls across the codebase.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. See ``.env.example`` for documentation of every field."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ runtime
    app_host: str = "0.0.0.0"
    app_port: int = 7777
    app_log_level: str = "info"
    runtime_env: str = Field(default="dev", description="'dev' or 'prd'")

    # ----------------------------------------------------------------- database
    database_url: str = Field(
        default="postgresql+psycopg://gargantua:gargantua@localhost:5432/gargantua",
        description="Sync DB URL used by Alembic and Agno's PostgresDb.",
    )
    database_url_async: str = Field(
        default="postgresql+psycopg://gargantua:gargantua@localhost:5432/gargantua",
        description=(
            "Async DB URL used by the FastAPI app's request handlers. "
            "Same psycopg-3 dialect as the sync URL; SQLAlchemy switches modes "
            "based on whether you call create_engine() vs create_async_engine()."
        ),
    )

    # ---------------------------------------------------------------------- LLM
    #
    # There is **no global LLM setting** — the model is configured per-row on
    # the ``agents.model`` column and passed verbatim into Agno's model-as-
    # string parser (e.g. ``"openrouter:anthropic/claude-3.5-sonnet"``,
    # ``"openai:gpt-4o-mini"``, ``"gemini:gemini-2.0-flash"``).
    #
    # API keys are read by Agno's per-provider model classes directly from
    # provider-specific env vars at the moment the transient agent is
    # constructed (see :func:`gargantua.registry.build_agno_agent`):
    #
    #   * OpenRouter  ->  OPENROUTER_API_KEY
    #   * OpenAI      ->  OPENAI_API_KEY
    #   * Anthropic   ->  ANTHROPIC_API_KEY
    #   * Google      ->  GOOGLE_API_KEY
    #   * Groq        ->  GROQ_API_KEY
    #
    # Set whichever env vars match the providers your agents use; this
    # ``Settings`` class deliberately doesn't shadow them.

    # --------------------------------------------------------- secrets at rest
    master_key: str = Field(
        default="",
        description=(
            "Base64-encoded 32-byte KEK used to envelope-encrypt secret values. "
            "Generate via `gargantua-admin generate-master-key`."
        ),
    )

    # ---------------------------------------------------------------------- JWT
    jwt_private_key_path: Path = Path("./secrets/jwt_private.pem")
    jwt_public_key_path: Path = Path("./secrets/jwt_public.pem")
    jwt_issuer: str = "gargantua"
    jwt_audience: str = "gargantua"
    jwt_access_ttl_seconds: int = 43_200
    jwt_refresh_ttl_seconds: int = 2_592_000

    # -------------------------------------------------------- bootstrap admin
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""

    # ---------------------------------------------------------------- MCP cache
    mcp_cache_idle_ttl_seconds: int = 300
    mcp_cache_reaper_interval_seconds: int = 30

    # --------------------------------------------------------------------- CORS
    cors_origins: str = "http://localhost:3000"

    # ----------------------------------------------------------------- UI mount
    # Path to the Next.js static export.  When the directory exists, the
    # FastAPI app mounts it at ``/`` so the UI is served same-origin as
    # the API (no CORS in prod).  In dev (no build available), the mount
    # is skipped and the UI is run separately via ``pnpm dev``.
    ui_static_root: Path = Path("./ui/out")

    # ------------------------------------------------------------ agno debug
    # When true, every transient Agent / Team built by :mod:`gargantua.registry`
    # is constructed with ``debug_mode=True``.  Agno then bumps its own
    # ``agno`` / ``agno-team`` loggers to DEBUG and prints the full run
    # trace (model prompts, tool calls + args + results, intermediate
    # reasoning) to the uvicorn console.  Useful for diagnosing opaque
    # tool errors like "fetch failed" coming back from an MCP server.
    #
    # Leave OFF in production: prompts and tool args often contain
    # sensitive data, and the rich traces are noisy at scale.
    agno_debug: bool = Field(
        default=False,
        description="Enable Agno's verbose debug logging on every run.",
    )

    # ---------------------------------------------------------------- computed
    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        """Parse ``cors_origins`` into a list of trimmed origins."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_prod(self) -> bool:
        return self.runtime_env.lower() in {"prd", "prod", "production"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached ``Settings`` instance.

    Side-effect: also pushes ``.env`` values into ``os.environ`` (with
    ``override=False``) so third-party libraries that read env vars
    directly — most notably Agno's model classes
    (``OPENROUTER_API_KEY``, ``OPENAI_API_KEY``, …) — see what's in
    ``.env``.  Pydantic-settings on its own only hydrates this class's
    own fields; it doesn't touch ``os.environ``.

    We pass an explicit ``dotenv_path`` so :func:`load_dotenv` doesn't
    walk up the directory tree looking for a ``.env`` in an ancestor
    folder (which would leak the workspace's ``.env`` into tests that
    deliberately chdir to a clean ``tmp_path``).  Mirrors pydantic-
    settings's own ``env_file=".env"`` resolution (cwd-relative).
    """
    load_dotenv(dotenv_path=".env", override=False)
    return Settings()
