# syntax=docker/dockerfile:1.7
#
# Multi-stage build for gargantua.
#
# Final image layout
# ------------------
#   /opt/venv         pip-installed gargantua + transitive deps
#   /app/src          source tree (uvicorn imports gargantua.main:app)
#   /app/alembic.ini  + alembic migrations live under src/gargantua/db/migrations
#   /app/ui/out       Next.js static export, served by FastAPI at /
#   /app/docker       entrypoint shim (key-gen + migrate + exec uvicorn)
#
# Build
# -----
#   docker build -t gargantua:dev .
#
# Run (with a postgres on host networking)
# ----------------------------------------
#   docker run --rm -p 7777:7777 --env-file .env \
#       -v $PWD/secrets:/app/secrets gargantua:dev
#
# See ``docker-compose.yml`` for the postgres + app combo.


# =============================================================================
# Stage 1 — Build the Next.js UI into ``ui/out``.
# =============================================================================
#
# Why pnpm + corepack: matches the dev workflow exactly (``pnpm-lock.yaml``
# is the lockfile), and corepack-installed pnpm avoids the ``npm install -g``
# tax + version drift.
FROM node:26-bookworm-slim AS ui-builder

# Disable Next's first-build telemetry network call; speeds up the
# stage in restricted-network builders and is the right default in CI.
ENV NEXT_TELEMETRY_DISABLED=1
# Same-origin in prod: the FastAPI app serves both /api and the UI.
# An empty base URL makes the client use relative URLs (``/auth/me``).
ENV NEXT_PUBLIC_API_BASE_URL=""

RUN corepack enable && corepack prepare pnpm@9.7.0 --activate

WORKDIR /ui

# Two-step copy so the dep-install layer is reused across edits to
# component code (lockfile rarely changes).
COPY ui/package.json ui/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

COPY ui/ ./
RUN pnpm build


# =============================================================================
# Stage 2 — Install the Python backend into a venv we copy whole.
# =============================================================================
#
# Why a venv and not site-packages: the final image has a different
# system Python install, and copying ``/opt/venv`` is the cleanest
# way to deliver every dep + the entry-point scripts (``gargantua-admin``)
# in one tree.  We don't need build deps in the final image because
# every wheel we pull (psycopg[binary], argon2-cffi, cryptography) ships
# pre-built linux/amd64+arm64 wheels.
FROM python:3.12-slim-bookworm AS py-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# A handful of OS packages help pip resolve a clean wheel set when one
# of our deps doesn't have an arm64 wheel on a given Python.  Kept
# minimal so the build stage stays under ~150MB.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Non-editable install: the wheel ends up under
# ``/opt/venv/lib/python3.12/site-packages/gargantua/`` and is fully
# self-contained, so the runtime stage only needs to copy ``/opt/venv``
# (no source tree dependency).  This is intentionally simpler than the
# "stub + editable" caching trick — Hatchling's editable layout would
# write a ``.pth`` pointing at ``/build/src``, which doesn't exist
# in the runtime image.
RUN pip install --upgrade pip wheel && pip install .


# =============================================================================
# Stage 3 — Runtime image.
# =============================================================================
#
# Slim Debian + system Python plus the two MCP-launcher runtimes the
# community has standardised on: Node 20 (for ``npx ...`` servers like
# ``@modelcontextprotocol/server-sequential-thinking``) and ``uv``/``uvx``
# (for Python-packaged servers like ``postgres-mcp``).  Without those
# the ``stdio`` MCPs from the seeded catalog — and most third-party
# tutorials — would fail at subprocess spawn.  Final image is ~500MB
# (Python venv with all deps is the dominant cost; Node + uv add ~85MB).
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    APP_HOST=0.0.0.0 \
    APP_PORT=7777 \
    # The UI mount path inside the image; matches the COPY below.
    UI_STATIC_ROOT=/app/ui/out \
    # JWT keys land here by default; mount a volume to persist them.
    JWT_PRIVATE_KEY_PATH=/app/secrets/jwt_private.pem \
    JWT_PUBLIC_KEY_PATH=/app/secrets/jwt_public.pem

# ``libpq5`` is the only runtime shared lib psycopg-binary loads at
# import time on some arches; keeping it explicit avoids surprises if
# pip falls back to a source build for any reason.  ``curl`` powers the
# healthcheck without pulling in ``netcat``/``wget``.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        tini \
        && rm -rf /var/lib/apt/lists/*

# Node 20 for ``npx``-launched MCP servers.  We piggy-back on the same
# multi-arch ``node:20-bookworm-slim`` image used by the UI build stage,
# so the same binary that built the UI also runs MCP subprocesses — no
# nodesource curl-pipe, no Debian-old Node 18.  ~55MB on the wire.
COPY --from=node:20-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:20-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# ``uv`` + ``uvx`` for Python-packaged MCP servers (``uvx postgres-mcp``
# in the seed catalog, and the entire growing ecosystem of ``uvx``-based
# tools).  Single static binary from the official Astral image; ~15MB.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Copy the prebuilt venv (gargantua + every transitive dep) and the
# source tree.  The venv alone covers all imports at runtime; ``src/``
# is here only so ``alembic.ini``'s ``script_location = src/gargantua/
# db/migrations`` still resolves from ``WORKDIR=/app``.  Alembic loads
# migration scripts by file-path, not by Python import, so duplicating
# them across both locations costs ~200KB and avoids a config rewrite.
COPY --from=py-builder /opt/venv /opt/venv
COPY --from=py-builder /build/src /app/src

COPY alembic.ini /app/alembic.ini

# UI static export.  The FastAPI app mounts this at ``/`` if the
# directory exists (see ``gargantua.main.create_app``).
COPY --from=ui-builder /ui/out /app/ui/out

# Entrypoint shim + start command.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Drop privileges.  Pre-create the secrets dir so the entrypoint can
# write generated JWT keys there without a chmod dance.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home --home-dir /home/app app \
    && mkdir -p /app/secrets \
    && chown -R app:app /app /home/app
USER app

WORKDIR /app
EXPOSE 7777

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:${APP_PORT}/health \
        | grep -q '"status":"ok"' || exit 1

# ``tini`` is the PID 1 we want — clean signal forwarding so ``docker
# stop`` actually closes the SSE generators in flight.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "gargantua.main:app", "--host", "0.0.0.0", "--port", "7777"]
