# TekesQuotaKit

TekesQuotaKit is a Python service for shared Service, Quota, Level, and Limit rules. An `instant` per-use Service is admitted, charged, and completed by one `redeem` call. A `durable` per-use Service is charged by `redeem`, validates each permitted Service attempt with `use`, and remains open until `stop` or its optional expiry. Reported-usage Services retain `issue` followed by `settle(token, consumed_units)`.

The first consumer is `shukang-zhiyi`. Deploy TekesQuotaKit against that application's **existing MySQL database**. It creates only `tq_` tables and stores the application's numeric `resident_users.id` as `subject_id`, scoped by `tenant_id`; it does not create, alter, or own the application's user table. A separate database is not required. All tables have auto increment integer `id` primary keys; business codes and token hashes use unique constraints. SQLAlchemy models also support SQLite for local tests and future deployment.

## Local setup

```bash
uv sync --all-groups
cp .env.example .env
# Set TEKES_QUOTA_DATABASE_URL to an existing MySQL schema and set both secrets.
set -a; source .env; set +a
uv run tekes-quota-kit init-schema
uv run tekes-quota-kit serve
```

`init-schema` creates missing `tq_` tables in the configured database. It does not create a database, alter existing tables, or seed Levels and Limits. For a reviewed fresh MySQL deployment, [migrations/create_tables_mysql.sql](migrations/create_tables_mysql.sql) contains the explicit DDL. An existing installation must instead apply the one-time [composite migration](migrations/add_composite_services_mysql.sql) after a database backup; `init-schema` will not perform that upgrade. Do not run both fresh creation methods on the same schema. The API binds to `127.0.0.1:9460` by default. Caller credentials and the admin credential stay on trusted servers; never put them in a miniapp or browser bundle. Keep `TEKES_QUOTA_TOKEN_SECRET` stable across restarts so retries continue to produce the same token.

## First integration

1. Configure tenant `shukang-zhiyi`, Quotas, Services, Levels, and Limits through the authenticated `/v1/admin` API.
2. Associate a resident with a Level using the resident's existing positive integer ID as `subject_id`.
3. If one backend both initiates and performs the Service, create one `consumer` client. For separate backends, create an `issuer` client and a `provider` client. Each client is bound to one tenant and one Service; use different long random keys for separate clients.
4. For an `instant` `per_use` Service, its trusted backend calls `POST /v1/redeem` with the authenticated resident's integer `subject_id` and either its registered integer `service_id` or stable `service_code`. Success atomically admits one use, deducts one unit, and completes the token. A rejected redemption must stop the Service.
5. For a `reported_usage` Service, the trusted backend calls `POST /v1/token` with the same IDs. Success admits the work and returns a token without deducting usage. After the work, its provider calls `POST /v1/token/settle` with that token and actual `consumed_units`, including partial usage on failure. There is no intermediate redemption call.
6. Instant redemption and reported-usage admission accept an optional stable `request_key`. A repeated instant key returns HTTP 409 `already_redeemed` with the original token; a repeated reported-usage key returns the original token with `idempotent: true`. Durable redemption requires a stable key and returns the same token with `idempotent: true` on retry. No idempotent response authorizes repeating a physical operation.

Example request bodies (sent by trusted backends with a Service credential):

```text
Per use:        POST /v1/redeem         {"subject_id":42,"service_id":7,"request_key":"measurement-123"}
Reported usage: POST /v1/token         {"subject_id":42,"service_id":8,"request_key":"chat-456"}
After usage:    POST /v1/token/settle  {"token":"tq_...","consumed_units":750}
```

`GET /v1/quota` is for display only. It cannot authorize execution. `GET /v1/tokens/unsettled` lists admitted metered uses still awaiting settlement. The provider must retain its token until settlement succeeds and reconcile missing usage. Settlement needs only the token and `consumed_units` in its body plus the provider's server credential; QuotaKit resolves the subject from the token. Refund and status endpoints require the trusted `X-Subject-ID` header containing the resident ID as decimal text.

If a provider loses an admission response, it must retry with the same `request_key` or recover the original business result. The repeated instant call returns the original token for refund or status lookup, while durable redemption returns the existing open or closed token. It must not start a second business operation solely because a token is returned again. A `settled` token status confirms the quota transaction, not the business side effect.

This initial implementation uses integer units and `day`, `month`, or `level_term` periods. Metered calls may exceed a finite Limit when concurrent calls settle later; consumers must bound concurrency and maximum per-call usage. Business-specific success events, identity mapping, and legacy quota migration remain with each consumer. See [用户服务Quota通用模型.md](用户服务Quota通用模型.md) for the current table relationships and [组合服务与测量示例.md](组合服务与测量示例.md) for the full measurement example.

The core covers one parent Service per credential, Level assignment, finite/unlimited Limits, direct per-use redemption, reported-usage admission and settlement, refunds, and an immutable usage ledger. Composite sessions can grant multiple configured child Services without charging the Quota again. Versioned Service-to-Quota mappings and a shared grant spanning separately deployed providers remain design work before broad production rollout. The existing Shukang measurement counter must not be charged alongside this ledger during migration.

## Instant and durable redemption

Configure an instant Service with `service_kind: "atomic"`, `redemption_mode: "instant"`, and its per-use `quota_code`. One `POST /v1/redeem` charges and completes it. Configure a durable Service with `redemption_mode: "durable"`, a per-use `quota_code`, and positive `charge_units`; `service_kind` may be `atomic` or `composite`. Define every parent and child once in `tq_services`, then configure each parent-child relationship through `PUT /v1/admin/tenants/{tenant}/services/{parent}/members/{child}` with `{"max_uses":null}` for unlimited attempts or a positive number for a cap. `tq_service_members` is a mapping table, not another Service definition: the same atomic child may belong to multiple composites whose parent Services charge different Quotas. Each redemption snapshots its parent-specific grants and limits. An atomic durable Service can validate use of its own `service_code`.

```text
POST /v1/redeem {"subject_id":42,"service_code":"measurement_session","request_key":"session-501","duration_seconds":3600}
POST /v1/use    {"token":"tq_...","child_service_code":"blood_pressure","request_key":"session-501-bp-1"}
POST /v1/use    {"token":"tq_...","child_service_code":"blood_pressure","request_key":"session-501-bp-2"}
POST /v1/stop   {"token":"tq_..."}
```

`duration_seconds` is optional on durable `redeem` (60–86400 seconds). When omitted, the Service's optional `session_ttl_seconds` is used; when neither is set, the token remains open until `stop`. With a duration, `use` fails immediately after expiry, and `tekes-quota-kit expire-sessions` persists the expired state. `redeem` returns the authorized Service codes captured at that moment. Each `use` validates the child against this snapshot, checks that the token is open and unexpired, and records an attempt with a stable `request_key`; it never charges the Quota again. A repeated key returns its prior attempt with `idempotent: true`. Distinct keys may use the same child without a count limit when `max_uses` is null, or up to the snapshotted positive limit.

`stop` closes a durable token without charging again; `/v1/token/settle` with just the token is an equivalent durable close. The charge remains `settled`, while `session_status` records `open`, `closed`, or `expired`. Timeout and stop never refund automatically. A whole-session refund requires explicit `/v1/token/refund` and is allowed only when no child attempt was recorded. The trusted backend must call `use` before each controlled attempt so the Kit can enforce membership and this refund rule. `use`, `refund`, and status require the same Service credential and a trusted `X-Subject-ID` header. Older `/v1/begin` and `/v1/close` routes remain as aliases for this durable flow.

Level assignment now accepts optional `effective_at` and `renew_term`. A level change within an active term carries the original term start/end and usage; changing term end requires `renew_term: true`, which starts a new accounting term. `level_term` Limits use those term boundaries. Consumers must make a real membership renewal and a mid-term upgrade distinguishable when calling the admin API. The term and business-side order/entitlement lifecycle still need a Shukang integration design before production cutover.

Existing atomic `/v1/redeem` and metered `/v1/token` flows remain unchanged. Composite configuration and use are server-side only. The old Shukang `resident_entitlements.measure_used` path must be switched off at the same cutover point to avoid double charging.

The alternative measurement layout gives each child Service its own per-use Quota and Level Limit. Each project then calls instant `/v1/redeem` independently, using its stable `service_code` or integer `service_id` and a separate client credential bound to that Service. The [measurement model tests](tests/test_measurement_models.py) exercise both layouts and the configuration switch. Existing durable tokens keep their child snapshots through the switch, so the consumer must route old sessions to `use` until they stop or expire.

## Tests

```bash
uv run pytest
uv run ruff check .
```

The SQLite tests exercise the portable model. The optional MySQL integration test uses a disposable test schema named by `TEKES_QUOTA_TEST_DATABASE_URL`; it never targets the configured application database.

## Web administration and client credentials

Generate deployment secrets once with `uv run tekes-quota-kit generate-secrets`. Store the printed `TEKES_QUOTA_ADMIN_KEY` and `TEKES_QUOTA_TOKEN_SECRET` in a server-side secret manager or protected environment file. Keep the token secret stable: replacing it would prevent existing tokens and idempotent retries from being verified. The command does not need database access and does not change a running deployment.

Start the Kit with `uv run tekes-quota-kit serve`, then open `/admin` on its configured host (localhost by default). Enter the admin key in the page; the page keeps it only in the current browser tab's memory. The dashboard lists all 11 `tq_` tables, scoped by tenant and paginated. It hides credential and token hashes. Configure Quotas, Levels, Limits, Services, parent-child memberships, and subject assignments through validated API calls. Usage, tokens, token items, and the ledger are read-only in the dashboard; consumer operations and refunds retain their dedicated endpoints.

In **Client credentials**, choose a unique Client ID, the preconfigured Service code, role, and the API base URL used by that client. The admin generates a random client key, stores only its SHA-256 hash, and downloads an agent-readable Markdown integration contract. It contains the plaintext client key, exact binding and Service settings, current child mappings, relevant API sequence, and the source of each runtime value. The client agent can read this file to implement its trusted backend integration. This is the one opportunity to save the plaintext key; Kit cannot retrieve it later. Generating a key for an existing Client ID requires the explicit **rotate** option, which immediately invalidates the previous key. Keep the downloaded Markdown out of Git and client-side applications. The admin and token secrets are never included in a client guide.

A client key is a server credential. A per-use `token` is created later by `redeem` or `issue` for a specific subject and request, so the admin cannot preconfigure or export one. The trusted business backend obtains the user's `subject_id` from its own authentication and creates stable `request_key` values for each business operation.
