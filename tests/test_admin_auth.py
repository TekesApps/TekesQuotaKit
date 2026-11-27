from __future__ import annotations

import io
import sys
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from tekes_quota_kit import admin_auth
from tekes_quota_kit.admin_auth import AdminAccountError, AdminAccounts
from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaKit, utc_now
from tekes_quota_kit.main import run
from tekes_quota_kit.models import AdminSession, Base

ADMIN_KEY = "admin-auth-management-key-for-tests-000001"
TOKEN_SECRET = "admin-auth-token-secret-for-tests-0000001"
PASSWORD = "Horse!Battery17"
GUARD = {"X-Admin-Request": "1"}


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    # Production uses 600k PBKDF2 rounds; tests only need the format, not the cost.
    monkeypatch.setattr(admin_auth, "PBKDF2_ROUNDS", 1000)


@pytest.fixture
def env(tmp_path):
    kit = QuotaKit(f"sqlite:///{tmp_path / 'auth.db'}", TOKEN_SECRET)
    Base.metadata.create_all(kit.engine)
    accounts = AdminAccounts(kit)
    accounts.create("ops", PASSWORD, "system_admin")
    accounts.create("sec", PASSWORD, "security_admin")
    accounts.create("review", PASSWORD, "auditor")
    return kit, accounts, TestClient(create_app(kit))


def login(client, username="ops", password=PASSWORD, headers=None):
    return client.post(
        "/v1/admin/session/login",
        json={"username": username, "password": password},
        headers={**GUARD, **(headers or {})},
    )


def test_login_sets_strict_httponly_cookie_scoped_to_admin_api(env):
    _kit, _accounts, client = env
    response = login(client)
    assert response.status_code == 200
    assert response.json()["data"]["username"] == "ops"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("tq_admin_session=")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/v1/admin" in cookie
    assert "Secure" not in cookie
    assert client.get("/v1/admin/session").json()["data"]["username"] == "ops"


def test_cookie_path_and_secure_follow_the_proxy(env):
    _kit, _accounts, client = env
    response = login(client, headers={"X-Admin-Base": "/user-quota", "X-Forwarded-Proto": "https"})
    cookie = response.headers["set-cookie"]
    assert "Path=/user-quota/v1/admin" in cookie and "Secure" in cookie
    assert login(client, headers={"X-Admin-Base": "/x;/y"}).status_code == 400
    assert login(client, headers={"X-Admin-Base": "relative"}).status_code == 400


def test_login_requires_guard_header_and_valid_credentials(env):
    _kit, _accounts, client = env
    no_guard = client.post(
        "/v1/admin/session/login", json={"username": "ops", "password": PASSWORD}
    )
    assert no_guard.status_code == 403
    assert login(client, password="wrong password here").status_code == 401
    assert login(client, username="nobody").status_code == 401
    assert client.get("/v1/admin/session").status_code == 401


def test_five_failures_lock_the_username_for_fifteen_minutes(env):
    _kit, _accounts, client = env
    for _ in range(5):
        assert login(client, password="wrong password here").status_code == 401
    locked = login(client)
    assert locked.status_code == 429
    assert "15" in locked.json()["detail"]


def test_session_cookie_authorizes_admin_api_and_writes_need_guard(env):
    _kit, _accounts, client = env
    login(client)
    assert client.get("/v1/admin/tables").status_code == 200
    unguarded = client.put("/v1/admin/tenants/demo-tenant/levels/basic", json={})
    assert unguarded.status_code == 403
    guarded = client.put("/v1/admin/tenants/demo-tenant/levels/basic", json={}, headers=GUARD)
    assert guarded.status_code == 200
    assert client.get("/v1/admin/tenants").json()["tenants"] == ["demo-tenant"]


def test_logout_revokes_the_session(env):
    _kit, _accounts, client = env
    login(client)
    assert client.post("/v1/admin/session/logout", headers=GUARD).status_code == 200
    assert client.get("/v1/admin/session").status_code == 401
    assert client.get("/v1/admin/tables").status_code == 401


def test_password_change_disable_and_expiry_end_sessions(env):
    kit, accounts, client = env
    login(client)
    accounts.set_password("ops", "Another-Long27")
    assert client.get("/v1/admin/session").status_code == 401
    assert login(client).status_code == 401
    assert login(client, password="Another-Long27").status_code == 200
    accounts.set_status("ops", "disabled")
    assert client.get("/v1/admin/session").status_code == 401
    assert login(client, password="Another-Long27").status_code == 401
    accounts.set_status("ops", "active")
    login(client, password="Another-Long27")
    with kit.sessions.begin() as db:
        db.execute(update(AdminSession).values(expires_at=utc_now() - timedelta(seconds=1)))
    assert client.get("/v1/admin/session").status_code == 401


def test_shared_admin_key_is_retired_and_a_bad_bearer_is_not_rescued_by_cookie(env):
    _kit, _accounts, client = env
    bearer = {"Authorization": f"Bearer {ADMIN_KEY}"}
    assert client.get("/v1/admin/session", headers=bearer).status_code == 401
    token = _accounts.login("ops", PASSWORD)
    bearer = {"Authorization": f"Bearer {token}"}
    # Bearer callers are not browsers, so they need no guard header.
    assert client.put("/v1/admin/tenants/t/levels/l", json={}, headers=bearer).status_code == 200
    login(client)
    wrong = {"Authorization": "Bearer not-the-admin-key"}
    assert client.get("/v1/admin/tables", headers=wrong).status_code == 401


def test_account_rules(tmp_path):
    kit = QuotaKit(f"sqlite:///{tmp_path / 'rules.db'}", TOKEN_SECRET)
    Base.metadata.create_all(kit.engine)
    accounts = AdminAccounts(kit)
    with pytest.raises(AdminAccountError):
        accounts.create("ops", "short", "system_admin")
    with pytest.raises(AdminAccountError):
        accounts.create("bad name", PASSWORD, "system_admin")
    accounts.create("ops", PASSWORD, "system_admin")
    with pytest.raises(AdminAccountError):
        accounts.create("ops", PASSWORD, "system_admin")
    with pytest.raises(AdminAccountError):
        accounts.set_password("missing", PASSWORD)
    stored = admin_auth.hash_password(PASSWORD)
    assert stored.startswith("pbkdf2_sha256$1000$")
    assert admin_auth.verify_password(PASSWORD, stored)
    assert not admin_auth.verify_password("nope", stored)
    assert not admin_auth.verify_password(PASSWORD, "garbage")


def test_cli_manages_admin_users(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("TEKES_QUOTA_DATABASE_URL", url)
    monkeypatch.setenv("TEKES_QUOTA_TOKEN_SECRET", TOKEN_SECRET)
    Base.metadata.create_all(QuotaKit(url, TOKEN_SECRET).engine)

    def cli(*args, stdin=""):
        monkeypatch.setattr(sys, "argv", ["tekes-quota-kit", *args])
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        with pytest.raises(SystemExit) as exit_info:
            run()
        return exit_info.value.code, capsys.readouterr()

    code, out = cli(
        "admin-user",
        "add",
        "--role",
        "system_admin",
        "--username",
        "ops",
        "--password-stdin",
        stdin=PASSWORD + "\n",
    )
    assert code == 0 and "Created admin user ops" in out.out
    code, out = cli(
        "admin-user",
        "add",
        "--role",
        "system_admin",
        "--username",
        "ops",
        "--password-stdin",
        stdin=PASSWORD + "\n",
    )
    assert code == 1 and "already exists" in out.err
    code, out = cli("admin-user", "list")
    assert code == 0 and out.out.startswith("system_admin\tops\tactive\tlast login -")
    code, out = cli("admin-user", "disable", "--username", "ops")
    assert code == 0 and "Disabled ops" in out.out
    code, out = cli("admin-user", "passwd", "--username", "ops", "--password-stdin", stdin="x\n")
    assert code == 1 and "at least 12" in out.err


def test_register_business_system_and_list_it(env):
    _kit, _accounts, client = env
    login(client)
    assert client.get("/v1/admin/tenants").json() == {"tenants": [], "items": []}
    created = client.put(
        "/v1/admin/tenants/shukang-zhiyi", json={"name": "数康智医"}, headers=GUARD
    )
    assert created.status_code == 200
    assert created.json() == {
        "tenant_id": "shukang-zhiyi",
        "name": "数康智医",
        "subject_id_definition": None,
    }
    # A tenant used only by API scripts is listed but marked unregistered.
    client.put("/v1/admin/tenants/legacy/levels/basic", json={}, headers=GUARD)
    assert client.get("/v1/admin/tenants").json()["items"] == [
        {"tenant_id": "legacy", "name": None, "registered": False, "subject_id_definition": None},
        {
            "tenant_id": "shukang-zhiyi",
            "name": "数康智医",
            "registered": True,
            "subject_id_definition": None,
        },
    ]
    renamed = client.put("/v1/admin/tenants/shukang-zhiyi", json={"name": " 数康 "}, headers=GUARD)
    assert renamed.json()["name"] == "数康"
    assert (
        client.put("/v1/admin/tenants/bad code", json={"name": "x"}, headers=GUARD).status_code
        == 400
    )
    assert (
        client.put("/v1/admin/tenants/" + "a" * 65, json={"name": "x"}, headers=GUARD).status_code
        == 400
    )
    assert client.put("/v1/admin/tenants/ok", json={"name": "  "}, headers=GUARD).status_code == 400
    assert client.put("/v1/admin/tenants/ok", json={"name": "x"}).status_code == 403


def test_session_reports_the_installed_version(env):
    import re
    from pathlib import Path

    from tekes_quota_kit import __version__

    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    assert __version__ == declared
    _kit, _accounts, client = env
    assert login(client).json()["data"]["version"] == declared
    assert client.get("/v1/admin/session").json()["data"]["version"] == declared
    assert client.get("/openapi.json").json()["info"]["version"] == declared


@pytest.mark.parametrize(
    "weak",
    [
        "Abcdef1!",
        "abcdefghijk9!",
        "ABCDEFGHIJK9!",
        "Abcdefghijk!",
        "Abcdefghijk9",
        "Abcdefghijk9 ",
        "Abc!SecureOPS42",
        "Abcd!1234567" + "x" * 117,
    ],
)
def test_creation_rotation_and_recovery_apply_the_same_policy(env, weak):
    _kit, accounts, client = env
    with pytest.raises(AdminAccountError):
        accounts.create("op", weak, "system_admin")
    with pytest.raises(AdminAccountError):
        accounts.set_password("ops", weak)
    assert client.post(
        "/v1/admin/session/login",
        headers=GUARD,
        json={"username": "ops", "password": PASSWORD, "new_password": weak},
    ).status_code in (400, 422)
    assert login(client).status_code == 200


def test_deadline_rotation_and_active_session_revocation(env, monkeypatch):
    from sqlalchemy import select

    from tekes_quota_kit.models import AdminUser

    kit, accounts, client = env
    with kit.sessions() as db:
        start = db.scalar(select(AdminUser.password_changed_at).where(AdminUser.username == "ops"))
    deadline = start + timedelta(days=90)
    monkeypatch.setattr(admin_auth, "utc_now", lambda: deadline - timedelta(seconds=1))
    assert login(client).json()["data"]["expires_at"] == deadline.isoformat() + "Z"
    bearer = {"Authorization": "Bearer " + accounts.login("ops", PASSWORD)}
    monkeypatch.setattr(admin_auth, "utc_now", lambda: deadline)
    assert client.get("/v1/admin/tables", headers=bearer).status_code == 401
    assert login(client).status_code == 403

    def rotate(current, new):
        return client.post(
            "/v1/admin/session/login",
            headers=GUARD,
            json={"username": "ops", "password": current, "new_password": new},
        )

    assert rotate(PASSWORD, PASSWORD).status_code == 400
    assert rotate("invalid", "Renewed!Private15").status_code == 401
    assert rotate(PASSWORD, "Renewed!Private15").status_code == 200
    assert client.get("/v1/admin/tables").status_code == 200
    assert client.get("/v1/admin/tables", headers=bearer).status_code == 401
    assert login(client).status_code == 401


def test_roles_enforce_revoke_and_audit_operations(env):
    _, accounts, client = env
    accounts.check_roster()
    system = {"Authorization": "Bearer " + accounts.login("ops", PASSWORD)}
    security = {"Authorization": "Bearer " + accounts.login("sec", PASSWORD)}
    auditor = {"Authorization": "Bearer " + accounts.login("review", PASSWORD)}
    for headers in (auditor, security):
        assert (
            client.put("/v1/admin/tenants/test/levels/base", headers=headers, json={}).status_code
            == 403
        )
    assert (
        client.put("/v1/admin/tenants/test/levels/base", headers=system, json={}).status_code == 200
    )
    for headers in (auditor, system):
        assert client.post("/v1/admin/operators/ops/revoke", headers=headers).status_code == 403
    assert client.post("/v1/admin/operators/ops/revoke", headers=security).status_code == 200
    assert client.get("/v1/admin/session", headers=system).status_code == 401
    assert client.get("/v1/admin/audit/operations", headers=security).status_code == 403
    rows = client.get("/v1/admin/audit/operations", headers=auditor).json()["data"]
    assert any(
        r["actor"] == "ops" and r["target"] == "test/base" and r["status"] == 200 for r in rows
    )
    assert any(r["actor"] == "sec" and "revoke" in r["operation"] for r in rows)
    for headers in (security, auditor):
        res = client.put(
            "/v1/admin/operators/ops/status", json={"status": "disabled"}, headers=headers
        )
        assert res.status_code == (200 if headers is security else 403)
    with pytest.raises(AdminAccountError, match="system_admin"):
        accounts.check_roster()
    assert client.get("/v1/admin/audit/sign-ins", headers=auditor).status_code == 200


def test_sqlite_upgrade_requires_named_enrollment_and_rejects_old_sessions(tmp_path):
    import sqlite3
    from pathlib import Path

    database = tmp_path / "legacy.db"
    old = admin_auth.hash_password(PASSWORD)
    assert admin_auth.hash_password(PASSWORD) != old
    now = utc_now()
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE tq_admin_users (
                id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, status TEXT,
                created_at DATETIME, updated_at DATETIME,last_login_at DATETIME);
            CREATE TABLE tq_admin_sessions (
                id INTEGER PRIMARY KEY, token_hash TEXT,admin_user_id INTEGER,
                created_at DATETIME, expires_at DATETIME, revoked_at DATETIME);
            CREATE TABLE tq_admin_login_attempts (
                id INTEGER PRIMARY KEY, username TEXT, success BOOLEAN, attempted_at DATETIME);
            """)
        db.execute(
            "INSERT INTO tq_admin_users VALUES (1,'legacy',?,'active',?,?,NULL)",
            (old, now.isoformat(), now.isoformat()),
        )
        db.execute(
            "INSERT INTO tq_admin_sessions VALUES (1,?,1,?,?,NULL)",
            (
                admin_auth._token_hash("old-session"),
                now.isoformat(),
                (now + timedelta(hours=12)).isoformat(),
            ),
        )
        migration = (
            Path(__file__).resolve().parents[1] / "migrations/add_operator_security_sqlite.sql"
        )
        db.executescript(migration.read_text())
    kit = QuotaKit(f"sqlite:///{database}", TOKEN_SECRET)
    accounts = AdminAccounts(kit)
    client = TestClient(create_app(kit))
    assert login(client, "legacy").status_code == 401
    assert accounts.session_user("old-session") is None
    with pytest.raises(AdminAccountError):
        accounts.set_password("legacy", PASSWORD, enroll_role="auditor")
    accounts.set_password("legacy", "Fresh!Secret27", enroll_role="auditor")
    assert login(client, "legacy", "Fresh!Secret27").json()["data"]["role"] == "auditor"
    assert accounts.session_user("old-session") is None


def test_bootstrap_requires_rotation_and_preserves_existing_accounts(tmp_path, monkeypatch):
    from tekes_quota_kit.admin_auth import ROLES
    from tekes_quota_kit.operator_bootstrap import bootstrap

    kit = QuotaKit(f"sqlite:///{tmp_path / 'bootstrap.db'}", TOKEN_SECRET)
    Base.metadata.create_all(kit.engine)
    accounts = AdminAccounts(kit)
    directory = tmp_path / "handoff"
    assert bootstrap(accounts, directory) == list(ROLES)
    client = TestClient(create_app(kit))
    passwords = {role: (directory / f"{role}.txt").read_text().strip() for role in ROLES}
    assert len(set(passwords.values())) == 3
    assert directory.stat().st_mode & 0o777 == 0o700
    for role, password in passwords.items():
        assert len(password) >= 24
        assert (directory / f"{role}.txt").stat().st_mode & 0o777 == 0o600
        assert login(client, role, password).status_code == 403
        assert client.get("/v1/admin/session").status_code == 401

    with pytest.raises(AdminAccountError, match="Awaiting initial password change"):
        accounts.check_roster()

    def rotate(role):
        return client.post(
            "/v1/admin/session/login",
            json={
                "username": role,
                "password": passwords[role],
                "new_password": PASSWORD,
            },
            headers=GUARD,
        )

    assert rotate("system_admin").json()["data"]["role"] == "system_admin"
    assert login(client, "system_admin", passwords["system_admin"]).status_code == 401
    accounts.set_status("auditor", "disabled")
    for path in directory.iterdir():
        path.unlink()
    directory.rmdir()
    assert bootstrap(accounts, directory) == []
    assert not directory.exists()
    assert login(client, "system_admin", PASSWORD).status_code == 200
    assert rotate("auditor").status_code == 401
    monkeypatch.setattr(admin_auth, "utc_now", lambda: utc_now() + timedelta(days=92))
    with pytest.raises(AdminAccountError, match="Awaiting initial password change: security_admin"):
        accounts.check_roster()
    assert login(client, "security_admin", passwords["security_admin"]).status_code == 403
    assert rotate("security_admin").status_code == 200
    assert login(client, "security_admin", PASSWORD).status_code == 200


def test_bootstrap_conflict_and_failed_delivery_are_atomic(env, tmp_path):
    from tekes_quota_kit.operator_bootstrap import bootstrap

    _kit, accounts, _client = env
    directory = tmp_path / "handoff"
    directory.mkdir(mode=0o700)
    stale = directory / "auditor.txt"
    stale.write_text("existing delivery")
    with pytest.raises(AdminAccountError, match="Credential file already exists"):
        bootstrap(accounts, directory)
    assert list(directory.iterdir()) == [stale]
    assert stale.read_text() == "existing delivery"
    assert {u["username"] for u in accounts.list()} == {"sec", "ops", "review"}
    accounts.create("auditor", PASSWORD, "system_admin")
    with pytest.raises(AdminAccountError, match="conflicts with role name auditor"):
        bootstrap(accounts, directory)
    assert {u["username"] for u in accounts.list()} == {"sec", "ops", "review", "auditor"}


def test_bootstrap_provisions_missing_names_only(env, tmp_path):
    from tekes_quota_kit.operator_bootstrap import bootstrap

    _kit, accounts, _client = env
    accounts.create("auditor", PASSWORD, "auditor")
    accounts.set_status("auditor", "disabled")
    directory = tmp_path / "handoff"
    directory.mkdir(mode=0o755)
    with pytest.raises(AdminAccountError, match="0700"):
        bootstrap(accounts, directory)
    directory.chmod(0o700)
    assert bootstrap(accounts, directory) == ["system_admin", "security_admin"]
    assert not (directory / "auditor.txt").exists()
    assert {u["username"]: u["status"] for u in accounts.list()} == {
        "ops": "active",
        "sec": "active",
        "review": "active",
        "auditor": "disabled",
        "system_admin": "active",
        "security_admin": "active",
    }


def test_bootstrap_keeps_handoffs_when_commit_acknowledgement_is_lost(env, tmp_path):
    from sqlalchemy import event

    from tekes_quota_kit.admin_auth import ROLES
    from tekes_quota_kit.operator_bootstrap import bootstrap

    kit, accounts, client = env
    directory = tmp_path / "handoff"

    def lose_acknowledgement(session):
        raise ConnectionError("commit acknowledgement lost")

    event.listen(kit.sessions, "after_commit", lose_acknowledgement, once=True)
    with pytest.raises(ConnectionError, match="acknowledgement lost"):
        bootstrap(accounts, directory)

    assert bootstrap(accounts, directory) == []
    for role in ROLES:
        password = (directory / f"{role}.txt").read_text().strip()
        assert login(client, role, password).status_code == 403
