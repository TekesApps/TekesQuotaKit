# Local issue · Enforce individual operator access

Baseline ec3b81a. The [design](../operator-security.md) defines the three role boundaries, password lifecycle, and legacy migration. [Deployment instructions](../deployment.md#web-admin-accounts) cover host provisioning, enrollment, and the readiness check.

The implementation covers the host account CLI, API authentication and authorization, role-specific browser pages, individual script sessions, the SQLite and MySQL schema upgrades, and non-secret audit evidence. The compiled console ships with the Python package. No remote issue or pull request was opened.

## Verification on 2026-11-26

* Python 3.13 with an isolated, socket-only MySQL 8.0.46 instance passed 207 tests, including the optional MySQL suite and SQLite legacy account migration. The test client emitted one upstream Starlette deprecation warning.
* Fresh MySQL DDL and the upgrade from the old three-table admin schema both ran on disposable databases. A synthetic existing auditor was enrolled, the other two role accounts were provisioned, all three signed in, the roster check passed, and the auditor read the operations feed. Separate CLI-only provisioning with private standard input also passed.
* TypeScript type checking and Vite production build passed. All 23 Chromium scenarios passed, including expired-password rotation, the read-only audit view, operator session revocation and existing quota administration workflows.
* A final login-response check handles expiry or concurrent revocation before the cookie is sent. The 29 focused HTTP administration and authentication tests passed after this change. The generated integration-guide wording received an additional administration test run. Ruff, git diff --check, source distribution and wheel builds passed.
* All verification used disposable identities and local data. Deployment, real identity assignment, and production checks require an authorized operator with the three actual account holders. No production credentials were accessed or issued.
