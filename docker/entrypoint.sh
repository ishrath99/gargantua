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
#
# Common dev footgun: the bind-mounted host directory ends up owned by
# the host's UID with restrictive perms, and the container (non-root,
# UID 1001) can't read or write it.  We detect both failure modes
# (unwritable parent dir, unreadable existing key file) and degrade
# gracefully to ephemeral keys in ``/tmp/secrets`` so the container at
# least boots — every restart invalidates issued tokens, but the
# operator sees a precise remediation message in the logs instead of a
# PermissionError 30 frames deep in uvicorn.
JWT_PRIVATE_KEY_PATH="${JWT_PRIVATE_KEY_PATH:-/app/secrets/jwt_private.pem}"
JWT_PUBLIC_KEY_PATH="${JWT_PUBLIC_KEY_PATH:-/app/secrets/jwt_public.pem}"
SECRETS_DIR="$(dirname "${JWT_PRIVATE_KEY_PATH}")"

_current_uid="$(id -u)"
_current_gid="$(id -g)"

# Best-effort: try to ensure the directory exists. Bind mounts always
# exist already; this matters for the docker-volume / no-mount paths.
mkdir -p "${SECRETS_DIR}" 2>/dev/null || true

# Detect: is the (likely host-mounted) secrets dir actually writable?
if ! [ -w "${SECRETS_DIR}" ]; then
    cat <<EOF >&2
[entrypoint] WARN: ${SECRETS_DIR} is not writable by uid=${_current_uid} gid=${_current_gid}.
[entrypoint]       The host-mounted directory is probably owned by your host
[entrypoint]       user with restrictive perms.  To make keys persist across
[entrypoint]       restarts, run ONE of the following on the host and restart:
[entrypoint]
[entrypoint]           chmod 777 secrets/                     # easiest, dev-only
[entrypoint]           sudo chown -R 1001:1001 secrets/       # safer, persistent
[entrypoint]
[entrypoint]       Falling back to ephemeral keys in /tmp/secrets so the app
[entrypoint]       still boots.  *Every restart invalidates issued tokens.*
EOF
    JWT_PRIVATE_KEY_PATH=/tmp/secrets/jwt_private.pem
    JWT_PUBLIC_KEY_PATH=/tmp/secrets/jwt_public.pem
    export JWT_PRIVATE_KEY_PATH JWT_PUBLIC_KEY_PATH
    SECRETS_DIR=/tmp/secrets
    mkdir -p "${SECRETS_DIR}"
fi

# Detect: an existing key file we can't read.  Same root cause (host
# UID mismatch) but a different failure mode — the file is there, we
# just can't open it.  Refuse to silently overwrite; refuse to silently
# crash later.  Tell the operator exactly which file is the problem.
if [ -f "${JWT_PRIVATE_KEY_PATH}" ] && ! [ -r "${JWT_PRIVATE_KEY_PATH}" ]; then
    cat <<EOF >&2
[entrypoint] ERROR: ${JWT_PRIVATE_KEY_PATH} exists but is not readable by
[entrypoint]        uid=${_current_uid} gid=${_current_gid}.  This usually means the file was
[entrypoint]        generated on the host with a different UID and mode 0600.
[entrypoint]        Remediation (on the host):
[entrypoint]
[entrypoint]            rm -f secrets/jwt_*.pem
[entrypoint]            chmod 777 secrets/
[entrypoint]            docker compose restart app
EOF
    exit 1
fi

if [ ! -f "${JWT_PRIVATE_KEY_PATH}" ]; then
    echo "[entrypoint] JWT private key not found at ${JWT_PRIVATE_KEY_PATH}; generating a fresh keypair."
    gargantua-admin generate-jwt-keys --out-dir "${SECRETS_DIR}"
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
