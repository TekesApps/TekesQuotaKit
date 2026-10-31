# PR #42 pre-merge review and local revalidation

The user authorized review, merge and Quota deployment, followed by SKZY deployment
and integration; the user also explicitly chose agent local revalidation before
continuing. Earlier no-production notes describe the prior authorization boundary.

Review baseline: PR head `13e1445c70fdb884069a21dc7d319d6e9f43142c`, main `d03b7b7`.
The existing GitHub CI passed Python 3.12, Python 3.13 and Admin web build.

One reproducible P2 finding was fixed before merge: create an enabled default plan
from a formal Level, demote the Level to test, then try to disable the plan. The
inline toggle used full PUT, which attempted to republish/validate the Level and
failed, leaving the plan enabled. The admin toggle now uses a status-only PATCH with
`enabled` and expected `revision`, retaining the previous published package snapshot.
No SKZY consumption request or success response changes. Full content saves still
validate formal Levels and copy current limits.

The regression test verifies unauthorized and stale writes fail, the demoted-Level
plan can be disabled, first initialization is blocked while disabled, and status
changes preserve the original frozen quota instead of silently taking edited limits.

Local revalidation of the corrected 0.17.0 candidate:

- Full Python suite with fresh disposable MySQL 8.0.46 schema:
  **175 passed, no skip**, one existing Starlette/httpx deprecation warning.
- Ruff and diff whitespace checks passed.
- Frontend build passed; complete Playwright suite **19/19 passed**.
- No dependency version changes; only local package metadata changed to 0.17.0.

Primary-source review of transaction locking, tenant/subject fact identity,
immutable snapshots, normal/metered/durable usage and original-source refunds found
no other actionable issue in this scope. This is bounded review/test evidence,
not a promise that all possible failures or production performance were tested.

Deployment identity, database backup/migration and post-switch checks are recorded
in the separate production receipt once performed. No production real-user grant
is part of these local checks.
