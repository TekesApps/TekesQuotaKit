# TekesQuotaKit

[![CI](https://github.com/TekesApps/TekesQuotaKit/actions/workflows/ci.yml/badge.svg)](https://github.com/TekesApps/TekesQuotaKit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

TekesQuotaKit is a small FastAPI + SQLAlchemy service that acts as a shared entitlement and quota authority for other backends. Your trusted servers ask it whether a user (a "subject") may use a Service, and it atomically checks the user's Level, enforces the Limit for the current period, charges the Quota, and records every change in an immutable ledger. It is for teams that run several backends (or several features in one backend) that must draw from the same per-user allowance, and that want one auditable place for that rule instead of a counter column in each application.

> **Status: alpha (pre-1.0).** The HTTP API and schema may change between minor versions. See [Known limitations](#known-limitations).

## Core concepts

| Concept | Meaning |
| --- | --- |
| **Service** | Something a backend performs for a user, identified by `service_code` (and an integer `service_id`). `atomic` or `composite`; `instant` or `durable`. |
| **Quota** | A countable allowance with a `unit_code` and a `metering_mode` of `per_use` or `reported_usage`. A Service charges one Quota. |
| **Level** | A tier assigned to a subject, such as `regular` or `premium`. |
| **Limit** | For one Level and one Quota: `finite` with a `limit_value`, or `unlimited`, over a period of `day`, `month`, or `level_term`. |
| **Assignment** | Links a subject (positive integer `subject_id`, scoped by tenant) to a Level, with optional `effective_at`, `expires_at`, and `renew_term`. |
| **Client credential** | A long random key bound to one tenant, one Service, and one role (`issuer`, `provider`, or `consumer`). Only its SHA-256 hash is stored. |
| **Token** | Created per subject and request by `redeem` or `token`; used for settlement, use, stop, refund, and status. |
| **Ledger** | Append-only record of every `consume` and `refund` event. |

A `consumer` client may call both issuer and provider endpoints. Use separate `issuer` and `provider` clients when admission and execution happen on different backends.

## Redemption modes

All calls below are sent by a trusted backend with `Authorization: Bearer <client key>`.

**Instant** (per-use Quota, atomic Service). One call admits, charges one unit, and completes:

```text
POST /v1/redeem  {"subject_id":42,"service_code":"report_export","request_key":"export-123"}
```

**Durable** (per-use Quota, `charge_units` > 0). `redeem` charges once and opens a session; each controlled attempt calls `use`; `stop` closes it. A composite parent grants its child Services, configured as rows in `tq_service_members`. `duration_seconds` (60 to 86400) is optional and falls back to the Service's `session_ttl_seconds`; with neither, the session stays open until `stop`.

```text
POST /v1/redeem  {"subject_id":42,"service_code":"session_bundle","request_key":"session-501","duration_seconds":3600}
POST /v1/use     {"token":"tq_...","child_service_code":"child_a","request_key":"session-501-a-1"}   + X-Subject-ID: 42
POST /v1/stop    {"token":"tq_..."}
```

`use` never charges again. It checks that the child is in the snapshot captured at `redeem`, that the session is open and unexpired, and that the child's optional `max_uses` is not exceeded. Stop and expiry never refund. An explicit `POST /v1/token/refund` is allowed only when no child attempt was recorded.

**Reported usage** (`reported_usage` Quota). `token` admits work without charging; the provider settles actual usage afterwards, including partial usage on failure:

```text
POST /v1/token         {"subject_id":42,"service_id":8,"request_key":"chat-456"}
POST /v1/token/settle  {"token":"tq_...","consumed_units":750}
```

**Idempotency with `request_key`.** A stable key per business operation lets a caller retry safely:

- Instant: a repeated key returns HTTP 409 `already_redeemed` with the original `token`, and nothing is charged again.
- Reported usage: a repeated key returns the original token with `idempotent: true`.
- Durable: `request_key` is required. A repeat returns the same token with `idempotent: true`. Each `use` also needs its own stable key.

A token returned again never authorizes repeating the physical operation. A `settled` status confirms the quota transaction, not the business side effect.

## Quick start

Requirements: Python >= 3.12, [uv](https://docs.astral.sh/uv/), and MySQL for deployment (SQLite works for tests).

```bash
uv sync --all-groups
cp .env.example .env
uv run tekes-quota-kit generate-secrets   # paste both values into .env
# Set TEKES_QUOTA_DATABASE_URL in .env, then export the variables:
set -a; source .env; set +a
uv run tekes-quota-kit init-schema
uv run tekes-quota-kit admin-user add --username admin   # prompts for a password
uv run tekes-quota-kit serve
```

Open `http://127.0.0.1:9460/admin` and sign in for the web admin, and `http://127.0.0.1:9460/docs` for the interactive OpenAPI page.

TekesQuotaKit creates only `tq_` tables, so it can share your application's existing schema; a separate database is optional. It never creates, alters, or owns your application's user table: `subject_id` is your existing numeric user ID. `init-schema` creates missing `tq_` tables only. It does not create the database, alter existing tables, or seed data. For a reviewed fresh MySQL install, [migrations/create_tables_mysql.sql](migrations/create_tables_mysql.sql) contains the explicit DDL. Installs created before 0.2.0 must apply [migrations/add_composite_services_mysql.sql](migrations/add_composite_services_mysql.sql) once, after a backup; `init-schema` will not perform that upgrade. Upgrading a 0.2.x install to web admin accounts only adds tables: run `init-schema` or apply [migrations/add_admin_accounts_mysql.sql](migrations/add_admin_accounts_mysql.sql). Never run both fresh creation methods on the same schema.

## First integration

Each step below can be done in the web admin. Scripts call the same endpoints with `Authorization: Bearer $TEKES_QUOTA_ADMIN_KEY`. All admin configuration endpoints are idempotent `PUT`s.

1. Create a Quota:
   `PUT /v1/admin/tenants/demo-tenant/quotas/exports` `{"unit_code":"use","metering_mode":"per_use"}`
2. Create a Level:
   `PUT /v1/admin/tenants/demo-tenant/levels/regular` (no body)
3. Set its Limit:
   `PUT /v1/admin/tenants/demo-tenant/levels/regular/limits/exports` `{"limit_mode":"finite","limit_value":10,"period_kind":"month","timezone":"UTC"}`
4. Create a Service (the response contains its `service_id`):
   `PUT /v1/admin/tenants/demo-tenant/services/report_export` `{"quota_code":"exports","service_kind":"atomic","redemption_mode":"instant"}`
5. Assign a subject, using your application's user ID:
   `PUT /v1/admin/tenants/demo-tenant/subjects/42/level` `{"level_code":"regular"}`
6. Create a client credential, either in the web admin (recommended, it generates the key and a Markdown contract) or directly:
   `PUT /v1/admin/clients/export-backend` `{"client_id":"export-backend","key":"<at least 32 random chars>","tenant_id":"demo-tenant","service_code":"report_export","role":"consumer"}`
7. From your backend, redeem:

   ```bash
   curl -X POST http://127.0.0.1:9460/v1/redeem \
     -H "Authorization: Bearer $CLIENT_KEY" -H "Content-Type: application/json" \
     -d '{"subject_id":42,"service_code":"report_export","request_key":"export-123"}'
   ```

   On success, perform the work. On rejection (for example HTTP 409 `quota_exhausted`), do not perform it.

`GET /v1/quota` (with `X-Subject-ID`) is for display only and never authorizes execution. `GET /v1/tokens/unsettled` lists reported-usage tokens still awaiting settlement; the provider must keep its token until settlement succeeds. If you are replacing a legacy usage counter, switch it off at the same moment you start charging through TekesQuotaKit to avoid double charging. See [docs/api.md](docs/api.md) for every endpoint and error code.

## Security model

- `TEKES_QUOTA_ADMIN_KEY`, `TEKES_QUOTA_TOKEN_SECRET`, and every client key must be at least 32 characters. Use `generate-secrets`.
- Client keys are server credentials. Never ship them, or the admin key, in a browser, mobile app, or miniapp bundle.
- Web admin accounts use PBKDF2-SHA256 password hashes (600,000 rounds), an HttpOnly `SameSite=Strict` session cookie scoped to the admin API path and valid for 12 hours, and a lock of 15 minutes after 5 failed sign-ins for a username. Cookie-authenticated writes must send `X-Admin-Request: 1`, which a cross-site page cannot do.
- The server binds to `127.0.0.1:9460` by default. Expose it only through a TLS reverse proxy on a trusted network.
- Keep `TEKES_QUOTA_TOKEN_SECRET` stable. Tokens are derived from it, so changing it breaks idempotent retries of every earlier `request_key`. There is no rotation procedure.
- `use`, `refund`, `status`, and `quota` take the subject from a trusted `X-Subject-ID` header that your backend sets from its own authentication. Settlement and `stop` resolve the subject from the token.

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities and a deployment checklist.

## Web admin

`GET /admin` serves a React console (source in [admin-web/](admin-web/)). It opens on a sign-in page; operators sign in with a web admin account created by `tekes-quota-kit admin-user add`. After sign-in, a collapsible sidebar groups the pages:

| Group | Pages |
| --- | --- |
| Rules | Services, composite members, Quotas, Levels, limit rules |
| Subjects and access | Subject levels, client credentials |
| Runtime data (read-only) | Usage, tokens, session items, ledger |

On first sign-in the console asks for the business system: a display name, such as 数康智医, and a code, such as `shukang-zhiyi`, which becomes the tenant ID for every rule and client. After that the console works in that business system and shows its name in the header; a switcher appears only if several are registered. Edits go through the same validated admin API that scripts use. Credential and token hashes are never shown.

Under **Client credentials**, choose a Client ID, Service, role, and the API base URL the client will use. The console generates a random key, stores only its hash, and downloads a Markdown integration contract containing the plaintext key, binding, Service settings, child mappings, and the relevant API sequence. This download is the only time the plaintext key is available. **Rotate** on an existing client immediately invalidates the old key. Keep the downloaded file out of Git and out of client-side code.

The console works at `/admin` or behind a reverse proxy path prefix such as `/user-quota/admin`; see [docs/deployment.md](docs/deployment.md).

## CLI

| Command | Purpose |
| --- | --- |
| `tekes-quota-kit serve` | Run the API and web admin on `TEKES_QUOTA_HOST`:`TEKES_QUOTA_PORT`. |
| `tekes-quota-kit init-schema` | Create missing `tq_` tables in `TEKES_QUOTA_DATABASE_URL`. |
| `tekes-quota-kit expire-sessions [--limit N]` | Persist the `expired` state for durable sessions past their expiry (default limit 500 per run). Intended for cron. |
| `tekes-quota-kit generate-secrets` | Print a new admin key and token secret. Needs no database. |
| `tekes-quota-kit admin-user add\|passwd --username NAME [--password-stdin]` | Create a web admin account or change its password. Prompts twice unless `--password-stdin` reads one line. Passwords need 12 or more characters. A password change signs out the account's sessions. |
| `tekes-quota-kit admin-user disable\|enable --username NAME` | Disable an account and sign out its sessions, or re-enable it. |
| `tekes-quota-kit admin-user list` | List accounts, status, and last sign-in. |

Commands other than `generate-secrets` read `TEKES_QUOTA_DATABASE_URL` and `TEKES_QUOTA_TOKEN_SECRET` from the environment; `serve` also needs `TEKES_QUOTA_ADMIN_KEY`.

## Known limitations

- Alpha, pre-1.0. The API and schema may change.
- Units are integers. Periods are `day`, `month`, or `level_term` only.
- Reported-usage calls can exceed a finite Limit, because admission checks current usage and concurrent settlements land later. Callers must bound concurrency and maximum per-call usage.
- One parent Service per client credential.
- Versioned Service-to-Quota mappings and a shared grant spanning separately deployed providers are not implemented.
- Business success events, identity mapping, and migration from a legacy counter remain the integrating application's responsibility.
- `/v1/begin` and `/v1/close` are legacy aliases for durable `redeem` and `stop`.

## Development

```bash
uv run pytest
uv run ruff check .
```

The web admin lives in `admin-web/`. Its build output is committed under `src/tekes_quota_kit/admin_assets/` so the Python package needs no Node at install time:

```bash
cd admin-web && npm ci && npm run build
```

For live editing, run `npm run dev` there; it proxies `/v1` to a Kit on `127.0.0.1:9460`. See [CONTRIBUTING.md](CONTRIBUTING.md) for the optional MySQL integration tests and pull request guidelines.

## Documentation

- [docs/api.md](docs/api.md): full HTTP API reference
- [docs/deployment.md](docs/deployment.md): production deployment
- [docs/zh/quota-model.md](docs/zh/quota-model.md): quota model and table relationships (in Chinese)
- [docs/zh/composite-measurement-example.md](docs/zh/composite-measurement-example.md): composite Service worked example (in Chinese)
- [CHANGELOG.md](CHANGELOG.md), [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md)

## License

[MIT](LICENSE)
