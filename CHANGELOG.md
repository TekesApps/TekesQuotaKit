# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Before 1.0, minor versions may contain breaking changes.

## [Unreleased]

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

[Unreleased]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/TekesApps/TekesQuotaKit/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/TekesApps/TekesQuotaKit/compare/f6296dc...v0.2.0
[0.1.0]: https://github.com/TekesApps/TekesQuotaKit/tree/f6296dc
