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
PASSWORD = "correct horse battery"
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
    accounts.create("ops", PASSWORD)
    return kit, accounts, TestClient(create_app(kit, ADMIN_KEY))


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
    accounts.set_password("ops", "another long password")
    assert client.get("/v1/admin/session").status_code == 401
    assert login(client).status_code == 401
    assert login(client, password="another long password").status_code == 200
    accounts.set_status("ops", "disabled")
    assert client.get("/v1/admin/session").status_code == 401
    assert login(client, password="another long password").status_code == 401
    accounts.set_status("ops", "active")
    login(client, password="another long password")
    with kit.sessions.begin() as db:
        db.execute(update(AdminSession).values(expires_at=utc_now() - timedelta(seconds=1)))
    assert client.get("/v1/admin/session").status_code == 401


def test_admin_key_still_works_and_a_bad_bearer_is_not_rescued_by_cookie(env):
    _kit, _accounts, client = env
    bearer = {"Authorization": f"Bearer {ADMIN_KEY}"}
    assert client.get("/v1/admin/session", headers=bearer).json()["data"]["via"] == "admin_key"
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
        accounts.create("ops", "short")
    with pytest.raises(AdminAccountError):
        accounts.create("bad name", PASSWORD)
    accounts.create("ops", PASSWORD)
    with pytest.raises(AdminAccountError):
        accounts.create("ops", PASSWORD)
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
        "admin-user", "add", "--username", "ops", "--password-stdin", stdin=PASSWORD + "\n"
    )
    assert code == 0 and "Created admin user ops" in out.out
    code, out = cli(
        "admin-user", "add", "--username", "ops", "--password-stdin", stdin=PASSWORD + "\n"
    )
    assert code == 1 and "already exists" in out.err
    code, out = cli("admin-user", "list")
    assert code == 0 and out.out.startswith("ops\tactive\tlast login -")
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
    assert created.json() == {"tenant_id": "shukang-zhiyi", "name": "数康智医"}
    # A tenant used only by API scripts is listed but marked unregistered.
    client.put("/v1/admin/tenants/legacy/levels/basic", json={}, headers=GUARD)
    assert client.get("/v1/admin/tenants").json()["items"] == [
        {"tenant_id": "legacy", "name": None, "registered": False},
        {"tenant_id": "shukang-zhiyi", "name": "数康智医", "registered": True},
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
