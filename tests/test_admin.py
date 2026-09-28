from __future__ import annotations

import sys
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaKit, utc_now
from tekes_quota_kit.main import run
from tekes_quota_kit.models import Base, Client

ADMIN_KEY = "admin-management-key-for-tests-0000000001"
TOKEN_SECRET = "admin-token-secret-for-tests-0000000001"
TENANT = "shukang-zhiyi"


def setup(tmp_path):
    kit = QuotaKit(f"sqlite:///{tmp_path / 'admin.db'}", TOKEN_SECRET)
    Base.metadata.create_all(kit.engine)
    kit.put_level(TENANT, "basic")
    kit.put_quota(TENANT, "measurement_count", "use", "per_use")
    kit.put_limit(TENANT, "basic", "measurement_count", "finite", 2, "level_term", "UTC")
    kit.assign(TENANT, 42, "basic", utc_now() + timedelta(days=1))
    kit.put_service(
        TENANT, "measurement_session", "measurement_count", kind="composite", charge_units=1
    )
    kit.put_service(TENANT, "blood_pressure", None)
    kit.put_service_member(TENANT, "measurement_session", "blood_pressure")
    return kit, TestClient(create_app(kit, ADMIN_KEY))


def admin_headers():
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


def test_admin_lists_all_tables_and_scopes_token_items(tmp_path):
    kit, client = setup(tmp_path)
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/app.js").status_code == 200
    assert client.get("/admin/app.css").status_code == 200
    assert client.get("/v1/admin/tables").status_code == 401
    names = client.get("/v1/admin/tables", headers=admin_headers()).json()["tables"]
    assert len(names) == 11
    for name in names:
        response = client.get(
            f"/v1/admin/tables/{name}",
            headers=admin_headers(),
            params={"tenant": TENANT},
        )
        assert response.status_code == 200, (name, response.text)
        assert "key_hash" not in response.json()["columns"]
        assert "token_hash" not in response.json()["columns"]
    assert (
        client.get(
            "/v1/admin/tables/other", headers=admin_headers(), params={"tenant": TENANT}
        ).status_code
        == 404
    )

    provisioned = client.post(
        "/v1/admin/clients/measurement-backend/provision",
        headers=admin_headers(),
        json={
            "tenant_id": TENANT,
            "service_code": "measurement_session",
            "role": "consumer",
            "base_url": "https://quota.example.com",
        },
    )
    assert provisioned.status_code == 200, provisioned.text
    key = provisioned.text.split('"client_key": "')[1].split('"')[0]
    caller = kit.client(key, "provider")
    started = kit.redeem(caller, 42, "measurement_session", "visit-1")
    assert kit.use(started["token"], caller, 42, "blood_pressure", "bp-1")["slot_no"] == 1
    result = client.get(
        "/v1/admin/tables/tq_token_items",
        headers=admin_headers(),
        params={"tenant": TENANT},
    ).json()
    assert result["total"] == 2
    assert (
        client.get(
            "/v1/admin/tables/tq_token_items",
            headers=admin_headers(),
            params={"tenant": "another-tenant"},
        ).json()["total"]
        == 0
    )


def test_provision_download_is_one_time_and_rotation_requires_opt_in(tmp_path):
    kit, client = setup(tmp_path)
    path = "/v1/admin/clients/measurement-backend/provision"
    payload = {
        "tenant_id": TENANT,
        "service_code": "measurement_session",
        "role": "consumer",
        "base_url": "https://quota.example.com",
    }
    first = client.post(path, headers=admin_headers(), json=payload)
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    assert "attachment" in first.headers["content-disposition"]
    assert '"client_key": "' in first.text
    assert "/v1/redeem" in first.text and "/v1/use" in first.text
    assert "## Instructions for the client agent" in first.text
    assert '"child_service_code": "blood_pressure"' in first.text
    assert '"max_uses": null' in first.text
    assert ADMIN_KEY not in first.text and TOKEN_SECRET not in first.text
    first_key = first.text.split('"client_key": "')[1].split('"')[0]
    with kit.sessions() as db:
        row = db.scalar(select(Client).where(Client.client_id == "measurement-backend"))
        assert row.key_hash == kit.key_hash(first_key)
        assert first_key != row.key_hash
    assert (
        client.post(path, headers=admin_headers(), json=payload).json()["code"] == "client_exists"
    )
    second = client.post(path, headers=admin_headers(), json={**payload, "rotate": True})
    assert second.status_code == 200, second.text
    second_key = second.text.split('"client_key": "')[1].split('"')[0]
    assert second_key != first_key
    old_headers = {"Authorization": f"Bearer {first_key}", "X-Subject-ID": "42"}
    assert client.get("/v1/quota", headers=old_headers).status_code == 401
    assert kit.client(second_key, "provider").service_code == "measurement_session"
    listed = client.get(
        "/v1/admin/tables/tq_clients",
        headers=admin_headers(),
        params={"tenant": TENANT},
    ).json()
    assert first_key not in str(listed) and second_key not in str(listed)
    assert "key_hash" not in listed["columns"]
    assert client.post(path, json=payload).status_code == 401
    invalid = client.post(
        path, headers=admin_headers(), json={**payload, "service_code": "missing"}
    )
    assert invalid.status_code == 404


def test_reported_usage_guide_matches_issuer_role(tmp_path):
    kit, client = setup(tmp_path)
    kit.put_quota(TENANT, "model_tokens", "token", "reported_usage")
    kit.put_service(TENANT, "chat", "model_tokens")
    response = client.post(
        "/v1/admin/clients/chat-issuer/provision",
        headers=admin_headers(),
        json={
            "tenant_id": TENANT,
            "service_code": "chat",
            "role": "issuer",
            "base_url": "https://quota.example.com",
        },
    )
    assert response.status_code == 200, response.text
    assert '"metering_mode": "reported_usage"' in response.text
    assert "/v1/token" in response.text
    assert "/v1/redeem" not in response.text
    assert "/v1/token/settle" not in response.text


def test_generate_secrets_needs_no_database(monkeypatch, capsys):
    monkeypatch.delenv("TEKES_QUOTA_DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["tekes-quota-kit", "generate-secrets"])
    run()
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert all(len(line.split("=", 1)[1]) >= 32 for line in lines)
    assert lines[0].startswith("TEKES_QUOTA_ADMIN_KEY=")
    assert lines[1].startswith("TEKES_QUOTA_TOKEN_SECRET=")
