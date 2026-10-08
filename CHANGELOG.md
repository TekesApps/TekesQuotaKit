# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Before 1.0, minor versions may contain breaking changes.

## [Unreleased]

## [0.7.3] - 2026-10-08

### Changed

- When a contract's `api_base_url` is a loopback address, it now explains that this is the
  business system's production server, not a developer's workstation, and how to develop locally.
  The console's API address hint says the same.

- The sidebar caption reads 配额管理平台 without the wide letter-spacing, and shows the version
  of the server that is actually running, read from the session. A browser test checks both.
- `pyproject.toml` is the only place the version is written. The server reads it from the
  installed package metadata (`tekes_quota_kit.__version__`, the OpenAPI version, and the
  `version` field of the admin session); nothing else hard-codes it, and the console no longer
  embeds a version at build time.

## [0.7.2] - 2026-10-08

### Changed

- Rotating a credential no longer opens a form, since nothing in it can change. 轮换密钥 asks for
  confirmation and downloads the new contract directly, using the default API address
  `http://127.0.0.1:9460`. The issue forms lose their rotate checkbox.

### Fixed

- Disabled text inputs, such as a locked user ID definition, looked editable: the copied
  stylesheet forced a white background and normal text on every input. They now use antd's
  disabled colours and cursor, and a browser test checks they match a disabled antd Select.

## [0.7.1] - 2026-10-08

### Changed

- Once a business system has a user ID definition, issuing a credential can no longer change it.
  The console shows it read-only in the issue form and points to the 业务系统 page; the API returns
  409 for a different definition. The 业务系统 page (`PUT /v1/admin/tenants/{tenant}`) is the one
  place to change it.
- The issue form is split by credential type. "签发会员同步凭据" has a fixed role and no Service
  field; "签发服务凭据" offers only the Service roles. Hints for the other type no longer show.

## [0.7.0] - 2026-10-08

### Added

- A "业务系统" page in the console to edit the business system's name and user ID definition
  directly, without issuing a credential.
- Deleting a client credential: `DELETE /v1/admin/tenants/{tenant}/clients/{client_id}` and a
  "删除" action in the console that asks for the Client ID. The key stops working at once.
- A "签发会员同步凭据" button that opens the issue form with the member-sync role selected.

## [0.6.0] - 2026-10-08

### Fixed

- The backup example in `docs/deployment.md` listed only the 11 tables from 0.2 and missed
  `tq_admin_users`, `tq_admin_sessions`, `tq_admin_login_attempts` (0.3.0) and `tq_tenants`
  (0.4.0). It now dumps the whole Kit database, or selects `tq_` tables at backup time. Tests now
  fail if the backup commands hard-code table names or if the fresh-install DDL and the models
  disagree on the set of tables.

### Added

- Member sync: a `membership` client role and `/v1/members` endpoints to set or renew, read, end,
  and batch-import members (up to 500 per call). A business system keeps Kit's member list with its
  own credential instead of the admin key. Provisioning such a client downloads a
  `tekes-quotakit-membership/v1` contract listing the assignable Levels and their Limits. The
  console offers the role as "会员同步".
- Service client contracts now state that only users with an active Level are admitted and that
  others get 409 `no_level`.
- Operators write a user ID definition, such as `user_table.id`, when issuing a credential. It is
  stored on the business system (`tq_tenants.subject_id_definition`), quoted in every contract the
  business system issues and in their JSON as `subject_id_definition`, and prefilled for the next
  credential. Issuing is refused until one is stored. Upgrades apply
  `migrations/add_subject_id_definition_mysql.sql` before starting 0.6.0.
- Both kinds of client contract open with the same user ID rule, also carried as
  `subject_id_rule` in their JSON: the business system picks one stable positive integer per
  person and sends it in member sync and in every Service request, with a go-live self-check.

## [0.5.0] - 2026-10-08

### Added

- `week` Limit period: a natural week from Monday 00:00 to the next Monday 00:00 in the Limit's
  timezone. Available in the API and the console.

## [0.4.0] - 2026-10-08

### Added

- Business system registry (`tq_tenants`) and `PUT /v1/admin/tenants/{tenant}` to register a
  business system with a display name. `GET /v1/admin/tenants` also returns `items` with each
  tenant's name and whether it is registered. Upgrades run `init-schema` or
  `migrations/add_tenants_mysql.sql`.

### Changed

- The console asks for the business system (name and code) on first sign-in, then works in it and
  shows its name in the header. The free-text tenant box is gone, so a typo can no longer start an
  empty tenant. A switcher appears only when several business systems are registered.

### Fixed

- Dropdown text in the console was pushed down and clipped, and editor controls ranged from 38 to
  50px tall. The Shukang admin stylesheet sizes every bare `input`, including antd's internal ones.
  `antd-overrides.css` hands those back to antd and fixes all field controls at 38px.
- CI runs a Playwright layout test that opens every editor and fails if any control is not 38px,
  clips its text, or is off-center by more than 1px.

## [0.3.0] - 2026-10-08

### Added

- Web admin accounts: `tekes-quota-kit admin-user add|passwd|disable|enable|list`, stored in three new
  tables. Upgrades run `init-schema` or `migrations/add_admin_accounts_mysql.sql`.
- Sign-in for the web admin: `POST /v1/admin/session/login`, `GET /v1/admin/session`,
  `POST /v1/admin/session/logout`. PBKDF2-SHA256 passwords, a 12-hour HttpOnly `SameSite=Strict`
  cookie scoped to the admin API path, a 15-minute lock after 5 failed sign-ins, and an
  `X-Admin-Request` header required on cookie-authenticated writes.
- `GET /v1/admin/tenants` lists known tenants for the console's tenant picker.
- CI builds the console and fails if the committed build is stale.

### Changed

- The web admin is rebuilt in React, TypeScript, and antd (source in `admin-web/`), following the
  layout of the Shukang admin: a sign-in page first, then a collapsible sidebar with collapsible
  sections, per-entity pages with editors in a side drawer, and paginated read-only data pages.
  The build is committed under `src/tekes_quota_kit/admin_assets/`, so installs need no Node.
- `/v1/admin/...` accepts a console session as well as the admin key. Scripts using the admin key
  are unaffected.

## [0.2.2] - 2026-10-08

### Fixed

- The admin page works behind a reverse proxy path prefix such as `/user-quota/admin`. Its
  stylesheet and script URLs are relative, and admin API calls use the prefix derived from the
  page path. Serving at `/admin` is unchanged.
- Require `pymysql[rsa]` so MySQL 8 `caching_sha2_password` accounts can connect without TLS.
  Pin PyMySQL below 1.2.1, because 1.2.1 to 1.2.3 crash with `'NoneType' object has no attribute
  'is_auth_switch_request'` on a cold full authentication.

## [0.2.1] - 2026-10-08

### Added

- MIT license.
- Public documentation: rewritten README, HTTP API reference (`docs/api.md`), deployment guide
  (`docs/deployment.md`), CONTRIBUTING.md, SECURITY.md, and this changelog.
- GitHub Actions CI running pytest and ruff.
- Private vulnerability reporting enabled on the repository.

### Changed

- Examples and tests use a generic `demo-tenant` instead of a specific customer.
- Chinese design documents moved to `docs/zh/`.
- Version bumped to 0.2.1 in `pyproject.toml` and the FastAPI app metadata.

### Fixed

- Documentation now describes the real effect of changing `TEKES_QUOTA_TOKEN_SECRET`: issued
  tokens keep working, but idempotent retries of earlier `request_key` values fail.

## [0.2.0] - 2026-09-29

### Added

- Durable redemption: `redeem` charges a per-use Quota once (`charge_units`) and opens a session;
  `POST /v1/use` validates each attempt; `POST /v1/stop` closes the session.
- Composite Services with parent-child membership in `tq_service_members` (optional per-child
  `max_uses`) and per-session child snapshots in `tq_token_items`.
- Session expiry via `duration_seconds` on `redeem` or the Service's `session_ttl_seconds`, and the
  `expire-sessions` CLI command (`--limit`) for cron.
- Level term tracking for `level_term` Limits: assignments store term start and end, accept
  `effective_at` and `renew_term`, and carry usage across a mid-term Level change.
- Web administration at `/admin`: browse all `tq_` tables (hashes hidden) and edit configuration.
- Client provisioning (`POST /v1/admin/clients/{client_id}/provision`) that generates a client key
  and downloads a Markdown integration contract; explicit `rotate` option.
- `generate-secrets` CLI command.
- `migrations/add_composite_services_mysql.sql` for upgrading existing 0.1.0 schemas.

### Changed

- `/v1/begin` and `/v1/close` kept as legacy aliases for the durable flow.

## [0.1.0] - 2026-09-25

### Added

- Initial shared quota guard: Services, Quotas, Levels, Limits (`day`, `month`, `level_term`),
  and subject assignments per tenant.
- Instant per-use redemption with `POST /v1/redeem`.
- Reported-usage admission with `POST /v1/token` and settlement with `POST /v1/token/settle`.
- Refund and token status endpoints, unsettled token listing, and display-only quota balance.
- Idempotent retries through `request_key`.
- Immutable usage ledger and issuer, provider, and consumer client roles.
- `serve` and `init-schema` CLI commands and explicit MySQL DDL.

[Unreleased]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.7.3...HEAD
[0.7.3]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/TekesApps/TekesQuotaKit/compare/f6296dc...v0.2.0
[0.1.0]: https://github.com/TekesApps/TekesQuotaKit/tree/f6296dc
