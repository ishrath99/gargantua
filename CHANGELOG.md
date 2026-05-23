# Changelog

All notable changes to Gargantua are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Node 20 and `uv`/`uvx` in the runtime image** so the seeded
  `postgres-mcp` catalog entry and any community MCP server launched
  via `npx ...` (e.g. `@modelcontextprotocol/server-sequential-thinking`)
  work out of the box. Image grows from ~250 MB to ~340 MB.

### Changed

- **All JSON API routes moved under `/api/*`** to eliminate route
  collisions with the bundled UI. Auth, admin, me, and runtime endpoints
  are now at `/api/auth/*`, `/api/admin/*`, `/api/me/*`, `/api/v1/*`.
  `/health` stays at the root for load-balancer probes. The UI, all
  tests, the README and the RUNBOOK are updated to match; external
  callers of the old paths must rewrite them.

## [0.1.0] — 2026-05-22

Initial public release.

### Added

- **DB-first agent and team CRUD** with archive / unarchive, per-user
  access scoping, and a bundled set of Markdown agent-instruction
  templates (`api-explorer`, `db-investigator`, `logs-explorer`,
  `triage-lead`).
- **MCP server type catalog** + per-instance child-resource scoping
  (swagger docs, etc.) with enable / disable lifecycle.
- **Warm MCP handle cache** keyed by `(server_id, sorted_child_resource_ids)`
  with per-key locks, ref-counting, idle reaper, and version-bump
  invalidation on row change.
- **AES-256-GCM envelope encryption** for MCP secrets at rest under a
  single KEK, with a `rotate-kek` CLI command and a documented
  zero-downtime rotation procedure.
- **RS256 JWT auth** minted by the app and verified by Agno's
  `AgentOS(authorization=True)`; access + refresh token pair with
  configurable TTLs.
- **RBAC** (admin / user scopes) and an **audit log** for every
  state-changing admin action.
- **Streaming agent and team runs** via Server-Sent Events under
  `POST /v1/agents/{id}/runs` and `POST /v1/teams/{id}/runs`.
- **Bootstrap admin** on first boot when the users table is empty and
  `BOOTSTRAP_ADMIN_*` env vars are set.
- **Next.js 14 + TypeScript UI** (admin console + chat) built as a
  fully static export and served from the same FastAPI origin as the
  API in production.
- **Multi-stage Dockerfile** producing a ~250 MB runtime image, and a
  `docker-compose.yml` stack (Postgres + app) that boots end-to-end
  with `docker compose up --build`.
- **Admin CLI** (`gargantua-admin`) covering KEK + JWT material
  generation, KEK rotation, catalog seeding, user lifecycle, and audit
  log inspection.
- **Day-2 runbook** (`RUNBOOK.md`) covering KEK rotation, JWT rotation,
  stuck-cache recovery, lost-KEK recovery, backup and restore, and
  user promotion / demotion.
- **47 unit + 31 integration tests** running against a real Postgres
  via the `migrated_engine` fixture.

### Security

- Tool secrets (MCP env vars and child resource headers) are encrypted
  with AES-256-GCM and tagged with a short KEK fingerprint so rows
  know which key can decrypt them.
- The JWT signing keypair lives outside the database and is rotated
  per `RUNBOOK.md` §3.
- UI tokens live in `localStorage`; this trade-off is documented in
  `SECURITY.md` and mitigated by a strict CSP and the absence of any
  `dangerouslySetInnerHTML` usage.

[Unreleased]: https://github.com/ishrath99/gargantua/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ishrath99/gargantua/releases/tag/v0.1.0
