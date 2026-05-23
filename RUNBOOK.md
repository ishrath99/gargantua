# Runbook

Day-2 operational procedures for `gargantua`.  Each section is a
self-contained recipe: pre-checks, the commands to run, and a recovery
path if something goes wrong.

For "what is this and how do I get it running", see `README.md`.

---

## Table of contents

1. [First boot of a fresh environment](#1-first-boot-of-a-fresh-environment)
2. [Rotating the master key (KEK)](#2-rotating-the-master-key-kek)
3. [Rotating the JWT signing keypair](#3-rotating-the-jwt-signing-keypair)
4. [Adding a new MCP server type to the catalog](#4-adding-a-new-mcp-server-type-to-the-catalog)
5. [Adding a new MCP server instance](#5-adding-a-new-mcp-server-instance)
6. [Adding child resources (swagger docs etc.)](#6-adding-child-resources-swagger-docs-etc)
7. [Creating an agent or team](#7-creating-an-agent-or-team)
8. [Diagnosing a stuck MCP cache entry](#8-diagnosing-a-stuck-mcp-cache-entry)
9. [Reading the audit log](#9-reading-the-audit-log)
10. [Recovering from a lost or compromised KEK](#10-recovering-from-a-lost-or-compromised-kek)
11. [Backup and restore](#11-backup-and-restore)
12. [Promoting / demoting / deactivating users](#12-promoting--demoting--deactivating-users)

---

## 1. First boot of a fresh environment

Pre-checks:

- Postgres reachable at `DATABASE_URL` and `DATABASE_URL_ASYNC`.
- A provider API key for whichever models your agents reference. Set
  the matching env var: `OPENROUTER_API_KEY`, `OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or `GROQ_API_KEY`.
- Write access to `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` directory.

Steps:

```bash
# (a) Generate cryptographic material.  Both commands are one-shot;
#     re-running generate-master-key on an existing deployment will
#     orphan every encrypted row.
gargantua-admin generate-master-key            # paste output into MASTER_KEY
gargantua-admin generate-jwt-keys --out-dir ./secrets

# (b) Configure .env.  Required at minimum:
#       DATABASE_URL, DATABASE_URL_ASYNC, MASTER_KEY,
#       OPENROUTER_API_KEY (or another provider key — see .env.example),
#       JWT_PRIVATE_KEY_PATH, JWT_PUBLIC_KEY_PATH,
#       BOOTSTRAP_ADMIN_USERNAME, BOOTSTRAP_ADMIN_PASSWORD
cp .env.example .env && $EDITOR .env

# (c) Schema.
alembic upgrade head

# (d) Catalog.  Idempotent — re-runs upsert by slug.
gargantua-admin seed-catalog

# (e) Boot.  The lifespan creates the bootstrap admin if (and only if)
#     the users table is empty AND both BOOTSTRAP_ADMIN_* are set.
uvicorn gargantua.main:app --host 0.0.0.0 --port 7777
```

Verify:

```bash
curl -s http://localhost:7777/health | jq .
curl -s -X POST http://localhost:7777/api/auth/login \
    -H 'content-type: application/json' \
    -d "{\"username\":\"$BOOTSTRAP_ADMIN_USERNAME\",\"password\":\"$BOOTSTRAP_ADMIN_PASSWORD\"}" \
    | jq .access_token
```

After the first successful login: **unset** `BOOTSTRAP_ADMIN_PASSWORD`
from `.env` (or rotate it through `/admin/users/{id}/role` + a manual
password reset script).  The bootstrap is a fallback, not a credential.

Recovery: if step (e) fails because the catalog is missing rows or
secrets are unreadable, run with `APP_LOG_LEVEL=debug` and look for the
first `ERROR` in stdout — every startup-fatal path logs its cause
before the lifespan exits.

### Debugging an agent run

When a run fails with an opaque tool error (e.g. `fetch failed`, an
empty assistant reply, or "tool returned an error") the **API**
logger usually doesn't have enough detail to root-cause — the actual
prompt / tool args / tool result lives inside Agno.

Set `AGNO_DEBUG=true` in `.env` and restart the API (or just save the
file; `uvicorn --reload` picks it up).  Every subsequent run builds
its Agent / Team with `debug_mode=True`, which bumps Agno's own
`agno` and `agno-team` loggers to DEBUG and prints the full trace
to stdout: model name, prompt sent, tool calls + args + results,
intermediate reasoning, and any provider-side errors.

Turn it off again before going back to production — the traces
contain prompt content and tool arguments, both of which can carry
sensitive data, and they are noisy at scale.

---

## 2. Rotating the master key (KEK)

Every secret stored in `mcp_server.env_vars` and
`mcp_server_child_resource.headers` is AES-256-GCM encrypted under the
current KEK.  The `kek_id` column on each row carries a fingerprint of
the key that encrypted it, so the app can refuse to decrypt with the
wrong KEK rather than producing garbage.

**Rotation must be transactional.**  If a row was left half-rotated
the app would fail to decrypt it on next read.  The `rotate-kek`
command runs every re-encrypt inside a single transaction — all rows
move, or none do.

Procedure:

```bash
# (a) Generate the new key, keeping both old and new accessible.
NEW=$(gargantua-admin generate-master-key --raw)
OLD="$MASTER_KEY"                          # whatever's currently in .env

# (b) Quiesce writers to the secrets tables.  Easiest is to stop the
#     app while you rotate; if downtime isn't acceptable, pick a
#     window when no admin is editing server configs.

# (c) Dry-run.  Reports row count without writing.
gargantua-admin rotate-kek --from-key "$OLD" --to-key "$NEW" --dry-run

# (d) Apply.  Single transaction; the script prints a per-table summary.
gargantua-admin rotate-kek --from-key "$OLD" --to-key "$NEW"

# (e) Promote.  Update MASTER_KEY in .env / secret store and restart.
sed -i "s|^MASTER_KEY=.*|MASTER_KEY=$NEW|" .env
systemctl restart gargantua     # or your equivalent
```

Exit codes:

| Code | Meaning |
|---:|---|
| 0 | All rows moved (or `--dry-run` succeeded). |
| 2 | Bad input — invalid base64, wrong key length, or `--from-key == --to-key`. |
| 3 | At least one row was encrypted under a KEK that is neither `--from-key` nor `--to-key`.  **Stop and investigate** — a third key shouldn't exist in a single-tenant deployment.  Likely cause: a previous rotation crashed mid-write, or the database was restored from a backup made under yet another KEK. |

Recovery from exit code 3:

1. Identify the rogue `kek_id`(s):
   ```sql
   SELECT id, kek_id FROM mcp_server WHERE kek_id NOT IN ('<old_fp>', '<new_fp>');
   SELECT id, kek_id FROM mcp_server_child_resource WHERE kek_id NOT IN ('<old_fp>', '<new_fp>');
   ```
2. If you can locate the corresponding KEK, run `rotate-kek` with
   `--from-key=<that_one>` `--to-key="$OLD"` *first* to consolidate
   everything onto the current KEK, then resume the planned rotation.
3. If the original KEK is gone, the affected rows are unrecoverable —
   delete them and have an admin re-enter the secrets through `PATCH
   /admin/mcp-servers/{id}`.  This is the same recovery path as
   section 10.

---

## 3. Rotating the JWT signing keypair

The JWT signing keypair is RS256.  The private key signs tokens; the
public key verifies them.  Rotation is more forgiving than KEK
rotation: tokens have a TTL, so a brief window of dual-key validity is
enough to roll over without invalidating sessions.

We don't ship dual-key support out of the box.  The straightforward
procedure (with a short logout window) is:

```bash
# (a) Generate a new pair into a side directory.
gargantua-admin generate-jwt-keys --out-dir ./secrets/jwt-next

# (b) During a maintenance window:
#     1. Replace the live keys.
cp ./secrets/jwt-next/jwt_private.pem ./secrets/jwt_private.pem
cp ./secrets/jwt-next/jwt_public.pem  ./secrets/jwt_public.pem
#     2. Restart the app.
systemctl restart gargantua

# (c) Tell users to re-login.  All previously issued tokens fail
#     verification immediately because the public key changed.
```

If you can't afford a logout window, the lower-effort path is to bump
`JWT_ACCESS_TTL_SECONDS` to a shorter value, wait that long, then
rotate — every active token will have expired naturally, so the
rotation itself doesn't surprise anyone.

If you need true zero-downtime rotation, the code change required is
small: have `verify_token` try both an "active" and a "previous"
public key for a configurable overlap window.  Not done yet because
nothing in production needs it.

---

## 4. Adding a new MCP server type to the catalog

The catalog (`mcp_server_type`) is the *kinds* of MCP servers admins
can spin up — e.g. "stdio python script", "swagger adapter over HTTP".
Each row carries a `mode` (`stdio` / `sse` / `streamable_http`), a
`config_schema` describing required env_vars, and defaults.

Two ways to add one:

**A — bundled seed (preferred for shared types).**  Edit
`src/gargantua/catalog_seed.py` to append a new dict, re-run
`gargantua-admin seed-catalog` (or `seed-catalog --overwrite` to
replace existing rows).  Commit the change so every environment
gets the type by default.

**B — ad-hoc via the API (one-off types).**

```bash
TOKEN=...   # admin token from /auth/login
curl -s -X POST http://localhost:7777/api/admin/mcp-server-types \
    -H "authorization: bearer $TOKEN" \
    -H 'content-type: application/json' \
    -d '{
        "slug": "my-custom-stdio",
        "name": "My Custom Stdio Tool",
        "mode": "stdio",
        "default_command": "uvx",
        "default_args": ["my-tool"],
        "config_schema": [
            {"key": "API_KEY", "required": true, "secret": true}
        ],
        "default_env_vars": {},
        "optional_env_vars": {}
    }'
```

`config_schema` entries with `secret: true` will be masked in
`GET /admin/mcp-servers/{id}` responses (they're still encrypted at
rest, but the API replies with `"***"`).

To deprecate a type without losing existing instances, archive it
(`POST /admin/mcp-server-types/{id}/archive`); the archive flag hides
it from `GET /admin/mcp-server-types` but keeps `mcp_server` rows
pointing at it valid.

---

## 5. Adding a new MCP server instance

A *server* is an instance of a type with concrete secrets and arguments.

```bash
# Find the type_id you want.
curl -s -H "authorization: bearer $TOKEN" \
    http://localhost:7777/api/admin/mcp-server-types | jq '.items[] | {id, slug}'

# Create the instance.  env_vars are encrypted at rest under the KEK.
curl -s -X POST http://localhost:7777/api/admin/mcp-servers \
    -H "authorization: bearer $TOKEN" \
    -H 'content-type: application/json' \
    -d '{
        "type_id": "<uuid>",
        "name": "production-petstore",
        "env_tag": "prd",
        "command": null,
        "args": [],
        "env_vars": {
            "API_KEY": "super-secret",
            "BASE_URL": "https://api.petstore.example"
        }
    }'
```

`env_tag` is a free-form label (`dev` / `prd` / etc.) used for
organisation; it doesn't gate access.  Editing `env_vars` on an
existing server bumps its cache version, so the next run re-builds
the warm handle (no stale credentials).

---

## 6. Adding child resources (swagger docs etc.)

A *child resource* is a per-server resource (typically a swagger or
OpenAPI doc) that some agents reference to filter the tool surface of
the underlying MCP server.

The cache key is `(server_id, sorted_child_resource_ids)`, so two
agents using the same parent server with **different** child sets each
get their own warm handle and tool surface.

```bash
# List existing children for a server.
curl -s -H "authorization: bearer $TOKEN" \
    http://localhost:7777/api/admin/mcp-servers/<sid>/child-resources

# Create a swagger child.  ``headers`` is encrypted alongside the
# parent's env_vars.
curl -s -X POST http://localhost:7777/api/admin/mcp-servers/<sid>/child-resources \
    -H "authorization: bearer $TOKEN" \
    -H 'content-type: application/json' \
    -d '{
        "type": "swagger",
        "name": "orders-api",
        "url": "https://api.example/orders/openapi.json",
        "headers": {"Authorization": "Bearer ..."}
    }'

# Disable a child without deleting it — agents that reference it
# will skip it at run time (the route silently drops disabled
# children with a warning log).
curl -s -X POST -H "authorization: bearer $TOKEN" \
    http://localhost:7777/api/admin/mcp-servers/<sid>/child-resources/<cid>/disable
```

At run time, the MCP server receives the enabled child set as either:

- **stdio**: `CS_AGENTS_CHILD_RESOURCES` env var, a JSON array.
- **HTTP** (`sse` / `streamable_http`): `X-CS-Child-Resources` header,
  same JSON shape.

Each entry: `{id, type, name, url, headers}`.  The server is expected
to expose only the tools backed by that subset.

---

## 7. Creating an agent or team

Agents and teams are config; both carry `mcp_server_ids`,
`child_resource_ids`, model selection, and instructions.

```bash
# Optional: list bundled templates for inspiration.
curl -s -H "authorization: bearer $TOKEN" \
    http://localhost:7777/api/admin/agent-templates

# Create an agent.
curl -s -X POST http://localhost:7777/api/admin/agents \
    -H "authorization: bearer $TOKEN" \
    -H 'content-type: application/json' \
    -d '{
        "name": "Triage Lead",
        "model": "openai:gpt-4o",
        "instructions": "...",
        "description": "...",
        "mcp_server_ids": ["<sid1>", "<sid2>"],
        "child_resource_ids": ["<cid1>"]
    }'
```

`child_resource_ids` may only reference children whose
`parent_mcp_server_id` is in `mcp_server_ids`; the repo enforces this
at create + update time.

Teams (`POST /admin/teams`) reference a list of `member_agent_ids` and
a `mode` (`route` / `coordinate` / `collaborate`).  The same MCP +
child-resource references apply: a team's effective tool surface is
the union of its members' resolved keys, but each member sees only
its own slice at run time.

To run:

```bash
# Streaming run (SSE).
curl -N -X POST http://localhost:7777/api/v1/agents/<agent_id>/runs \
    -H "authorization: bearer $TOKEN" \
    -H 'content-type: application/json' \
    -d '{"input": "what failed in the last deploy?", "stream": true}'

# Non-streaming.
curl -s -X POST http://localhost:7777/api/v1/agents/<agent_id>/runs \
    -H "authorization: bearer $TOKEN" \
    -H 'content-type: application/json' \
    -d '{"input": "..."}'
```

---

## 8. Diagnosing a stuck MCP cache entry

Symptoms:

- An agent run hangs or 503s with `MCP server <id> is not available`.
- An admin edited a server's `env_vars` but the run still uses the
  old credentials.
- `GET /admin/mcp-cache` shows `ref_count > 0` on an entry whose
  agents are no longer running.

Diagnostic flow:

```bash
# (a) Snapshot.  Each entry is one (server_id, child_resource_ids) variant.
curl -s -H "authorization: bearer $TOKEN" \
    http://localhost:7777/api/admin/mcp-cache | jq .

# Fields per entry:
#   server_id          which MCP server
#   child_resource_ids which child set this entry is bound to
#   version            cache's view of the row revision; rises on every PATCH
#   ref_count          live leases — 0 means idle, >0 means at least one run
#                      currently holds it.  Stuck at >0 with no active runs
#                      means a release path failed.
#   last_used          last activity; helps spot leaks
#   is_orphan          true = this is an old version kept alive only because
#                      a still-running call holds it; the new version is
#                      already serving fresh calls.

# (b) Force eviction.  Closes the warm handle now, even if ref_count > 0.
#     This is intentionally destructive — any in-flight call holding a
#     lease will see its tool handle disappear and likely 5xx; only
#     evict an entry whose holders are genuinely stuck.
curl -s -X POST -H "authorization: bearer $TOKEN" \
    http://localhost:7777/api/admin/mcp-cache/<server_id>/evict
```

Evicting a server clears **every** child-set variant for that
`server_id`.  Next call rebuilds them lazily on demand.

If the same entry keeps coming back stuck:

- Check `args` / `command` / `env_vars` on the server row — bad
  credentials may be causing the subprocess (stdio) or HTTP client
  (sse / streamable_http) to hang at startup.
- Look at the app logs for `mcp-cache: lease.release for key=… failed`
  — that exception class is the only reason a `ref_count` can leak.
- Look at the agent's `child_resource_ids` for stale references; the
  route drops unknown children silently (with a warning log) but
  disabled children produce a `ServerNotFound` from the row fetcher.

---

## 9. Reading the audit log

Every admin write goes through `audit_log_repo.record` and lands in
the `audit_log` table.  Read via:

```bash
# Recent events, paginated.
curl -s -H "authorization: bearer $TOKEN" \
    "http://localhost:7777/api/admin/audit?page=1&page_size=50" | jq .

# Filter.
curl -s -H "authorization: bearer $TOKEN" \
    "http://localhost:7777/api/admin/audit?actor_id=<uid>&action=update_server" | jq .

# Same surface from the CLI (no need to mint a token).
gargantua-admin audit list --actor-id <uid>
gargantua-admin audit list --target-type mcp_server
gargantua-admin audit list --action create_agent
```

Each entry carries `actor_id`, `action`, `target_type`, `target_id`,
`before` and `after` JSON snapshots (with secret values masked), and a
timestamp.  This is the canonical "who changed what" log.

---

## 10. Recovering from a lost or compromised KEK

This is the worst-case scenario.  Every secret stored in `mcp_server`
and `mcp_server_child_resource` is unrecoverable: there is **no
backdoor**, by design.

Procedure:

1. **Stop the app.**  A running instance with the wrong KEK will fail
   noisily on every decrypt, but stopping it prevents any further
   admin writes that might confuse the recovery.

2. **Provision a new KEK.**
   ```bash
   NEW=$(gargantua-admin generate-master-key --raw)
   ```

3. **Reset the encrypted columns.**  Run this as a one-off psql
   session.  We're acknowledging the secrets are gone and clearing
   them so the new KEK can take over.
   ```sql
   UPDATE mcp_server
       SET env_vars = NULL, env_var_iv = NULL, env_var_kek_id = NULL;
   UPDATE mcp_server_child_resource
       SET headers = NULL, headers_iv = NULL, headers_kek_id = NULL;
   ```

4. **Promote the new KEK.**  Update `MASTER_KEY` in `.env` / secret
   store to the value from step 2.  Restart the app.

5. **Re-enter each secret.**  For every MCP server and child resource,
   an admin re-issues `PATCH /admin/mcp-servers/{id}` (or
   `…/child-resources/{cid}`) with the original secret values.  Each
   PATCH bumps the cache version, so the next run rebuilds with the
   new credentials.  The audit log will show the recovery as a normal
   sequence of admin writes.

If the KEK was compromised rather than lost, you have the old key —
use `rotate-kek` instead (section 2) and skip this whole procedure.

---

## 11. Backup and restore

What to back up:

- **Postgres**: all tables, including `mcp_server` and
  `mcp_server_child_resource` with their encrypted columns.  A normal
  `pg_dump` is sufficient.
- **`MASTER_KEY`**: stored *separately* from the database backup.
  Restoring the DB without the matching KEK is equivalent to losing
  the KEK (section 10).
- **JWT keypair** (`secrets/jwt_*.pem`): less critical — a restore can
  rotate to a new pair (section 3) at the cost of forcing every user
  to re-login.
- **`.env`**: nice to have for `DATABASE_URL`, model names, TTLs.

```bash
# Snapshot.
pg_dump -Fc -d gargantua > gargantua-$(date +%Y%m%d).dump
cp .env             backup/.env.$(date +%Y%m%d)
cp secrets/*.pem    backup/

# Restore.
createdb gargantua
pg_restore -d gargantua gargantua-YYYYMMDD.dump
cp backup/.env.YYYYMMDD .env
cp backup/jwt_*.pem secrets/
alembic upgrade head        # in case the dump was taken from an older schema
systemctl start gargantua
```

The integration tests are useful as a post-restore smoke check —
running `pytest tests/integration/test_lifecycle.py` against a
freshly-restored DB will at minimum confirm the schema is intact and
auth works end-to-end.

---

## 12. Promoting / demoting / deactivating users

Two equivalent surfaces — CLI and HTTP.  CLI is preferred for
break-glass changes because it doesn't require a token (it talks to
the DB directly with the app's settings).

```bash
# Promote a user to admin.
gargantua-admin user set-role --username alice --role admin

# Demote.
gargantua-admin user set-role --username alice --role user

# Deactivate (cannot log in; existing tokens still valid until they
# expire — rotate JWT keypair if you need immediate eviction).
gargantua-admin user deactivate --username alice

# Reactivate.
gargantua-admin user activate --username alice

# Create.
gargantua-admin user create --username bob --role user
# (the command prompts for password unless --password is supplied)
```

HTTP equivalents are under `/admin/users` — see the cheat-sheet in
`README.md`.  Every change writes an `audit_log` row keyed by the
admin's user_id, so attribution is preserved either way.

If you accidentally locked the *only* admin out (e.g. deactivated
yourself before promoting a successor):

1. Stop the app.
2. With the DB directly: `UPDATE users SET is_active = true, role = 'admin' WHERE username = '<you>';`
3. Restart and log in.

The bootstrap-admin path won't help here because it only fires when
the `users` table is empty.
