# Security policy

## Supported versions

Gargantua is pre-1.0. Only the latest tagged release on `main` receives
security fixes. Patches for older tags are not backported.

## Reporting a vulnerability

**Do not open a public issue for security bugs.**

Two ways to report, in order of preference:

1. **GitHub Security Advisory** — open a draft at
   <https://github.com/ishrath99/gargantua/security/advisories/new>.
2. **Email** — `ishrathahamed0@gmail.com` with `[gargantua security]` in
   the subject.

In either case, include:

- A description of the issue and its impact.
- A minimal reproduction, if possible.
- The commit SHA or tag you tested against.
- Your suggested fix or mitigation, if you have one.

You'll get an acknowledgement within 72 hours and a fix or mitigation
plan within 14 days. Coordinated disclosure preferred; a fix will be
released before public details are published.

## Threat model

Gargantua is built for **trusted, authenticated internal users**
operating against **operator-curated** MCP servers and agents. It is
**not** currently hardened for:

- **Hostile end-users**. A holder of `SCOPE_USER` who can call
  `POST /v1/agents/{id}/runs` can invoke any tool the agent is wired
  to. Curate the agent and child resource set accordingly.
- **Multi-tenant isolation**. There is one audit log and one KEK per
  deployment. Tenants are not cryptographically separated.
- **Adversarial MCP servers**. MCP server processes run with the
  privileges of the app container. Only configure MCP server URLs and
  binaries you trust.
- **Prompt injection**. Standard LLM agent caveats apply — user input
  can influence tool selection. Sensitive tools (mutations, deletes)
  should be gated by additional authorization at the MCP server.

## Secrets handling

- **`MASTER_KEY` (KEK)** is the root of trust for at-rest secrets
  (MCP env vars + child resource headers, encrypted AES-256-GCM).
  Store it outside the database and back it up separately. Rotate per
  `RUNBOOK.md` §2.
- **JWT signing keys** live on disk at `JWT_PRIVATE_KEY_PATH`. Rotate
  per `RUNBOOK.md` §3.
- **UI tokens** are stored in `localStorage`. This is an accepted
  XSS trade-off; the app sets a strict CSP and never renders
  user-supplied HTML via `dangerouslySetInnerHTML`. If your threat
  model requires httpOnly cookies, the only file that needs to
  change is `ui/lib/auth/storage.ts`.
- **Bootstrap admin credentials** should be unset from `.env` after
  first boot. They are a fallback, not a long-lived credential.

## Known limitations (not vulnerabilities, by design — open an issue
if you disagree)

- No CSRF protection on the JSON API (Bearer-only auth, no cookies).
- No application-layer rate limiting. Put a reverse proxy in front in
  production.
- The bundled UI is shipped as a static export; there is no
  server-side render trust boundary inside it.
- Provider API keys (`OPENROUTER_API_KEY`, etc.) live in env vars
  by design — Gargantua does not encrypt them at rest. Use a secret
  manager (Vault, AWS Secrets Manager, Kubernetes secrets) for prod
  deployments.
