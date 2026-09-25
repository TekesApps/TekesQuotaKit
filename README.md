# TekesQuotaKit

TekesQuotaKit is a Python service for shared Service, Quota, Level, and Limit rules. Per-use Services get one atomic admission and debit call: `redeem(subject_id, service_id)`. Reported-usage Services get admitted with `issue(subject_id, service_id)` and later account for actual usage with `settle(token, consumed_units)`.

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

`init-schema` creates missing `tq_` tables in the configured database. It does not create a database, alter business tables, or seed Levels and Limits. For a reviewed MySQL deployment, [migrations/create_tables_mysql.sql](migrations/create_tables_mysql.sql) contains the explicit DDL and can be applied to the **existing** application schema. Do not run both creation methods on the same schema. The API binds to `127.0.0.1:9460` by default. Caller credentials and the admin credential stay on trusted servers; never put them in a miniapp or browser bundle. Keep `TEKES_QUOTA_TOKEN_SECRET` stable across restarts so issue retries continue to produce the same token.

## First integration

1. Configure tenant `shukang-zhiyi`, Quotas, Services, Levels, and Limits through the authenticated `/v1/admin` API.
2. Associate a resident with a Level using the resident's existing positive integer ID as `subject_id`.
3. If one backend both initiates and performs the Service, create one `consumer` client. For separate backends, create an `issuer` client and a `provider` client. Each client is bound to one tenant and one Service; use different long random keys for separate clients.
4. For a `per_use` Service, its trusted performing backend calls `POST /v1/redeem` with the authenticated resident's integer `subject_id` and registered integer `service_id`. Success atomically admits one use, deducts one unit, and returns a token for possible refund or status inspection. There is no prior token request or second admission call. The backend runs the business operation only after success. A rejected redemption must stop the Service.
5. For a `reported_usage` Service, the trusted backend calls `POST /v1/token` with the same IDs. Success admits the work and returns a token without deducting usage. After the work, its provider calls `POST /v1/token/settle` with that token and actual `consumed_units`, including partial usage on failure. There is no intermediate redemption call.
6. Both admission calls accept an optional stable `request_key`. Use a business request ID to prevent retries from creating another use. A repeated per-use key returns HTTP 409 `already_redeemed` with the original token and does not authorize running the operation again; a repeated reported-usage key returns the same token with `idempotent: true`. Without a key, every call is a separate use. A per-use failure that qualifies for a refund calls `/v1/token/refund` with the returned token.

Example request bodies (sent by trusted backends with a Service credential):

```text
Per use:        POST /v1/redeem         {"subject_id":42,"service_id":7,"request_key":"measurement-123"}
Reported usage: POST /v1/token         {"subject_id":42,"service_id":8,"request_key":"chat-456"}
After usage:    POST /v1/token/settle  {"token":"tq_...","consumed_units":750}
```

`GET /v1/quota` is for display only. It cannot authorize execution. `GET /v1/tokens/unsettled` lists admitted metered uses still awaiting settlement. The provider must retain its token until settlement succeeds and reconcile missing usage. Settlement needs only the token and `consumed_units` in its body plus the provider's server credential; QuotaKit resolves the subject from the token. Refund and status endpoints require the trusted `X-Subject-ID` header containing the resident ID as decimal text.

If a provider loses an admission response, it must retry with the same `request_key` or recover the original business result. The repeated per-use call returns the original token for refund or status lookup, but never a fresh admission. It must not start a second business operation solely because a token is returned again. A `settled` token status confirms the quota transaction, not the business side effect; the provider must also make its business operation idempotent.

This initial implementation uses integer units and `day`, `month`, or `level_term` periods. Metered calls may exceed a finite Limit when concurrent calls settle later; consumers must bound concurrency and maximum per-call usage. Business-specific success events, identity mapping, and legacy quota migration remain with each consumer. See [用户服务Quota通用模型.md](用户服务Quota通用模型.md) for the complete model and open design decisions.

The first runnable slice covers one provider Service per credential, one Quota per Service, Level assignment, finite/unlimited Limits, direct per-use redemption, reported-usage admission and settlement, refunds, and an immutable usage ledger. Versioned Service mappings, mid-period Level changes, and a shared grant spanning separately deployed atomic Services remain design work before broad production rollout. The existing Shukang measurement counter must not be charged alongside this ledger during migration.

## Tests

```bash
uv run pytest
uv run ruff check .
```

The SQLite tests exercise the portable model. The optional MySQL integration test uses a disposable test schema named by `TEKES_QUOTA_TEST_DATABASE_URL`; it never targets the configured application database.
