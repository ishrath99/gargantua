# Contributing to Gargantua

Thanks for considering a contribution. Gargantua is small, opinionated,
and pre-1.0, so a quick chat in an issue or
[Discussion](https://github.com/ishrath99/gargantua/discussions) before
a large PR will save us both time.

## Code of conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md).
By participating, you agree to abide by its terms.

## Quick setup

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Postgres + the app (so integration tests have a DB)
docker compose up -d postgres
alembic upgrade head
gargantua-admin seed-catalog

# UI
cd ui
pnpm install --frozen-lockfile
```

You'll need cryptographic material before the app boots:

```bash
gargantua-admin generate-master-key            # paste into MASTER_KEY in .env
mkdir -p secrets
gargantua-admin generate-jwt-keys --out-dir ./secrets
cp .env.example .env && $EDITOR .env
```

See `README.md` for the full quickstart and `RUNBOOK.md` for operator
procedures.

## Tests

```bash
# Fast unit suite (no DB).
.venv/bin/python -m pytest tests/ --ignore=tests/integration

# Integration suite (real Postgres).
.venv/bin/python -m pytest tests/integration/

# Single file or test.
.venv/bin/python -m pytest tests/test_mcp_cache.py
.venv/bin/python -m pytest -k "child_resource"

# UI
cd ui
pnpm test           # vitest
pnpm test:e2e       # playwright (network stubbed)
```

CI runs all of the above on every PR. Don't bypass it.

## Style

```bash
# Backend
ruff check .
ruff format .
mypy src

# UI
cd ui
pnpm lint
pnpm typecheck
pnpm format
```

- **Backend**: strict mypy, ruff for lint + format. Line length 100.
- **UI**: ESLint + Next rules, Prettier, strict TypeScript.
- **Docs**: prefer concrete examples over abstract description. Keep
  `RUNBOOK.md` procedure-shaped (pre-checks → commands → recovery).

## Pull requests

- Branch from `main`. We squash-merge.
- **One logical change per PR.** Refactor PRs go separately from
  feature PRs.
- Update `CHANGELOG.md` under `## [Unreleased]` for any user-visible
  change.
- Update `RUNBOOK.md` if you change an operator procedure.
- Add tests:
  - **Unit tests** for pure logic (`tests/test_*.py`).
  - **Integration tests** for new HTTP routes (`tests/integration/test_admin_*.py`).
- Fill in the PR template checklist honestly.

## Commit messages

`scope: short imperative summary`

Examples:

- `mcp-cache: evict on child enable/disable`
- `auth: tighten refresh-token rotation window`
- `docs: clarify KEK rotation under load`
- `ci: bump postgres service to 16.3`

## What we'll merge fast

- Bug fixes with a regression test.
- Documentation improvements (especially `RUNBOOK.md` recipes).
- Test coverage for currently-untested code paths.
- Performance fixes with a benchmark.

## What we'll discuss before merging

- New top-level domains (e.g. workflows, tenancy).
- Anything that changes the on-disk encryption format.
- Anything that changes the JWT claim shape.
- New first-class dependencies.

## Releasing (maintainers only)

1. Bump `version` in `pyproject.toml` and `ui/package.json`.
2. Move `## [Unreleased]` notes to `## [<version>] — YYYY-MM-DD` in
   `CHANGELOG.md`.
3. Commit: `release: vX.Y.Z`.
4. Tag and push: `git tag vX.Y.Z && git push --tags`.
5. The `release.yml` workflow publishes to PyPI and GHCR and creates
   a GitHub release.
