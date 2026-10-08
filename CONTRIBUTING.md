# Contributing to TekesQuotaKit

Thanks for your interest. TekesQuotaKit is alpha software, so issues and pull requests that
improve correctness, documentation, or tests are especially welcome.

## Development setup

Requirements: Python >= 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/TekesApps/TekesQuotaKit.git
cd TekesQuotaKit
uv sync --all-groups
```

## Running checks

```bash
uv run pytest
uv run ruff check .
```

The default test suite uses SQLite and needs no external services. CI runs the same two commands
on every pull request.

### Optional MySQL integration tests

`tests/test_mysql_integration.py` runs only when `TEKES_QUOTA_TEST_DATABASE_URL` is set. It
creates tables and writes data, so point it at a disposable schema. The database name must start
with `tekes_quota_test_`; the test asserts this before touching anything.

```bash
mysql -e "CREATE DATABASE tekes_quota_test_local CHARACTER SET utf8mb4"
TEKES_QUOTA_TEST_DATABASE_URL='mysql+pymysql://user:password@127.0.0.1:3306/tekes_quota_test_local?charset=utf8mb4' \
  uv run pytest tests/test_mysql_integration.py
```

Never point it at an application or production database.

### Web admin

The console source is in `admin-web/` (React, TypeScript, antd, Vite). Its build output is committed
under `src/tekes_quota_kit/admin_assets/`, and CI fails if that output is stale. After changing the
console, rebuild and commit both:

```bash
cd admin-web
npm ci
npm run build
```

`npm run dev` serves the console with hot reload and proxies `/v1` to a Kit running on
`127.0.0.1:9460`. Create a local account with `uv run tekes-quota-kit admin-user add --username dev`.

## Code style

- Ruff with line length 100 and rule sets `E`, `F`, `I`, `UP`, `B` (see `pyproject.toml`).
- Target Python 3.12. Use type hints and keep the dependency list small.
- Database changes must stay portable between MySQL and SQLite. Schema changes need matching
  updates to `migrations/create_tables_mysql.sql` and, for existing installs, a new one-time
  migration file.

## Pull requests

- Add or update tests for every behavior change.
- Keep `README.md`, `docs/`, and `CHANGELOG.md` (under `[Unreleased]`) in sync with the change.
- Keep pull requests focused; explain the motivation and any API or schema impact.
- Never commit `.env` files, secrets, or downloaded client integration contracts
  (`*-quotakit.md`), which contain plaintext client keys.
- Make sure `uv run pytest` and `uv run ruff check .` pass.

## Reporting bugs

Open a [GitHub issue](https://github.com/TekesApps/TekesQuotaKit/issues) with:

- TekesQuotaKit version or commit, Python version, and database (MySQL version or SQLite).
- The requests you sent (redact keys and tokens), the responses, and what you expected.
- A minimal reproduction, ideally as a failing test.

Do not report security vulnerabilities in public issues. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
