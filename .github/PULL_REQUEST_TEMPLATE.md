<!--
Thanks for the PR. Keep this template; reviewers use it as the change log.
One logical change per PR — refactor PRs separate from feature PRs.
-->

## What

<!-- One-paragraph summary of the change. -->

## Why

<!-- Link the issue. If there's no issue, explain the motivation. -->

## How

<!-- Implementation notes. Anything reviewers should pay extra attention to. -->

## Checklist

- [ ] Tests added / updated (unit for pure logic, integration for new HTTP routes)
- [ ] `ruff check . && ruff format --check . && mypy src` passes locally
- [ ] `cd ui && pnpm lint && pnpm typecheck && pnpm test` passes (if UI touched)
- [ ] `CHANGELOG.md` updated under `## [Unreleased]`
- [ ] `RUNBOOK.md` updated if operator-visible behaviour changed
- [ ] No secrets, tokens, or API keys in the diff or attached logs
