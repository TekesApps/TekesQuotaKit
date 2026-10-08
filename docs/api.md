# TekesQuotaKit HTTP API

This document describes every HTTP route served by `tekes-quota-kit serve`. It is written against version 0.2.0. When the server is running, the live OpenAPI UI is at `/docs` and the machine-readable schema is at `/openapi.json`.

Examples use tenant `demo-tenant`, subject `42`, and a server at `http://127.0.0.1:9460`.

## Concepts in one paragraph

A **tenant** groups all configuration. A **Quota** is a counter definition (`unit_code`, `metering_mode`). A **Level** is a plan a subject can hold; a **Limit** caps one Quota for one Level per period. An **Assignment** puts a subject on a Level. A **Service** is the thing a client sells or performs; it may charge a Quota. A **client** is a backend credential bound to exactly one tenant, one `service_code`, and one role. A **token** (`tq_` followed by 64 hex characters) is returned when a use is admitted or charged, and is the handle for every later call about that use.

## Authentication

Consumer routes take `Authorization: Bearer <client key>`. Admin routes accept either the admin key as a Bearer credential, for scripts, or a web admin session cookie, for the browser console.

| Credential | Used for | Rules |
| --- | --- | --- |
| Admin key | `/v1/admin/...` routes | Set by `TEKES_QUOTA_ADMIN_KEY`. Must be at least 32 characters or the server refuses to start. Compared with `hmac.compare_digest`. If an `Authorization` header is present it must be valid; a session cookie does not rescue a wrong Bearer key. |
| Web admin session | `/v1/admin/...` routes | `tq_admin_session` cookie set by `POST /v1/admin/session/login`. See [Web admin sessions](#web-admin-sessions). |
| Client key | Consumer routes (`/v1/redeem`, `/v1/token`, ...) | Created with `PUT /v1/admin/clients/{client_id}` (you supply the key, at least 32 characters) or `POST /v1/admin/clients/{client_id}/provision` (the server generates it). Only a SHA-256 hash is stored. |

Each client row holds `tenant_id`, `service_code`, and `role`. A client can only act on its own tenant and its own Service; requests for any other Service fail with `service_forbidden` (403) or `scope_mismatch` (403).

Roles:

| Role | Satisfies |
| --- | --- |
| `issuer` | Endpoints that require `issuer` |
| `provider` | Endpoints that require `provider` |
| `consumer` | Both `issuer` and `provider` endpoints |
| `membership` | Only the `/v1/members` endpoints. Not bound to a Service; `consumer` does not include it. |

A client key whose role does not satisfy the endpoint is rejected exactly like an unknown key: 401 with code `unauthorized`.

### X-Subject-ID

**One user ID per person, everywhere.** Kit never defines or looks up user IDs; the business system chooses which positive integer identifies a person, such as its user table's primary key. Member sync (`/v1/members`) and every Service request (`subject_id` in bodies, `X-Subject-ID` in headers) must send that same ID. Kit matches them by number only, so a member registered under one ID and served under another is rejected with 409 `no_level`. Both kinds of client contract open with this rule and carry it as `subject_id_rule`.

`POST /v1/use`, `POST /v1/close`, `POST /v1/token/refund`, `POST /v1/token/status`, and `GET /v1/quota` require an `X-Subject-ID` header. Its value is the subject ID as decimal text, a positive integer no larger than 9223372036854775807 (2^63-1). A missing, zero, negative, non-numeric, or out-of-range value returns 422. For token operations the header must match the subject the token was created for, otherwise the call fails with `scope_mismatch` (403).

Only your trusted backend should set this header, from its own authenticated session. Never let a browser or app supply it directly.

## Error format

Domain errors raised by the quota engine (`QuotaError`) are returned with their HTTP status and this JSON body:

```json
{"code": "quota_exhausted", "message": "Quota exhausted"}
```

Some errors add detail fields at the top level. `already_redeemed` carries the original token and its status:

```json
{"code": "already_redeemed", "message": "Request already redeemed", "token": "tq_3f9c...", "status": "settled"}
```

Errors raised by the HTTP layer use FastAPI's default shape `{"detail": ...}`:

| Status | When |
| --- | --- |
| 401 | Missing `Authorization` header, missing `Bearer ` prefix, or wrong admin key (`{"detail": "Bearer credential required"}` or `{"detail": "Invalid admin credential"}`). An invalid client key returns the `QuotaError` body with code `unauthorized`. |
| 400 | `POST /v1/redeem` with both or neither of `service_id` / `service_code`; `PUT /v1/admin/clients/{client_id}` whose path and body `client_id` differ; provision with an unsafe code or invalid `base_url`. |
| 404 | Unknown table in `GET /v1/admin/tables/{name}`. |
| 422 | Request validation failure (wrong type, out-of-range value, missing field, bad `X-Subject-ID`). `detail` is FastAPI's list of validation errors. |

### Error codes

The table lists every code raised in `core.py`. Several 400 codes are normally pre-empted by request validation (422) at the HTTP layer and are mainly seen when calling `QuotaKit` from Python.

| Code | Status | Meaning |
| --- | --- | --- |
| `unauthorized` | 401 | Client key unknown, or its role does not satisfy the endpoint. |
| `service_forbidden` | 403 | The Service exists in the tenant but is not the client's bound `service_code`. |
| `scope_mismatch` | 403 | Token belongs to another tenant, Service, or subject (`X-Subject-ID`). |
| `wrong_provider` | 403 | Token was redeemed, settled, or opened by a different client; only that client may use, close, settle, refund, or inspect it. |
| `unknown_service` | 404 | Service not found in the tenant. |
| `unknown_quota` | 404 | Quota not found in the tenant. |
| `unknown_level` | 404 | Level not found in the tenant. |
| `unknown_member` | 404 | Service member mapping not found (DELETE). |
| `invalid_token` | 404 | Token not found. |
| `invalid_role` | 400 | Role is not `issuer`, `provider`, or `consumer`. |
| `weak_client_key` | 400 | Client key shorter than 32 characters. |
| `invalid_mode` | 400 | `metering_mode` is not `per_use` or `reported_usage`. |
| `invalid_unit` | 400 | A `per_use` Quota must have `unit_code` `use`. |
| `invalid_service_kind` | 400 | `service_kind` is not `atomic` or `composite`. |
| `invalid_service_config` | 400 | The combination of kind, redemption mode, Quota, `charge_units`, and `session_ttl_seconds` is not allowed (see PUT Service). |
| `invalid_member` | 400 | Parent equals child, `max_uses` out of range, or parent is not composite / child is not atomic. |
| `invalid_limit_mode` | 400 | `limit_mode` is not `finite` or `unlimited`. |
| `invalid_limit` | 400 | `finite` needs `limit_value >= 0`; `unlimited` needs `limit_value` null. Also raised by `expire-sessions` when `--limit` is outside 1 to 5000. |
| `invalid_period` | 400 | `period_kind` is not `day`, `week`, `month`, or `level_term`. |
| `invalid_timezone` | 400 | `timezone` is not a known IANA zone. |
| `invalid_subject` | 400 | Subject ID is not a positive integer up to 2^63-1. |
| `invalid_effective_at` | 400 | `effective_at` is more than 5 seconds in the past. |
| `invalid_expiry` | 400 | `expires_at` is not after `effective_at`. |
| `term_change_requires_renewal` | 400 | Assignment changes the current term end without `renew_term: true`. |
| `missing_identity` | 400 | Subject or Service identifier missing or invalid. |
| `invalid_request_key` | 400 | `request_key` is not 1 to 128 characters. |
| `missing_request_key` | 400 | Durable redeem, `/v1/begin`, or `/v1/use` called without `request_key`. |
| `invalid_duration` | 400 | `duration_seconds` outside 60 to 86400. |
| `invalid_units` | 400 | `consumed_units` is negative. |
| `missing_units` | 400 | Settling a reported-usage token without `consumed_units`. |
| `wrong_mode` | 400 | `duration_seconds` sent to an instant Service, or `consumed_units` sent when settling a durable session. |
| `wrong_mode` | 409 | Operation does not fit the Quota's metering mode: redeem on a `reported_usage` Quota, issue on a `per_use` Quota, durable Service on a non-`per_use` Quota, settle on a non-metered token, refund on a non-`per_use` token. |
| `wrong_service_kind` | 409 | Operation does not fit the Service kind or mode: issue on a composite Service, instant redeem on a durable Service, `/v1/begin` on a non-durable Service, close on a token without a session. |
| `client_exists` | 409 | Provision for an existing `client_id` without `rotate: true`. |
| `quota_definition_immutable` | 409 | Attempt to change an existing Quota's `unit_code` or `metering_mode`. |
| `period_immutable` | 409 | Attempt to change an existing Limit's `period_kind` or `timezone`. |
| `no_level` | 409 | Subject has no active Assignment. |
| `no_quota` | 409 | Service has no direct Quota (a child-only Service cannot be redeemed, issued, or have its balance read). |
| `no_limit` | 409 | Subject's Level has no Limit for the Service's Quota. |
| `missing_term_end` | 409 | Limit uses `level_term` but the subject's Assignment has no expiry. |
| `quota_exhausted` | 409 | Charging would exceed the Limit for the current period. |
| `already_redeemed` | 409 | Instant redeem repeated with the same `request_key`. Details: `token`, `status`. |
| `duration_conflict` | 409 | Durable redeem retried with the same `request_key` but a different `duration_seconds`. |
| `empty_composite` | 409 | Composite Service has no members at redeem time. |
| `session_not_open` | 409 | Durable session is closed, expired, or refunded. |
| `request_key_conflict` | 409 | `/v1/use` `request_key` was already used in this session for a different child. |
| `child_unavailable` | 409 | Child is not in the session snapshot, or its `max_uses` is reached. |
| `settlement_conflict` | 409 | Reported-usage token already settled with a different `consumed_units`. |
| `not_admitted` | 409 | Token is not in `admitted` state for settlement. |
| `not_redeemed` | 409 | Refund on a token that is not `settled`. |
| `child_already_used` | 409 | Refund on a durable session in which at least one child use was recorded. |
| `unsupported_database` | 500 | Database URL is not `mysql+pymysql://` or `sqlite:///`. Raised at startup. |
| `weak_secret` | 500 | Token secret shorter than 32 characters. Raised at startup. |

## Route table

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | none | Liveness check, returns `{"status": "ok"}`. Does not touch the database. |
| POST | `/v1/redeem` | client, `provider` | Charge an instant use, or open a durable session. |
| POST | `/v1/use` | client, `provider` + `X-Subject-ID` | Record one child use inside a durable session. |
| POST | `/v1/stop` | client, `provider` | Close a durable session. |
| POST | `/v1/token` | client, `issuer` | Admit a reported-usage operation and return a token. |
| POST | `/v1/token/settle` | client, `provider` | Settle a reported-usage token, or close a durable session. |
| POST | `/v1/token/refund` | client, `provider` + `X-Subject-ID` | Refund a `per_use` charge. |
| POST | `/v1/token/status` | client, `provider` + `X-Subject-ID` | Read a token's state. |
| GET | `/v1/quota` | client, `issuer` + `X-Subject-ID` | Read the subject's balance for the client's Service. |
| GET | `/v1/tokens/unsettled` | client, `provider` | List admitted, unsettled reported-usage tokens. |
| PUT | `/v1/members/{subject_id}` | client, `membership` | Set or renew a member's Level. |
| GET | `/v1/members/{subject_id}` | client, `membership` | Read a member's current Level, or `null`. |
| DELETE | `/v1/members/{subject_id}` | client, `membership` | End a membership now. |
| POST | `/v1/members/batch` | client, `membership` | Set up to 500 members in one call. |
| POST | `/v1/begin` | client, `provider` | Legacy alias: open a durable session (`request_key` required). |
| POST | `/v1/close` | client, `provider` + `X-Subject-ID` | Legacy alias: close a durable session. |
| PUT | `/v1/admin/clients/{client_id}` | admin | Create or replace a client with a caller-supplied key. |
| DELETE | `/v1/admin/tenants/{tenant}/clients/{client_id}` | admin | Revoke a client: its key stops working at once. 404 `unknown_client` if it does not exist in that tenant. Issued tokens keep their records. |
| POST | `/v1/admin/clients/{client_id}/provision` | admin | Create or rotate a client with a generated key; returns a Markdown contract. |
| PUT | `/v1/admin/tenants/{tenant}/quotas/{quota_code}` | admin | Create a Quota (or confirm an identical one). |
| PUT | `/v1/admin/tenants/{tenant}/levels/{level_code}` | admin | Create a Level. |
| PUT | `/v1/admin/tenants/{tenant}/services/{service_code}` | admin | Create or replace a Service. |
| PUT | `/v1/admin/tenants/{tenant}/services/{parent}/members/{child}` | admin | Add or update a child of a composite Service. |
| DELETE | `/v1/admin/tenants/{tenant}/services/{parent}/members/{child}` | admin | Remove a child from a composite Service. |
| PUT | `/v1/admin/tenants/{tenant}/levels/{level_code}/limits/{quota_code}` | admin | Create or update a Limit. |
| PUT | `/v1/admin/tenants/{tenant}/subjects/{subject_id}/level` | admin | Assign a subject to a Level. |
| GET | `/v1/admin/tables` | admin | List browsable table names. |
| GET | `/v1/admin/tables/{name}` | admin | Browse rows of one `tq_` table for one tenant. Extra query parameters filter by exact match on visible columns, e.g. `?tenant=t&subject_id=42`; integer columns need integers; unknown or hidden columns return 400. |
| GET | `/v1/admin/tenants/{tenant}/subjects/{subject_id}/usage` | admin | A user's Level and, for each 配额 it grants, `limit`, `used`, `remaining`, `period_end`, and the Services that use it. `member: null` if the user has no active Level. |
| GET | `/v1/admin/tenants/{tenant}/overview` | admin | Counts of Services, Levels, clients, and `active_members` (people with an active Level now). |
| POST | `/v1/admin/session/login` | none (guard header) | Sign in to the web admin; sets the session cookie. |
| GET | `/v1/admin/session` | admin | Current account (`username`, `expires_at`, `via`). |
| POST | `/v1/admin/session/logout` | admin | Revoke the session and clear the cookie. |
| GET | `/v1/admin/tenants` | admin | Registered business systems plus tenant IDs that only appear in data. |
| PUT | `/v1/admin/tenants/{tenant}` | admin | Register a business system or rename it. |
| GET | `/admin` | none | Admin web console (HTML, opens on the sign-in page). |
| GET | `/admin/assets/{file}` | none | Hashed console script and stylesheet. |
| GET | `/docs` | none | OpenAPI UI (FastAPI default). |
| GET | `/openapi.json` | none | OpenAPI schema (FastAPI default). |

The console page and its assets are served without authentication. The page has `Cache-Control: no-store` and a Content Security Policy that allows only same-origin scripts and connections. Hashed assets are cached as immutable. All data comes from the admin API, which requires a session or the admin key.

## Web admin sessions

Accounts are created on the server with `tekes-quota-kit admin-user add` (see the README's CLI table). There is no sign-up endpoint.

`POST /v1/admin/session/login` takes `{"username": "...", "password": "..."}` and requires two headers:

| Header | Purpose |
| --- | --- |
| `X-Admin-Request: 1` | Required on sign-in and on every cookie-authenticated `POST`, `PUT`, `PATCH`, or `DELETE`. The service adds no CORS headers, so a cross-site page cannot send it; missing it returns 403. |
| `X-Admin-Base` | The path prefix the console is served under, such as `/user-quota`, or empty at the root. It sets the cookie path to `<prefix>/v1/admin`, so the cookie is never sent to other applications on the same host. |

On success the response sets `tq_admin_session` with `HttpOnly`, `SameSite=Strict`, a 12-hour `Max-Age`, and `Secure` when the request arrived over HTTPS (`X-Forwarded-Proto: https`). The body is `{"data": {"username": "...", "expires_at": "...Z", "via": "session"}}`.

| Status | Meaning |
| --- | --- |
| 401 | Wrong username or password, or a disabled account. Unknown usernames take as long as known ones. |
| 429 | 5 failed sign-ins for this username in the last 15 minutes. |
| 400 | `X-Admin-Base` is not a plain path prefix. |

Sessions end at expiry, on `POST /v1/admin/session/logout`, or when `admin-user passwd` or `admin-user disable` runs for the account. Passwords are stored as PBKDF2-SHA256 with 600,000 rounds.

## Token lifecycle

| Field | Values |
| --- | --- |
| `status` | `admitted` (issued, not yet settled), `settled` (charged), `refunded` |
| `session_status` | `null` for non-durable tokens; `open`, `closed`, or `expired` for durable sessions |

A token is derived deterministically from the token secret and `(tenant_id, client_id, subject_id, service_code, request_key)`. The same client retrying the same request key for the same subject and Service always gets the same token, which is what makes retries idempotent. The server stores only a hash of the token.

## Consumer endpoints

### POST /v1/redeem

Role: `provider` (or `consumer`).

Charges one use of an instant Service, or opens a charged durable session. The behavior depends on the Service's `redemption_mode`.

Request body:

| Field | Type | Constraints |
| --- | --- | --- |
| `subject_id` | integer | Required. Strict JSON integer (a string such as `"42"` is rejected with 422), 1 to 9223372036854775807. |
| `service_id` | integer | Optional, > 0. Exactly one of `service_id` / `service_code` is required (otherwise 400). |
| `service_code` | string | Optional, 1 to 64 characters. |
| `request_key` | string | Optional for instant, required for durable. 1 to 128 characters. |
| `duration_seconds` | integer | Optional, 60 to 86400. Durable only; overrides the Service's `session_ttl_seconds`. Sending it to an instant Service returns `wrong_mode` (400). |

The Service must be the client's bound `service_code` in the client's tenant.

**Instant Service** (`redemption_mode: instant`, `per_use` Quota). The use is checked against the Limit and charged one unit in one transaction. Response:

```json
{
  "token": "tq_...",
  "status": "settled",
  "unit": "use",
  "usage_key": "<64 hex>",
  "quota_code": "api_calls"
}
```

Idempotency: repeating the same `request_key` never charges again. It returns 409 `already_redeemed` with the original `token` and `status` in the body, so a retry after a timeout can recover the token. If `request_key` is omitted, the server generates a random one and the call is not idempotent.

**Durable Service** (`redemption_mode: durable`). Charges `charge_units` once and opens a session. For a composite Service the current member list is snapshotted into the session; for an atomic durable Service the session authorizes the Service itself. Response:

```json
{
  "token": "tq_...",
  "status": "settled",
  "session_status": "open",
  "expires_at": "2026-10-08T09:30:00Z",
  "authorized_services": ["child_a", "child_b"],
  "idempotent": false
}
```

`expires_at` is UTC with a `Z` suffix, or `null` if the session has no TTL. Idempotency: repeating the same `request_key` returns 200 with the same token, the current status, and `"idempotent": true`, without charging again (even if the session has since closed or the Quota is now exhausted). Repeating it with a different `duration_seconds` returns `duration_conflict` (409).

Typical errors: `unauthorized`, `service_forbidden`, `unknown_service`, `no_level`, `no_limit`, `no_quota`, `missing_term_end`, `quota_exhausted`, `already_redeemed`, `wrong_mode`, `wrong_service_kind`, `missing_request_key`, `empty_composite`, `duration_conflict`.

Example:

```sh
curl -sS http://127.0.0.1:9460/v1/redeem \
  -H "Authorization: Bearer $CLIENT_KEY" -H "Content-Type: application/json" \
  -d '{"subject_id": 42, "service_code": "report_export", "request_key": "export-7f3a"}'
```

### POST /v1/use

Role: `provider`. Header: `X-Subject-ID`.

Records one child use inside an open durable session. It never charges the Quota.

| Field | Type | Constraints |
| --- | --- | --- |
| `token` | string | Required, non-empty. Token from a durable redeem. |
| `child_service_code` | string | Required, 1 to 64 characters. Must be in the session's `authorized_services`. |
| `request_key` | string | Required, 1 to 128 characters. Identifies one attempt; a real repeat of the child needs a new key. |

Checks, in order: the token exists and matches the client's tenant, Service, and `X-Subject-ID`; the caller is the client that opened the session; the session is `open` and not past `expires_at`; the child is in the snapshot; the child's `max_uses` (if any) is not exceeded.

Response:

```json
{"child_service_code": "child_a", "slot_no": 1, "status": "used", "idempotent": false}
```

`slot_no` counts uses of that child within the session, starting at 1. Idempotency: the same `request_key` for the same child returns the original record with `"idempotent": true`; the same key for a different child returns `request_key_conflict`. A retry after the session has closed or expired returns `session_not_open`.

Typical errors: `invalid_token`, `scope_mismatch`, `wrong_provider`, `session_not_open`, `child_unavailable`, `request_key_conflict`.

### POST /v1/stop

Role: `provider`. No `X-Subject-ID` header.

Closes a durable session. Body: `{"token": "tq_..."}`. Equivalent to `POST /v1/token/settle` without `consumed_units`.

Response:

```json
{"status": "settled", "session_status": "closed", "idempotent": false}
```

If the session is already past `expires_at`, `session_status` becomes `expired` instead of `closed`. Unused child grants are marked expired. Stopping never refunds. Calling again returns the current state with `"idempotent": true`.

Called on a non-durable token, it behaves like settle: `per_use` tokens return `wrong_mode` (409), reported-usage tokens return `missing_units` (400).

### POST /v1/token

Role: `issuer` (or `consumer`).

Admits one reported-usage operation. The subject must currently be under its Limit (`used < limit_value`); nothing is charged until settlement.

| Field | Type | Constraints |
| --- | --- | --- |
| `subject_id` | integer | Required, strict integer, 1 to 9223372036854775807. |
| `service_id` | integer | Required, > 0. The numeric Service ID (returned by `PUT .../services/{service_code}` and in the provisioned contract). Service codes are not accepted here. |
| `request_key` | string | Optional, 1 to 128 characters. Generated randomly if omitted (then not idempotent). |

The Service must be atomic and its Quota must be `reported_usage`.

Response:

```json
{"token": "tq_...", "status": "admitted", "idempotent": false}
```

Idempotency: the same `request_key` returns the same token with its current `status` and `"idempotent": true`, without re-checking the Limit.

Typical errors: `service_forbidden`, `unknown_service`, `wrong_service_kind`, `wrong_mode`, `no_quota`, `no_level`, `no_limit`, `missing_term_end`, `quota_exhausted`.

### POST /v1/token/settle

Role: `provider`.

| Field | Type | Constraints |
| --- | --- | --- |
| `token` | string | Required. |
| `consumed_units` | integer | `>= 0`. Required for reported-usage tokens; must be omitted for durable sessions. |

For a reported-usage token, adds `consumed_units` to the subject's usage for the period captured at issue time and marks the token `settled`. The reported amount is recorded as is; settlement does not re-check the Limit, so usage can exceed the Limit by the last settled amount. Response:

```json
{"status": "settled", "consumed_units": 120, "idempotent": false}
```

Retrying with the same `consumed_units` returns `"idempotent": true`; a different value returns `settlement_conflict`. The first provider to settle a token owns it; other providers get `wrong_provider`.

For a durable session, this closes the session exactly like `/v1/stop`.

Typical errors: `invalid_token`, `scope_mismatch`, `wrong_provider`, `wrong_mode`, `missing_units`, `settlement_conflict`, `not_admitted`.

### POST /v1/token/refund

Role: `provider`. Header: `X-Subject-ID`. Body: `{"token": "tq_..."}`.

Refunds a `per_use` charge (instant or durable). Only the client that redeemed the token may refund it. A durable session can be refunded only if no child use was recorded (`child_already_used` otherwise); refunding closes the session. The refunded units are returned to the period in which they were charged, and a `refund` ledger row is written.

Response:

```json
{"status": "refunded", "idempotent": false}
```

A second refund returns `"idempotent": true`. Reported-usage tokens cannot be refunded (`wrong_mode`).

Typical errors: `invalid_token`, `scope_mismatch`, `wrong_provider`, `wrong_mode`, `not_redeemed`, `child_already_used`.

### POST /v1/token/status

Role: `provider`. Header: `X-Subject-ID`. Body: `{"token": "tq_..."}`.

Response:

```json
{"status": "settled", "session_status": null, "consumed_units": 1, "usage_key": "<64 hex>"}
```

A token already redeemed or settled by another provider returns `wrong_provider`.

### GET /v1/quota

Role: `issuer` (or `consumer`). Header: `X-Subject-ID`.

Returns the subject's balance for the Quota of the client's bound Service in the current period. This is for display only; it does not reserve anything.

```json
{
  "quota_code": "api_calls",
  "unit": "use",
  "limit": 100,
  "used": 3,
  "remaining": 97,
  "period_end": "2026-10-31T16:00:00Z"
}
```

For an `unlimited` Limit, `limit` and `remaining` are `null`. `period_end` is UTC.

Typical errors: `unauthorized` (a `provider`-only client cannot call this), `unknown_service`, `no_quota`, `no_level`, `no_limit`, `missing_term_end`.

### GET /v1/tokens/unsettled

Role: `provider`.

Lists tokens in the client's tenant and Service with status `admitted` that are unclaimed or claimed by this client. Use it to find reported-usage operations that were admitted but never settled.

```json
[{"token_hash": "<64 hex>", "subject_id": 42, "admitted_at": "2026-10-08T08:15:02.123456Z"}]
```

The response contains the token hash, not the token. Match it against your own records by computing SHA-256 of the token string you hold.

### POST /v1/begin (legacy)

Role: `provider`. Opens a durable session. Body: `subject_id` (strict integer), `service_code` (1 to 64), `request_key` (required, 1 to 128), `duration_seconds` (optional, 60 to 86400). The Service must be durable (`wrong_service_kind` otherwise). Response and idempotency are the same as durable `/v1/redeem`. New integrations should use `/v1/redeem`.

### POST /v1/close (legacy)

Role: `provider`. Header: `X-Subject-ID`. Body: `{"token": "tq_..."}`. Closes a durable session opened by the same client. Response: `{"session_status": "closed", "idempotent": false}` (or `expired` if already past expiry; `"idempotent": true` if already closed). A token without a session returns `wrong_service_kind`. New integrations should use `/v1/stop`.

## Member sync endpoints

A business system keeps Kit's member list in step with its own memberships through one `membership` client. Issue it with `POST /v1/admin/clients/{client_id}/provision` and `"role": "membership"`; `service_code` is ignored and stored as `*`. The downloaded contract (`tekes-quotakit-membership/v1`) lists the Levels the client may assign and the Limits each grants. All calls act on the client's own tenant.

Send only users who hold a membership. A user without an active Level is rejected by every Service with 409 `no_level`.

### PUT /v1/members/{subject_id}

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `level_code` | string | yes | An existing Level of the tenant, else 404 `unknown_level`. |
| `expires_at` | datetime | no | ISO 8601 with offset. Omit for no end date. Must be in the future, else 400 `invalid_expiry`. |
| `renew_term` | bool | no, default `false` | `true` for a real purchase or renewal: starts a new term and allows a new expiry. With `false`, a different expiry for an active member returns 400 `term_change_requires_renewal`. |

The membership takes effect now. Response: `{"subject_id": 42, "member": {"level_code", "effective_at", "expires_at", "term_start", "term_end"}}`. Re-sending the current state is safe.

### GET /v1/members/{subject_id}

Response: `{"subject_id": 42, "member": null}` when the user has no active Level, otherwise the same `member` object as above.

### DELETE /v1/members/{subject_id}

Ends the active membership now by closing its record; history is kept. Response: `{"subject_id": 42, "ended": true}`, or `false` when nothing was active, so it is safe to repeat. A membership that reaches `expires_at` ends by itself.

### POST /v1/members/batch

Body `{"items": [...]}` with 1 to 500 items, each `{"subject_id", "level_code", "expires_at", "renew_term"}`. Items are applied one by one; a failure does not undo the others. Response: `{"total": n, "failed": k, "results": [{"subject_id", "ok", "code"?, "message"?}]}`. Intended for the one-time import of existing members.

## Admin configuration endpoints

All routes in this section require the admin key. Each `PUT` is an upsert and is safe to repeat with the same body.

### PUT /v1/admin/clients/{client_id}

Creates or replaces a client with a key you supply.

| Field | Type | Constraints |
| --- | --- | --- |
| `client_id` | string | 1 to 64 characters. Must equal the path value (400 otherwise). |
| `key` | string | At least 32 characters. Stored as SHA-256. |
| `tenant_id` | string | 1 to 64 characters. |
| `service_code` | string | 1 to 64 characters. |
| `role` | string | `issuer`, `provider`, or `consumer`. |

Response: `{"client_id": "..."}`. Replacing an existing client overwrites its key, tenant, Service, and role immediately; the old key stops working. This route does not check that the Service exists. Prefer the provision endpoint, which generates the key and validates the Service.

### Business systems (tenants)

A tenant ID is the scope of every rule and client. Admin `PUT` calls may still use a new tenant ID implicitly, as scripts did before; registering it adds a display name and lets the console offer it.

`PUT /v1/admin/tenants/{tenant}` with `{"name": "数康智医"}` registers the business system, or renames it if already registered. An optional `subject_id_definition` sets the user ID definition quoted in client contracts; omitting it keeps the stored one. The tenant ID must match `[A-Za-z0-9._-]{1,64}`; the name must be 1–100 non-blank characters. Both errors return 400. There is no delete.

`GET /v1/admin/tenants` returns:

```json
{
  "tenants": ["demo-business", "legacy"],
  "items": [
    {"tenant_id": "demo-business", "name": "演示系统", "registered": true, "subject_id_definition": "user_table.id"},
    {"tenant_id": "legacy", "name": null, "registered": false, "subject_id_definition": null}
  ]
}
```

`tenants` lists every ID; `items` marks which are registered. An unregistered ID appears when admin calls used it without registration.

### PUT /v1/admin/tenants/{tenant}/quotas/{quota_code}

| Field | Type | Constraints |
| --- | --- | --- |
| `unit_code` | string | 1 to 32 characters. Must be `use` when `metering_mode` is `per_use`. |
| `metering_mode` | string | `per_use` or `reported_usage`. |

Response: `{"quota_code": "..."}`. Immutable: once a Quota exists, a PUT with a different `unit_code` or `metering_mode` returns `quota_definition_immutable` (409). Create a new Quota code instead.

### PUT /v1/admin/tenants/{tenant}/levels/{level_code}

No body. Creates the Level if it does not exist. Response: `{"level_code": "..."}`.

### PUT /v1/admin/tenants/{tenant}/services/{service_code}

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `quota_code` | string or null | `null` | 1 to 64 characters. Must reference an existing Quota (`unknown_quota`). `null` means a child-only Service that can be used inside a composite session but not redeemed directly. |
| `service_kind` | string | `atomic` | `atomic` or `composite`. |
| `redemption_mode` | string or null | `null` | `instant` or `durable`. When null: `durable` for composite, `instant` for atomic. |
| `charge_units` | integer or null | `null` | > 0. Units charged once per durable session. |
| `session_ttl_seconds` | integer or null | `null` | 60 to 86400. Default session lifetime for durable sessions; `null` means no expiry. |

Allowed combinations (anything else returns `invalid_service_config`, 400):

| Kind | Mode | Rules |
| --- | --- | --- |
| `atomic` | `instant` | `charge_units` and `session_ttl_seconds` must be null. `quota_code` may be null (child-only), a `per_use` Quota (redeem), or a `reported_usage` Quota (issue and settle). |
| `atomic` | `durable` | `quota_code` and `charge_units` required. Quota must be `per_use` (`wrong_mode` otherwise). |
| `composite` | `durable` | Same as atomic durable. Children are added with the members endpoint. |

Response: `{"service_id": 7, "service_code": "..."}`. A PUT replaces all fields of an existing Service; it does not affect sessions that are already open.

### PUT /v1/admin/tenants/{tenant}/services/{parent}/members/{child}

Body: `{"max_uses": null}` or `{"max_uses": 3}`. `max_uses` is null (unlimited) or 1 to 2147483647. The parent must be composite, the child atomic, and they must differ (`invalid_member`); both must exist (`unknown_service`).

Response: `{"parent_service_code": "...", "child_service_code": "...", "max_uses": 3}`.

Membership is snapshotted when a session is redeemed. Adding, changing, or removing members affects only sessions redeemed afterward.

### DELETE /v1/admin/tenants/{tenant}/services/{parent}/members/{child}

Removes a mapping. Response: `{"deleted": true}`. Returns `unknown_member` (404) if it does not exist.

### PUT /v1/admin/tenants/{tenant}/levels/{level_code}/limits/{quota_code}

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `limit_mode` | string | | `finite` or `unlimited`. |
| `limit_value` | integer or null | `null` | Required and `>= 0` for `finite`; must be null for `unlimited`. |
| `period_kind` | string | | `day`, `week`, `month`, or `level_term`. `week` runs from Monday 00:00 to the next Monday 00:00 in `timezone`. |
| `timezone` | string | `Asia/Shanghai` | IANA zone name. Defines where `day` and `month` periods start. Set it explicitly. |

Response: `{"level_code": "...", "quota_code": "..."}`. The Level and Quota must exist. `limit_mode` and `limit_value` can be changed in place. Immutable: changing `period_kind` or `timezone` on an existing Limit returns `period_immutable` (409), because usage counters are keyed by period start.

`level_term` periods run from the subject's term start to term end, so the subject's Assignment must have an expiry (`missing_term_end` at redeem time otherwise).

### PUT /v1/admin/tenants/{tenant}/subjects/{subject_id}/level

`subject_id` in the path is a positive integer.

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `level_code` | string | | 1 to 64 characters. Level must exist. |
| `effective_at` | datetime or null | now | ISO 8601. No more than 5 seconds in the past (`invalid_effective_at`). |
| `expires_at` | datetime or null | `null` | Must be after `effective_at` (`invalid_expiry`). `null` means no expiry. |
| `renew_term` | boolean | `false` | Start a new term instead of continuing the current one. |

Timezone handling: datetimes with an offset (for example `2026-11-01T00:00:00+08:00` or `...Z`) are converted to UTC. Datetimes without an offset are taken as UTC. All stored times are UTC.

Term behavior:

- If the subject has no Assignment active at `effective_at`, or `renew_term` is true, the new Assignment starts a new term from `effective_at` to `expires_at`. Usage under `level_term` Limits starts from zero for the new term.
- If an Assignment is active and `renew_term` is false, the Level changes but the term is kept. `expires_at` must be omitted or equal to the current term end (`term_change_requires_renewal`, 400, otherwise). `level_term` usage carries over.
- In both cases the previously active Assignment ends at `effective_at`.

Response: `{"subject_id": 42, "level_code": "plus"}`.

Example:

```sh
curl -sS -X PUT http://127.0.0.1:9460/v1/admin/tenants/demo-tenant/subjects/42/level \
  -H "Authorization: Bearer $TEKES_QUOTA_ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"level_code": "plus", "expires_at": "2026-11-08T00:00:00Z", "renew_term": true}'
```

### GET /v1/admin/tables

Response: `{"tables": ["tq_clients", "tq_levels", "tq_quotas", "tq_limits", "tq_assignments", "tq_services", "tq_service_members", "tq_usage", "tq_tokens", "tq_token_items", "tq_ledger"]}`.

### GET /v1/admin/tables/{name}

Query parameters:

| Parameter | Default | Constraints |
| --- | --- | --- |
| `tenant` | | Required, 1 to 64 characters. |
| `limit` | 100 | 1 to 200. |
| `offset` | 0 | `>= 0`. |

Rows are filtered to the tenant (for `tq_token_items`, through the parent token) and ordered by `id` descending.

```json
{"table": "tq_usage", "columns": ["id", "tenant_id", "..."], "total": 12, "offset": 0, "rows": [{"id": 12, "tenant_id": "demo-tenant", "used_units": 3}]}
```

`key_hash` and `token_hash` columns are never returned. Null values are omitted from each row. Datetimes are UTC ISO 8601 with a `Z` suffix. Unknown table names return 404.

### POST /v1/admin/clients/{client_id}/provision

Creates a client with a server-generated key, or rotates the key of an existing one.

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `tenant_id` | string | | 1 to 64 characters. |
| `service_code` | string | | 1 to 64 characters. The Service must exist (`unknown_service`, 404). Ignored for `membership`. |
| `role` | string | | `issuer`, `provider`, `consumer`, or `membership` (422 otherwise). |
| `subject_id_definition` | string | stored value | Up to 500 characters, written by an operator: what `subject_id` is in this business system, for example `user_table.id`. Saved on the business system and quoted in every contract it issues. Required until one is stored (400 otherwise, and no key is generated). Once stored it cannot be changed by issuing: omit it or send the same text; different text returns 409. Change it with `PUT /v1/admin/tenants/{tenant}` (the console's 业务系统 page). |
| `base_url` | string | | 8 to 512 characters. `http` or `https` URL with a host and no credentials, query, fragment, or whitespace (400 otherwise). Written into the contract as the API base URL. |
| `rotate` | boolean | `false` | Must be true to replace an existing client (`client_exists`, 409, otherwise). |

`client_id`, `tenant_id`, and `service_code` may contain only letters, digits, `.`, `-`, and `_` (400 otherwise).

The response is a Markdown file (`Content-Type: text/markdown; charset=utf-8`, `Content-Disposition: attachment; filename="<client_id>-quotakit.md"`, `Cache-Control: no-store`). It opens with the user ID rule, quoting the business system's `subject_id_definition`, which also appears in the JSON values. It contains the plaintext client key, the client's configuration (including the numeric `service_id`, Service kind and mode, Quota, metering mode, and configured children), and the call sequence permitted for its role. The key is shown only in this response. With `rotate: true` the new key replaces the old one in the same transaction, so the old key is rejected immediately; the client's tenant, Service, and role are also set to the values in the request. Tokens already issued remain valid, because tokens are bound to the `client_id`, not the key.

## Flows

Setup shared by all flows (admin key):

1. `PUT /v1/admin/tenants/demo-tenant/quotas/{quota_code}`
2. `PUT /v1/admin/tenants/demo-tenant/levels/basic`
3. `PUT /v1/admin/tenants/demo-tenant/levels/basic/limits/{quota_code}`
4. `PUT /v1/admin/tenants/demo-tenant/subjects/42/level` with `{"level_code": "basic"}`

### Instant (per use)

1. Admin: Quota `api_calls` with `{"unit_code": "use", "metering_mode": "per_use"}`; Limit `{"limit_mode": "finite", "limit_value": 100, "period_kind": "month", "timezone": "UTC"}`.
2. Admin: `PUT .../services/report_export` with `{"quota_code": "api_calls"}`.
3. Admin: `POST /v1/admin/clients/export-backend/provision` with role `provider` (or `consumer`). Store the key in the backend's secret store.
4. Backend: `POST /v1/redeem` with `subject_id`, `service_code: "report_export"`, and a stable `request_key`. On 200, perform the operation. On 409 `already_redeemed`, the use was already charged; use the returned token.
5. Backend, only if the operation failed and should not count: `POST /v1/token/refund` with `X-Subject-ID: 42`.

### Durable composite

1. Admin: Quota `session_count` (`per_use`, unit `use`) and its Limit.
2. Admin: `PUT .../services/review_session` with `{"quota_code": "session_count", "service_kind": "composite", "charge_units": 1, "session_ttl_seconds": 3600}`.
3. Admin: child Services `PUT .../services/upload` and `PUT .../services/analyze` with `{}` (atomic, no Quota).
4. Admin: `PUT .../services/review_session/members/upload` with `{"max_uses": 5}` and `.../members/analyze` with `{"max_uses": null}`.
5. Admin: provision a `provider` or `consumer` client for `review_session`.
6. Backend: `POST /v1/redeem` with `service_code: "review_session"` and a stable session `request_key`. One unit is charged. Keep `token` and `authorized_services`.
7. Backend, before each child operation: `POST /v1/use` with `X-Subject-ID: 42`, the token, a child from `authorized_services`, and a new attempt `request_key`.
8. Backend, when finished: `POST /v1/stop` with the token. If the session expires first, further `use` calls fail with `session_not_open`. Stop and expiry never refund.
9. Backend, only if no child was used and the session should not count: `POST /v1/token/refund`.

### Reported usage (metered)

1. Admin: Quota `llm_tokens` with `{"unit_code": "token", "metering_mode": "reported_usage"}` and a Limit, for example `{"limit_mode": "finite", "limit_value": 200000, "period_kind": "day", "timezone": "UTC"}`.
2. Admin: `PUT .../services/chat_completion` with `{"quota_code": "llm_tokens"}`. Note the returned `service_id`.
3. Admin: provision an `issuer` client and a `provider` client for `chat_completion` (or one `consumer` client).
4. Issuer: `POST /v1/token` with `subject_id`, the numeric `service_id`, and a stable `request_key`. Admission only checks that the subject is under the Limit.
5. Perform the work and measure it.
6. Provider: `POST /v1/token/settle` with the token and `consumed_units`.
7. Provider, periodically: `GET /v1/tokens/unsettled` to find admitted tokens that were never settled.
