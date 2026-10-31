# Default-plan v1 candidate verification — 2026-10-31

Scope: TekesQuotaKit candidate `dbf5c3579a622285fc4a70a624641375d890e9fe`,
plus the following UI-only role-label correction and test update. No production
deployment, migration, estate settings, credential provisioning or user gifts.
Package metadata remains 0.16.2; implementation identity is the PR commit.

## Local checks

- `.venv/bin/ruff check .` and `git diff --check`: passed.
- Full `.venv/bin/python -m pytest -q` with `TEKES_QUOTA_TEST_DATABASE_URL` selecting
  the task-owned `tekes_quota_test_defaults` schema: **174 passed, no skip**.
  MySQL 8.0.46, socket-only dedicated datadir; no application database accessed.
  The existing Starlette/httpx deprecation warning remains.
- `npm run build` in `admin-web`: passed. Final generated bundle `index-ze2fwKQr.js`.
- Full `npm run test:e2e` in `admin-web`: **19 passed** after correction.
  Playwright `.last-run.json` status passed with an empty failedTests list.
  The new plan flow also independently passed; credential guide test passed after
  quoting the stored tenant subject definition.

## Preserved failures and their resolution

- Initial local package version metadata was 0.13.3 after the main baseline advanced
  to 0.16.2; the version assertion failed. Reinstalled the local editable package
  without dependency changes, then reran the failed check successfully. See design history.
- Chromium was initially absent; installed the test browser to execute the suite.
- Early new-flow assertions expected English `disabled` where the UI intentionally
  renders Chinese `停用`, and a seed attempted to rewrite an existing immutable Limit
  period. Corrected those test assumptions; no product acceptance rule was weakened.
- First complete browser run: **17 passed, 2 failed**. One existing generic geometry
  test attempted to click a disabled create action on the new page before any formal
  Level existed. It now only opens enabled actions; the dedicated positive plan flow
  seeds a formal Level and exercises creation/edit/addition/enable/disable separately.
  The other failure exposed loss of the existing localized membership credential label;
  restored localized labels for all three tenant-only roles, rebuilt, then all 19 passed.

## Database and real HTTP evidence

- [MySQL migration/fresh DDL](default-plans-mysql-migration-20261031.json):
  old main DDL, paid assignment inserted before migration, additive migration, actual
  initialization, original paid identity/order preserved; JSON/FKs/ascii_bin/DATETIME(6);
  fresh DDL created all 27 tables. This is separate from SQLite portability checks.
- [First direct HTTP run](default-plans-skzy-http-20261031.json): six groups, including
  real-time per-user seven-day expiry. This initial artifact is preserved.
- [Exact candidate direct HTTP run](default-plans-skzy-http-dbf5c35-20261031.json):
  seven groups, adding full prior paid-package state/ID/order/expiry preservation.
- [Actual Quota process restart](default-plans-process-restart-20261031.json):
  stopped/started Uvicorn on the same SQLite file; both original users replayed the
  same initialization IDs, applied_at, package IDs and expiry even with a missing plan.
- [Admin → shared database → fresh Backend workers → Quota](default-plans-skzy-chain-dbf5c35-20261031.json):
  seven groups, real Admin and Quota HTTP; includes retry after a proxy intentionally
  drops the real committed Quota response, pending original-plan preservation after
  estate configuration change, and unbind/rebind without another default initialization.
  This chain uses a disposable SQLite shared business database. It is **not** proof of
  the same chain on MySQL; Quota's MySQL transaction tests/DDL run separately.

The SKZY owner supplied the two final HTTP artifacts from its isolated Backend
worktree's `docs/validation/estate-default-quota-http-20261031.json` and
`estate-default-quota-chain-20261031.json`; the main agent inspected their contents
and the chain harness before preserving these redacted copies. Source commits for
SKZY are finalized by that owner; no SKZY source was changed by this chat.

## Boundaries

Concurrency tests are bounded multi-connection cases, not load/performance certification.
No physical miniapp/device acceptance. Production VIP Level identity/formal status,
the real estate IDs/configuration and actual deployment are unverified. Historical
users are not automatically granted; already initialized subjects replay forever.
No additional campaign semantics or reset API was added.

Temporary HTTP fixture remains localhost-only on port 19463, with private keys outside
the repository, for the peer's remaining checks. The fixture script and restart method
are documented in [the integration guide](../default-plans-integration.md).
