# Default-plan v1 non-production handoff

Default plans are introduced in **0.17.0**. Use the exact merged/tagged commit on
[PR #42](https://github.com/TekesApps/TekesQuotaKit/pull/42) for implementation identity.
This guide creates only synthetic localhost fixtures. Production deployment and validation
are recorded separately; its examples never authorize real-user grants.

The [API reference](api.md#default-plans-and-first-initialization-0170) is the
frozen consumption contract. The Chinese [design](zh/default-plans-v1-design.md) records
the initial review, the SKZY acceptance and implementation boundaries. Tracking:
[Quota #40](https://github.com/TekesApps/TekesQuotaKit/issues/40),
[Quota #41](https://github.com/TekesApps/TekesQuotaKit/issues/41),
[SK-Backend #94](https://github.com/ShuKangZhiYi/SK-Backend/issues/94).

## Start an isolated real HTTP service

From this checkout, after `uv sync --all-groups`:

```sh
uv run python scripts/default_plans_fixture.py init --directory /tmp/tekes-quota-http-test --port 19463
uv run python scripts/default_plans_fixture.py serve --directory /tmp/tekes-quota-http-test
```

Choose a fresh private directory and a free port. `init` refuses an existing manifest.
It uses a new SQLite file and a synthetic tenant `skzy-default-plan-fixture`. `serve`
binds only 127.0.0.1. To repeat on MySQL, first create a **disposable** utf8mb4/InnoDB
schema whose name starts `tekes_quota_test_`, then pass its `mysql+pymysql://` URL
via `init --database-url`; the fixture rejects other supplied database types/names.
Neither the fixture nor the app performs SKZY estate lookups.

`fixture.json` is created mode 0600 in that directory. Read it locally; never commit
it or put it in Issue/PR evidence. Fields:

| Field | Use |
| --- | --- |
| base_url, tenant_id | URL and synthetic tenant |
| catalog_key | Bearer default_catalog, GET catalog only |
| initialization_key | Bearer default_init, POST initialization only |
| admin_key | Test-only admin Bearer for configuration/issued package inspection |
| consumer_key | Test-only visit Service consumption/balance |
| vip_week_plan_id | One formal VIP Level package, seven days |
| multi_plan_id | VIP + bonus, both seven days, ordered |
| disabled_plan_id | Disabled VIP plan for blocked/replay cases |

The fixture VIP is explicitly **synthetic** `fixture_vip`, with 20 visits per Level
term. It does not guess a production VIP code. Production operators must inspect the
actual tenant Level configuration and explicitly choose the correct Level.
Earlier deployment notes classify an SKZY VIP as test; that is not live evidence.
Since 0.17.1 a private plan may reference that test Level directly; do not promote or
clone it merely to configure this plan. Its public membership-catalog visibility stays unchanged.

## Create a seven-day plan without changing VIP

On a non-production tenant, register the existing identity definition using the admin
tenant endpoint, then provision separate Clients using
`POST /v1/admin/clients/{client_id}/provision` with `tenant_id`, `role`, `base_url`
and the existing `subject_id_definition` contract. `service_code` is ignored for the
two new roles. Store the downloaded key privately in the corresponding server.

Use admin `POST /v1/admin/tenants/{tenant}/default-plans`:

```json
{
  "name": "VIP 七天", "enabled": true,
  "packages": [{
    "code": "vip-week", "name": "VIP 七天",
    "level_code": "confirmed-vip-code", "validity_days": 7
  }]
}
```

This copies that Level's current Limit definitions into the new plan revision. It
does not change the original Level. Each initialization uses the plan's current
revision and creates independent quota counters with its own seven-day term.
SKZY estates can reference the same returned stable plan ID. No production settings
are changed by these instructions or the fixture.

## Cross-repository HTTP checks

Use only synthetic users and server credentials:

1. Fetch all catalog pages, including disabled plans; check total/limit/offset and
   tenant scope. Swap credentials and verify both role violations return 401.
2. POST initialization with the original four-field source. Inspect the fact and all
   package IDs; retry with another/missing plan and verify the identical fact, with
   only replayed changing to true. A new subject with disabled/missing plan must fail.
3. Initialize subject A with the seven-day plan, then subject B at a later actual time.
   Using test admin Bearer, read
   `GET /v1/admin/tenants/{tenant}/subjects/{subject}/level-packages` for both. Match
   each Assignment integer id to the fact's string package IDs. Check effective_at and
   term_start equal that subject's applied_at; expires_at and term_end are exactly
   604800 seconds later; A/B absolute deadlines differ. Retry A and confirm no extension.
4. Exercise the multi-package plan, pre-existing synthetic paid membership and actual
   `fixture_visit` use. Check paid identity/counters/priority remain and gifts add after it.
   Admin user package GET exposes each independent quota usage and expiry.
5. Change plan/Level rules and disable the plan. Existing users replay and consume
   their original frozen limits; a new subject cannot receive the disabled plan.
6. To test Quota's optional replay guarantee for response loss, discard a successful real HTTP response (or interrupt the
   client before persisting it), restart the caller and retry the same request; verify
   one fact and one set of package IDs. Quota database concurrency/failure injection
   tests supply the separate transaction evidence; a mock HTTP test is not a substitute.

Quota does not implement SKZY binding/history logic. New users initialize only
after SKZY's first-binding decision; existing users are not automatically backfilled.
An already initialized subject cannot receive another default grant. Never clear the
fact to issue a separate campaign; that would require a separate authorized contract.

## Current SKZY delivery policy (2026-10-31 amendment)

The user's latest instruction replaces the earlier reliable-delivery/outbox proposal:
make one initialization request after the first successful binding. Failure leaves the
binding successful; no grant outbox, grant status cache, retry worker, compensation or
restart scan is required. Do not reissue on rebind or ordinary consumption. Normal
consumption asks Quota and uses its actual entitlement result, including refusal when
no entitlement exists. A timeout can still conceal a committed initialization.

Quota 0.17.1, its stable seven-day plan, tenant/subject idempotency, snapshots and paid
preservation remain unchanged. Earlier worker/outbox chain reports are historical
evidence of Quota replay safety, not acceptance requirements for the revised SKZY
consumer. Its superseded staged implementation must not be deployed as the new policy.

Record exact service/client commits, request IDs, redacted responses and acceptance
results on both linked Issues. Keep credentials out of evidence. Stop the temporary
service only after the peer finishes; delete only the task-owned fixture and databases
after preserving useful evidence. MySQL row-lock acceptance and SQLite HTTP acceptance
must be reported separately.
