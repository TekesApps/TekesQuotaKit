# Security Policy

## Supported versions

TekesQuotaKit is pre-1.0. Security fixes are made only for the latest minor release line.

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| < 0.2 | No |

## Reporting a vulnerability

Do not open a public issue, discussion, or pull request for a vulnerability.

Report it privately through GitHub's private vulnerability reporting: go to the
[Security tab](https://github.com/TekesApps/TekesQuotaKit/security) of the repository and choose
**Report a vulnerability**. This creates a private security advisory visible only to you and the
maintainers.

Please include the affected version or commit, a description of the impact, and steps or a test
that reproduces the issue. Redact real keys, tokens, and user data.

## What to expect

- We aim to acknowledge reports within 5 business days.
- We will confirm the issue, agree on a fix and disclosure timeline with you, and keep you
  updated in the advisory.
- Fixes are released as a patch version and disclosed through a GitHub Security Advisory. We
  credit reporters unless you prefer otherwise.

This is a small project maintained on a best-effort basis; these are goals, not contractual
guarantees.

## Deployment security checklist

- Generate `TEKES_QUOTA_ADMIN_KEY` and `TEKES_QUOTA_TOKEN_SECRET` with
  `uv run tekes-quota-kit generate-secrets`. Each must be at least 32 characters, and so must
  every client key. Store them in a secret manager or a protected environment file.
- Keep `TEKES_QUOTA_TOKEN_SECRET` stable. Tokens are derived from it, so rotating it breaks idempotent retries of every earlier `request_key`. There is no rotation procedure.
- Keep the default bind address `127.0.0.1` and expose the service only through a TLS reverse
  proxy reachable by your trusted backends. Restrict `/admin` and `/v1/admin/*` to operators where possible, and give each operator their own
  web admin account.
- Client keys are server credentials. Never embed a client key or the admin key in a browser,
  mobile app, or miniapp.
- Set `X-Subject-ID` only from your backend's own authenticated user, never from unverified client
  input.
- Treat downloaded client integration contracts as secrets: they contain a plaintext client key.
  Keep them out of Git and delete them once the key is stored. Use the **rotate** option if one
  leaks.
- Use one client credential per backend and Service, with the narrowest role (`issuer` or
  `provider`) that works.
- Back up the database before applying migrations, and restrict database access for the account
  in `TEKES_QUOTA_DATABASE_URL`.
