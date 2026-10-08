# Deploying TekesQuotaKit

This guide covers running TekesQuotaKit 0.2.0 in production: requirements, configuration, schema setup, process management, scheduled maintenance, operations, and a security checklist. For the HTTP interface see [api.md](api.md).

## Requirements

| Component | Version / notes |
| --- | --- |
| Python | 3.12 or newer (`requires-python = ">=3.12"`) |
| uv | Used to install dependencies from `uv.lock` and run the CLI |
| MySQL | 8.x, accessed through PyMySQL (`mysql+pymysql://` URLs). Use the InnoDB engine and `utf8mb4`. |
| PyMySQL | Pinned to `pymysql[rsa]>=1.1,<1.2.1`. The `rsa` extra installs `cryptography`, which MySQL 8's default `caching_sha2_password` needs on connections without TLS. PyMySQL 1.2.1 to 1.2.3 crash on the first full authentication of that kind. If you install outside `uv.lock`, keep this pin. |
| SQLite | Supported by the code only for tests and local experiments. Do not use it in production. |

The reviewed DDL in `migrations/create_tables_mysql.sql` does not specify `ENGINE` or `CHARSET`; tables inherit the defaults of the schema they are created in. On a stock MySQL 8 server that is InnoDB with `utf8mb4`. Check your schema defaults before applying it. InnoDB is required: correctness depends on row locks (`SELECT ... FOR UPDATE`), unique constraints, and foreign keys. Timestamps are stored as `DATETIME(6)` in UTC.

## Install

```sh
git clone https://github.com/TekesApps/TekesQuotaKit.git /opt/TekesQuotaKit
cd /opt/TekesQuotaKit
uv sync --frozen --no-dev
```

## Configuration

The CLI reads configuration from environment variables only. It does not load `.env` files on its own; export the variables, use systemd `EnvironmentFile=`, or run `uv run --env-file .env ...`. `.env.example` lists all of them.

| Variable | Required by | Default | Meaning |
| --- | --- | --- | --- |
| `TEKES_QUOTA_DATABASE_URL` | `serve`, `init-schema`, `expire-sessions` | none | SQLAlchemy URL. Must start with `mysql+pymysql://` (or `sqlite:///` for tests). Example: `mysql+pymysql://user:password@127.0.0.1:3306/your_app?charset=utf8mb4`. |
| `TEKES_QUOTA_TOKEN_SECRET` | `serve`, `init-schema`, `expire-sessions` | none | HMAC secret used to derive tokens. At least 32 characters, otherwise startup fails with `weak_secret`. Must stay stable (see below). |
| `TEKES_QUOTA_ADMIN_KEY` | `serve` | none | Bearer key for the admin API and admin UI. At least 32 characters, otherwise `serve` refuses to start. |
| `TEKES_QUOTA_HOST` | `serve` | `127.0.0.1` | Bind address. |
| `TEKES_QUOTA_PORT` | `serve` | `9460` | Bind port. |

Generate both secrets once:

```sh
uv run tekes-quota-kit generate-secrets
```

This prints `TEKES_QUOTA_ADMIN_KEY=...` and `TEKES_QUOTA_TOKEN_SECRET=...`, each 64 URL-safe characters. It needs no other configuration and does not touch the database.

Client keys (created through the admin API) must also be at least 32 characters; provisioned keys are generated with the same length as above.

### Why the token secret must stay stable

A token is `tq_` plus HMAC-SHA256(token secret, tenant, client_id, subject_id, service_code, request_key). This is how a retry with the same `request_key` finds the original token instead of charging again. Only a hash of the token is stored.

If the secret changes, tokens already handed out still work by value (they are looked up by hash), but any retry of a `request_key` created before the change derives a different token. The server can no longer match it to the original record, and the request fails with a server error because the unique constraint on `(tenant_id, issuer_client_id, subject_id, service_code, request_key)` rejects the new row. Treat the token secret as permanent for the life of the database. There is no supported rotation procedure.

## Schema setup

TekesQuotaKit creates and uses only tables prefixed `tq_` (11 tables: `tq_clients`, `tq_levels`, `tq_quotas`, `tq_limits`, `tq_assignments`, `tq_services`, `tq_service_members`, `tq_usage`, `tq_tokens`, `tq_token_items`, `tq_ledger`). It never creates a database or switches schemas, so it can live inside an existing application schema or in a dedicated one. The database user needs DML on the `tq_` tables, plus DDL rights while you create them.

### Fresh install

Pick exactly one of these. Never run both against the same schema.

- **Let the CLI create the tables:**

  ```sh
  uv run tekes-quota-kit init-schema
  ```

  This runs SQLAlchemy `create_all`, which creates missing `tq_` tables and leaves existing ones untouched.

- **Apply the reviewed DDL** if your process requires DBA review of schema changes:

  ```sh
  mysql --default-character-set=utf8mb4 your_app < migrations/create_tables_mysql.sql
  ```

### Upgrading a 0.1.x install

0.2.0 adds composite and durable Services. Existing tables need new columns and two new tables.

1. Back up the schema (at least all `tq_` tables).
2. Apply `migrations/add_composite_services_mysql.sql` once. It alters `tq_services`, `tq_assignments`, and `tq_tokens`, backfills assignment terms, and creates `tq_service_members` and `tq_token_items`. Existing atomic Services and tokens keep their behavior. Do not rerun it after it succeeds.
3. Deploy the new code and restart.

Do not rely on `init-schema` for upgrades. It creates missing tables but does not alter existing ones, so it would create the two new tables while leaving the old tables without the new columns.

### Upgrading a 0.2.x install

The web admin sign-in adds three tables (`tq_admin_users`, `tq_admin_sessions`, `tq_admin_login_attempts`) and changes no existing table. Run `tekes-quota-kit init-schema`, or apply `migrations/add_admin_accounts_mysql.sql` once. Then create the first account, as below.

### Upgrading a 0.3.x install

0.4.0 adds one table, `tq_tenants`, for the business system the console asks for on first sign-in. Run `tekes-quota-kit init-schema`, or apply `migrations/add_tenants_mysql.sql` once. No existing table changes.

### Web admin accounts

The console has no sign-up. Create accounts on the server, with the same environment as `serve`:

```bash
tekes-quota-kit admin-user add --username alice
```

It prompts twice for a password of at least 12 characters. For automation, pass `--password-stdin` and write one line to standard input. `admin-user passwd`, `disable`, `enable`, and `list` manage existing accounts; `passwd` and `disable` sign the account out everywhere.

## Running in production

`tekes-quota-kit serve` starts a single uvicorn process bound to `127.0.0.1:9460` by default. Keep it on localhost and terminate TLS in a reverse proxy (nginx, Caddy, or similar) in front of it.

### systemd service

`/etc/tekes-quota-kit.env` (mode 0600, owned by root or the service user):

```ini
TEKES_QUOTA_DATABASE_URL=mysql+pymysql://tq:change-me@127.0.0.1:3306/your_app?charset=utf8mb4
TEKES_QUOTA_ADMIN_KEY=...
TEKES_QUOTA_TOKEN_SECRET=...
TEKES_QUOTA_HOST=127.0.0.1
TEKES_QUOTA_PORT=9460
```

`/etc/systemd/system/tekes-quota-kit.service`:

```ini
[Unit]
Description=TekesQuotaKit
After=network-online.target
Wants=network-online.target

[Service]
User=tekes-quota
WorkingDirectory=/opt/TekesQuotaKit
EnvironmentFile=/etc/tekes-quota-kit.env
ExecStart=/usr/local/bin/uv run --frozen --no-dev tekes-quota-kit serve
Restart=on-failure
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Adjust the `uv` path to where it is installed (`command -v uv`). Run `uv sync --frozen --no-dev` as the service user after each deploy so the first start does not need to resolve dependencies.

### Reverse proxy (nginx)

```nginx
server {
    listen 443 ssl;
    server_name quota.example.com;
    ssl_certificate     /etc/ssl/quota.example.com.pem;
    ssl_certificate_key /etc/ssl/quota.example.com.key;

    # Admin UI and admin API: internal networks only.
    location ~ ^/(admin|v1/admin)(/|$) {
        allow 10.0.0.0/8;
        deny all;
        proxy_pass http://127.0.0.1:9460;
    }

    location / {
        proxy_pass http://127.0.0.1:9460;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

`/docs` and `/openapi.json` are also served without authentication. Restrict them the same way if you do not want the API surface published.

### Admin page under a path prefix

The admin page can share a host with another application, for example at `https://api.example.com/user-quota/admin`. Its assets use relative URLs, and the console derives the prefix from the page path, so the proxy only has to strip the prefix. The console also sends that prefix as `X-Admin-Base`, which scopes the session cookie to `/user-quota/v1/admin` so other applications on the host never receive it. The page must be opened without a trailing slash; the first block below redirects `/user-quota/admin/` for that reason.

```nginx
# Inside the existing server block, before "location /".
location = /user-quota/admin/ { return 301 /user-quota/admin; }
location = /user-quota/admin { proxy_pass http://127.0.0.1:9460/admin; include /etc/nginx/user-quota-proxy.conf; }
location ^~ /user-quota/admin/ { proxy_pass http://127.0.0.1:9460/admin/; include /etc/nginx/user-quota-proxy.conf; }
location ^~ /user-quota/v1/admin/ { proxy_pass http://127.0.0.1:9460/v1/admin/; include /etc/nginx/user-quota-proxy.conf; }
# Consumer endpoints are not published here; trusted backends call 127.0.0.1:9460 directly.
location ^~ /user-quota/ { return 404; }
```

`/etc/nginx/user-quota-proxy.conf`:

```nginx
allow 203.0.113.10;   # operator network or VPN egress
deny all;
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto https;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Real-IP $remote_addr;
```

The `allow`/`deny` lines are optional. The console requires a signed-in account and locks a username for 15 minutes after 5 failed sign-ins, but nothing limits how often the admin key itself can be tried. If you publish the admin API without an allowlist, add a per-IP `limit_req` on `/user-quota/v1/admin/`. Size it for the console: one page change makes about ten admin API calls, so allow at least 120 requests per minute with a burst of 60.

### Container

A minimal image:

```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev
ENV TEKES_QUOTA_HOST=0.0.0.0 TEKES_QUOTA_PORT=9460
EXPOSE 9460
CMD ["uv", "run", "--frozen", "--no-dev", "tekes-quota-kit", "serve"]
```

`README.md` and `LICENSE` are copied because the package build reads them. Pin the uv image to a specific version instead of `latest` in your own builds. Pass the database URL and secrets at runtime (for example `docker run --env-file`), never with `ENV` in the image.

`TEKES_QUOTA_HOST=0.0.0.0` is only appropriate inside a container, where the port is reachable only on the container network or through an explicit port mapping to the proxy. Do not publish port 9460 to the internet; keep the TLS proxy in front.

## Scheduled job: expiring durable sessions

Durable sessions with a TTL get an `expires_at`. Run this every few minutes:

```sh
uv run tekes-quota-kit expire-sessions --limit 500
```

It marks up to `--limit` sessions (1 to 5000, default 500) that are still `open` but past `expires_at` as `expired`, and marks their unused child grants expired. It prints `Expired N composite sessions`. If more than `--limit` sessions are due, the next run picks up the rest.

What happens if it never runs: expiry is still enforced. `/v1/use` compares `expires_at` with the current time on every call and rejects expired sessions with `session_not_open`, and `/v1/stop` or `/v1/close` on an expired session records `expired`. What does not happen is the state change in the database: `tq_tokens.session_status` stays `open` and `tq_token_items` stay `available` until the job runs or the session is stopped. Reporting, the admin table browser, and any query you run against the tables will show those sessions as open. Expiry never refunds Quota in either case.

systemd timer:

```ini
# /etc/systemd/system/tekes-quota-expire.service
[Unit]
Description=Expire TekesQuotaKit durable sessions

[Service]
Type=oneshot
User=tekes-quota
WorkingDirectory=/opt/TekesQuotaKit
EnvironmentFile=/etc/tekes-quota-kit.env
ExecStart=/usr/local/bin/uv run --frozen --no-dev tekes-quota-kit expire-sessions --limit 500
```

```ini
# /etc/systemd/system/tekes-quota-expire.timer
[Unit]
Description=Run TekesQuotaKit session expiry every 5 minutes

[Timer]
OnCalendar=*:0/5
Persistent=true

[Install]
WantedBy=timers.target
```

Enable with `systemctl enable --now tekes-quota-expire.timer`.

Or cron (as the service user):

```cron
*/5 * * * * cd /opt/TekesQuotaKit && set -a && . /etc/tekes-quota-kit.env && set +a && /usr/local/bin/uv run --frozen --no-dev tekes-quota-kit expire-sessions --limit 500
```

## Operations

### Health check

`GET /health` returns `{"status": "ok"}` with status 200. It confirms the process is serving HTTP; it does not query the database. To check database connectivity, call an authenticated endpoint such as `GET /v1/admin/tables/tq_levels?tenant=<tenant>&limit=1` from an internal host.

### Reconciliation of reported usage

For `reported_usage` Quotas, `POST /v1/token` only admits an operation; usage is counted when a provider settles it. Providers should periodically call `GET /v1/tokens/unsettled` with their client key. It lists `admitted` tokens for that client's tenant and Service as `token_hash`, `subject_id`, and `admitted_at`. Match `token_hash` against the SHA-256 of tokens your backend recorded and settle or investigate any stragglers. Unsettled tokens never count against the Limit.

### Backups

All state is in the `tq_` tables (15 as of 0.4.0, including the console accounts and the business system registry). Do not hard-code the table list: new versions add tables, and a stale list silently drops them from the backup.

If the Kit has its own database, dump the whole database:

```sh
mysqldump --single-transaction --default-character-set=utf8mb4 user_quota > tq-backup.sql
```

If it shares your application's schema, select the tables by prefix at backup time:

```sh
tables=$(mysql -N -e "SHOW TABLES LIKE 'tq\_%'" your_app)
mysqldump --single-transaction --default-character-set=utf8mb4 your_app $tables > tq-backup.sql
```

After a restore, check that every table is back: `SHOW TABLES LIKE 'tq\_%'` should list the same tables as `migrations/create_tables_mysql.sql`. Console sessions and login attempts may be dropped from a restore without harm; operators simply sign in again.

`tq_ledger` records every `consume` and `refund` with its unit delta and is the audit trail for `tq_usage`. Always take a backup before applying a migration. Back up the token secret separately in your secret manager; a restored database is only fully usable with the same secret.

### Key rotation

| Secret | How to rotate | Effect |
| --- | --- | --- |
| Admin key | Change `TEKES_QUOTA_ADMIN_KEY` and restart `serve`. | Old key stops working after restart for scripts. Console sessions are unaffected. |
| Console password | `tekes-quota-kit admin-user passwd --username NAME`. | The account's sessions end immediately. |
| Client key | `POST /v1/admin/clients/{client_id}/provision` with `"rotate": true` (or `PUT /v1/admin/clients/{client_id}` with a new key). | The new key replaces the stored hash in one transaction; the old key is rejected immediately. The client backend gets 401 until it is updated with the new key. Tokens already issued stay valid because they are bound to `client_id`, not to the key. |
| Token secret | Not rotatable. | See "Why the token secret must stay stable". |

## Security checklist

- Keep `serve` bound to `127.0.0.1` (or a private container network) and expose it only through a TLS proxy.
- Prefer restricting `/admin` and `/v1/admin/...` to a VPN or internal network at the proxy. If the console must be public, keep it on HTTPS so the session cookie is `Secure`, give each operator their own account, and rate limit the admin API per IP as described above.
- Store `TEKES_QUOTA_ADMIN_KEY`, `TEKES_QUOTA_TOKEN_SECRET`, and the database password in a secret store or a root-readable environment file, never in the repository or image.
- Client keys belong to trusted backends only. Never ship a client key to a browser, mobile app, or miniapp.
- Only trusted backends set `subject_id` and `X-Subject-ID`, taken from their own authenticated session. Never forward a subject ID supplied by an end user.
- The provision endpoint returns a Markdown contract containing the plaintext client key. Move the key into the backend's secret store, then delete the file or keep it outside version control. Do not commit it.
- Give each backend its own client with the narrowest role it needs (`issuer` or `provider` rather than `consumer` when one side suffices).
- Use a database account limited to the schema that holds the `tq_` tables.
