#!/usr/bin/env bash
#
# Container entrypoint: lazy JWT keypair generation → alembic upgrade
# → exec the real command (uvicorn by default).
#
# Why ``exec`` at the end: signals from ``docker stop`` / k8s reach the
# Python process directly so it can drain its SSE generators and
# release MCP cache leases on shutdown (see ``gargantua.main.lifespan``).

set -euo pipefail

# ---------------------------------------------------------------------------
# JWT keys
# ---------------------------------------------------------------------------
# The bootstrap admin + ``/auth/login`` need an RS256 keypair.  If the
# operator hasn't mounted one, generate it on the fly into the path the
# settings module points at.  Persisted across restarts via a volume
# mount on the parent directory (``./secrets`` in compose).
JWT_PRIVATE_KEY_PATH="${JWT_PRIVATE_KEY_PATH:-/app/secrets/jwt_private.pem}"

if [ ! -f "${JWT_PRIVATE_KEY_PATH}" ]; then
    echo "[entrypoint] JWT private key not found at ${JWT_PRIVATE_KEY_PATH}; generating a fresh keypair."
    mkdir -p "$(dirname "${JWT_PRIVATE_KEY_PATH}")"
    gargantua-admin generate-jwt-keys \
        --out-dir "$(dirname "${JWT_PRIVATE_KEY_PATH}")"
fi

# ---------------------------------------------------------------------------
# Database migrations
# ---------------------------------------------------------------------------
# Run alembic up to head before the app takes traffic.  If migrations
# fail we abort (set -e), giving the orchestrator a chance to retry
# rather than booting a partially-migrated DB.
#
# Honour an opt-out so a CLI-only invocation
# (``docker run --rm gargantua gargantua-admin user list``) doesn't
# block on migrations.  Default ON.
if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
    echo "[entrypoint] running alembic upgrade head"
    alembic upgrade head
else
    echo "[entrypoint] SKIP_MIGRATIONS=1; not running alembic"
fi

# ---------------------------------------------------------------------------
# Hand off to the real command
# ---------------------------------------------------------------------------
exec "$@"
