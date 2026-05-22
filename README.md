# Gargantua

An open ecosystem where agents, tools, MCP servers, workflows, and
teams coexist.  Built on [Agno](https://docs.agno.com/) `AgentOS` with
a DB-first model: admins define MCP servers, agents, and teams; users
chat with them through a Next.js UI (see `ui/`) or by hitting
`POST /v1/agents/{id}/runs` directly.

Backend covers: schema + Alembic, KEK + secrets, JWT auth, RBAC, audit
log, catalog of MCP types, MCP server + child-resource CRUD, agent +
team CRUD, MCP cache with leases / version bumps / child-resource
scoping, runtime routes (`/v1/agents/{id}/runs`, `/v1/teams/{id}/runs`),
`/me` projections, and an agent-template loader.

See `RUNBOOK.md` for day-2 ops procedures (KEK rotation, JWT rotation,
diagnosing a stuck MCP cache entry, etc.).

## Stack

- **Runtime**: Python 3.12, FastAPI, Agno 2.6.7, PostgreSQL 16+
- **Auth**: RS256 JWT minted by the app, verified by `AgentOS(authorization=True)`
- **Secrets**: AES-256-GCM envelope encryption under a single KEK
- **MCP lifecycle**: lazy cache keyed by `(server_id, sorted_child_resource_ids)`,
  per-key lock, ref-count, idle reaper, evict-all-variants on row change
- **DB**: SQLAlchemy 2.x with sync + async engines on the same psycopg-3 dialect

## Local quickstart

```bash
# 1. Install deps
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Generate the master key + JWT keypair
gargantua-admin generate-master-key            # prints base64; paste into MASTER_KEY in .env
mkdir -p secrets
gargantua-admin generate-jwt-keys --out-dir ./secrets

# 3. Configure .env (copy .env.example) — at minimum:
#    DATABASE_URL, DATABASE_URL_ASYNC, MASTER_KEY,
#    one LLM provider key (OPENROUTER_API_KEY is the easiest),
#    BOOTSTRAP_ADMIN_USERNAME, BOOTSTRAP_ADMIN_PASSWORD
cp .env.example .env

# 4. Bring up Postgres
docker compose up -d postgres   # if you have a compose file; otherwise any PG ≥ 16

# 5. Run migrations
alembic upgrade head

# 6. Seed the MCP server type catalog (idempotent)
gargantua-admin seed-catalog

# 7. Start the app — the lifespan auto-creates the bootstrap admin if the
#    users table is empty and BOOTSTRAP_ADMIN_* are set.
uvicorn gargantua.main:app --reload --port 7777
```

Sanity check the boot:

```bash
curl -s http://localhost:7777/health
curl -s -X POST http://localhost:7777/auth/login \
    -H 'content-type: application/json' \
    -d '{"username":"<bootstrap-username>","password":"<bootstrap-password>"}'
```

The login response carries `{ access_token, refresh_token }`; use the
`access_token` as `Authorization: Bearer …` against every other route.

## UI quickstart

```bash
cd ui
pnpm install
cp .env.example .env.local       # NEXT_PUBLIC_API_BASE_URL=http://localhost:7777
pnpm dev                         # http://localhost:3000
```

Tests (no backend required — network is stubbed):

```bash
pnpm typecheck && pnpm lint && pnpm test && pnpm test:e2e
```

See `ui/README.md` for the full layout, scripts, and the auth /
codegen model.

## Container quickstart

The fastest way to a working stack is `docker compose up --build` —
the image ships the Next.js UI baked in as static assets, so once
the container is healthy you can hit the chat UI, the admin console,
and the API all on the same origin.

```bash
# 1. Fill in the operator-supplied secrets in .env.  At minimum:
#       MASTER_KEY
#       BOOTSTRAP_ADMIN_USERNAME + BOOTSTRAP_ADMIN_PASSWORD  (first boot only)
#       OPENROUTER_API_KEY (or any other provider key — see .env.example)
cp .env.example .env

# 2. Bring everything up.  The first build downloads ~1GB of deps and
#    takes 5–10 minutes; subsequent builds reuse layer caches.
docker compose up --build

# 3. Open http://localhost:7777/  — chat UI for everyone
#                  /admin/         — admin console (admin role only)
#                  /docs           — OpenAPI / Swagger
#                  /health         — liveness probe
```

What the entrypoint does on first boot (see `docker/entrypoint.sh`):

1. Generates an RS256 JWT keypair under `/app/secrets` if one isn't
   mounted from the host.  Compose mounts `./secrets` so subsequent
   restarts reuse the same keys.
2. Runs `alembic upgrade head` against the compose-internal Postgres
   so the schema is on the latest migration before the app accepts
   traffic.
3. `exec`s `uvicorn` so SIGTERM from `docker stop` flows straight to
   the Python process and the SSE generators get a chance to release
   their MCP cache leases.

Set `SKIP_MIGRATIONS=1` to bypass step 2 — useful when invoking the
admin CLI in a one-shot container (`docker compose run --rm app
gargantua-admin user list`).

### Image layout

```
/opt/venv          gargantua + every Python dep (pip install . into a venv)
/app/src           source tree (referenced by alembic.ini's script_location)
/app/alembic.ini   migration entry point
/app/ui/out        Next.js static export — served by FastAPI at /
/app/secrets       JWT keys (mount a volume to persist across rebuilds)
```

The image is multi-stage so the runtime layer is ~250MB:

* `ui-builder` (`node:20-bookworm-slim`) — `pnpm install` + `pnpm build`,
  with `NEXT_PUBLIC_API_BASE_URL=""` so the UI uses relative URLs.
* `py-builder` (`python:3.12-slim`) — `pip install .` into `/opt/venv`.
* `runtime` (`python:3.12-slim`) — copies the venv + the static export,
  drops privileges to UID 1001, runs under `tini` for clean signal
  handling.

## Layout

```
src/gargantua/
    main.py            ASGI entry; wires lifespan + MCP cache + AgentOS mount
    settings.py        pydantic-settings shim around .env
    auth/              JWT mint/verify, password hashing, scopes
    db/                SQLAlchemy models + session factories
    crypto/            KEK loader + AES-GCM envelope encrypt/decrypt
    repo/              Plain functions: one module per table, sync + async
    api/
        auth.py        /auth/login, /auth/refresh, /auth/me
        admin.py       /admin/* (users, audit, catalog, servers, children,
                       agents, teams, mcp-cache, agent-templates)
        me.py          /me/agents, /me/teams (non-admin caller's accessible set)
        runs.py        POST /v1/agents/{id}/runs, POST /v1/teams/{id}/runs
        schemas.py     Pydantic in/out models for the whole HTTP surface
    mcp_cache.py       Warm-handle cache: ref-count, idle reaper, version bumps
    mcp_tools.py       ToolsBuilder — turns DB rows into agno.tools.mcp.MCPTools
    registry.py        build_agno_agent / build_agno_team factories
    bootstrap.py       First-boot admin seed
    catalog_seed.py    MCP server type catalog (seeded rows)
    templates.py       Agent template markdown loader
    seeds/agents/      *.md template instructions (api-explorer, db-investigator,
                       logs-explorer, triage-lead)
    cli/
        admin.py       Typer app for KEK + JWT + catalog + rotate-kek
        cli_admin.py   Typer sub-apps for users + audit
tests/                 pytest suite; integration/ holds end-to-end tests
alembic/               Migrations (sync engine only — Alembic doesn't use async)
secrets/               Local-only: jwt_*.pem (gitignored)
ui/                    Next.js 14 + TypeScript admin/chat console (see ui/README.md)
```

## Admin CLI

```bash
# Cryptographic material
gargantua-admin generate-master-key [--raw]   # one-time KEK
gargantua-admin generate-jwt-keys --out-dir ./secrets
gargantua-admin rotate-kek --from-key <b64> --to-key <b64> [--dry-run]

# Catalog
gargantua-admin seed-catalog [--overwrite]    # seeds mcp_server_type rows

# Users
gargantua-admin user create   --username <u> [--role admin|user]
gargantua-admin user list     [--role ...] [--search ...] [--include-inactive]
gargantua-admin user set-role --username <u> --role <admin|user>
gargantua-admin user deactivate --username <u>
gargantua-admin user activate   --username <u>

# Audit
gargantua-admin audit list    [--actor-id ...] [--target-type ...] [--action ...]
```

For procedure-by-procedure operational guidance (rotating the KEK
without downtime, recovering from a lost KEK, diagnosing a stuck warm
handle, etc.), see `RUNBOOK.md`.

## HTTP surface (cheat-sheet)

Mounted under FastAPI root; AgentOS sub-app is mounted at `/v1`.

```
# Auth (open)
POST   /auth/login                       username + password → token pair
POST   /auth/refresh                     refresh_token       → new token pair
GET    /auth/me                          claims              → caller projection

# User self-service (SCOPE_USER)
GET    /me/agents                        list non-archived agents accessible to caller
GET    /me/teams                         list non-archived teams accessible to caller

# Runtime (SCOPE_USER) — these are AgentOS-mounted under /v1
POST   /v1/agents/{agent_id}/runs        run an agent; stream=true → SSE
POST   /v1/teams/{team_id}/runs          run a team; stream=true → SSE

# Admin (SCOPE_ADMIN) — all under /admin
GET    /admin/users
POST   /admin/users
GET    /admin/users/{id}
PATCH  /admin/users/{id}/role
POST   /admin/users/{id}/deactivate
POST   /admin/users/{id}/activate

GET    /admin/audit
GET    /admin/audit/{id}

GET    /admin/mcp-server-types
POST   /admin/mcp-server-types
GET    /admin/mcp-server-types/{id}
PATCH  /admin/mcp-server-types/{id}
POST   /admin/mcp-server-types/{id}/archive
POST   /admin/mcp-server-types/{id}/unarchive

GET    /admin/mcp-servers
POST   /admin/mcp-servers
GET    /admin/mcp-servers/{id}
PATCH  /admin/mcp-servers/{id}
POST   /admin/mcp-servers/{id}/archive
POST   /admin/mcp-servers/{id}/unarchive

GET    /admin/mcp-servers/{id}/child-resources
POST   /admin/mcp-servers/{id}/child-resources
GET    /admin/mcp-servers/{id}/child-resources/{cid}
PATCH  /admin/mcp-servers/{id}/child-resources/{cid}
POST   /admin/mcp-servers/{id}/child-resources/{cid}/enable
POST   /admin/mcp-servers/{id}/child-resources/{cid}/disable

GET    /admin/agents
POST   /admin/agents
GET    /admin/agents/{id}
PATCH  /admin/agents/{id}
POST   /admin/agents/{id}/archive
POST   /admin/agents/{id}/unarchive

GET    /admin/teams                      (CRUD analogous to agents)

GET    /admin/mcp-cache                  warm-handle inspector (lists every
                                         server×child-set variant separately)
POST   /admin/mcp-cache/{server_id}/evict   force-evict every variant of a server

GET    /admin/agent-templates            list bundled markdown templates
GET    /admin/agent-templates/{slug}     one template's full body
```

OpenAPI is auto-published at `/docs` (Swagger) and `/redoc`; that's the
authoritative reference for request/response shapes.

## Tests

```bash
.venv/bin/python -m pytest                                    # full suite
.venv/bin/python -m pytest tests/test_mcp_cache.py           # one file
.venv/bin/python -m pytest tests/integration/                # integration only
.venv/bin/python -m pytest -k "child_resource"               # by name
```

The integration tests use a real Postgres via the
`migrated_engine` fixture (spins up a per-session test DB and runs
Alembic against it).  Set `TEST_DATABASE_URL` if you want to point at
a non-default cluster.

