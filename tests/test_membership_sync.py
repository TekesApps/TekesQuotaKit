from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaKit
from tekes_quota_kit.models import Base, Client

ADMIN_KEY = "member-sync-admin-key-for-tests-000000001"
SECRET = "member-sync-token-secret-for-tests-00001"
TENANT = "shukang-zhiyi"
ADMIN = {"Authorization": f"Bearer {ADMIN_KEY}"}


def setup(tmp_path):
    kit = QuotaKit(f"sqlite:///{tmp_path / 'members.db'}", SECRET)
    Base.metadata.create_all(kit.engine)
    kit.put_quota(TENANT, "door_open_count", "use", "per_use")
    kit.put_level(TENANT, "member")
    kit.put_limit(TENANT, "member", "door_open_count", "finite", 2, "week", "Asia/Shanghai")
    kit.put_service(TENANT, "door_open", "door_open_count")
    kit.put_level("other-business", "member")
    return kit, TestClient(create_app(kit, ADMIN_KEY))


def provision(client, client_id, role, service_code=""):
    response = client.post(
        f"/v1/admin/clients/{client_id}/provision",
        headers=ADMIN,
        json={
            "tenant_id": TENANT,
            "service_code": service_code,
            "role": role,
            "base_url": "http://127.0.0.1:9460",
        },
    )
    assert response.status_code == 200, response.text
    guide = response.text
    contract = json.loads(re.search(r"```json\n(.*?)\n```", guide, re.S).group(1))
    return guide, contract


def later(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def test_member_sync_contract_lists_levels_and_needs_no_service(tmp_path):
    kit, client = setup(tmp_path)
    guide, contract = provision(client, "shukang-members", "membership")
    assert contract["schema"] == "tekes-quotakit-membership/v1"
    assert contract["role"] == "membership" and contract["tenant_id"] == TENANT
    assert contract["levels"] == [
        {
            "level_code": "member",
            "limits": [
                {
                    "quota_code": "door_open_count",
                    "limit_mode": "finite",
                    "limit_value": 2,
                    "period_kind": "week",
                    "timezone": "Asia/Shanghai",
                }
            ],
        }
    ]
    for endpoint in ("/v1/members/batch", "/v1/members/{subject_id}", "DELETE"):
        assert endpoint in guide
    with kit.sessions() as db:
        row = db.scalar(select(Client).where(Client.client_id == "shukang-members"))
    assert (row.role, row.service_code) == ("membership", "*")


def test_service_contract_explains_the_membership_prerequisite(tmp_path):
    _kit, client = setup(tmp_path)
    guide, _ = provision(client, "wecom-door", "consumer", "door_open")
    assert "no_level" in guide and "member-sync client" in guide


def test_members_drive_service_admission(tmp_path):
    _kit, client = setup(tmp_path)
    _, members = provision(client, "shukang-members", "membership")
    _, door = provision(client, "wecom-door", "consumer", "door_open")
    sync = {"Authorization": f"Bearer {members['client_key']}"}
    use = {"Authorization": f"Bearer {door['client_key']}"}

    def open_door(key: str):
        return client.post(
            "/v1/redeem",
            headers=use,
            json={"subject_id": 42, "service_code": "door_open", "request_key": key},
        )

    assert client.get("/v1/members/42", headers=sync).json() == {"subject_id": 42, "member": None}
    assert open_door("before").json()["code"] == "no_level"

    expiry = later(30)
    put = client.put(
        "/v1/members/42",
        headers=sync,
        json={"level_code": "member", "expires_at": expiry, "renew_term": True},
    )
    assert put.status_code == 200, put.text
    assert put.json()["member"]["level_code"] == "member"
    assert open_door("after").status_code == 200

    # Re-sending the same state is safe; changing the expiry needs renew_term.
    same = {"level_code": "member", "expires_at": expiry}
    assert client.put("/v1/members/42", headers=sync, json=same).status_code == 200
    longer = {"level_code": "member", "expires_at": later(60)}
    rejected = client.put("/v1/members/42", headers=sync, json=longer)
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "term_change_requires_renewal"
    renewed = client.put("/v1/members/42", headers=sync, json={**longer, "renew_term": True})
    assert renewed.status_code == 200

    assert client.delete("/v1/members/42", headers=sync).json() == {"subject_id": 42, "ended": True}
    assert client.delete("/v1/members/42", headers=sync).json()["ended"] is False
    assert client.get("/v1/members/42", headers=sync).json()["member"] is None
    assert open_door("ended").json()["code"] == "no_level"


def test_batch_import_applies_items_independently(tmp_path):
    _kit, client = setup(tmp_path)
    _, members = provision(client, "shukang-members", "membership")
    sync = {"Authorization": f"Bearer {members['client_key']}"}
    response = client.post(
        "/v1/members/batch",
        headers=sync,
        json={
            "items": [
                {"subject_id": 1, "level_code": "member", "renew_term": True},
                {"subject_id": 2, "level_code": "gold", "renew_term": True},
                {"subject_id": 3, "level_code": "member", "expires_at": later(10)},
            ]
        },
    )
    body = response.json()
    assert (body["total"], body["failed"]) == (3, 1)
    assert [r["ok"] for r in body["results"]] == [True, False, True]
    assert body["results"][1]["code"] == "unknown_level"
    assert client.get("/v1/members/3", headers=sync).json()["member"]["level_code"] == "member"
    too_many = {"items": [{"subject_id": i, "level_code": "member"} for i in range(1, 502)]}
    assert client.post("/v1/members/batch", headers=sync, json=too_many).status_code == 422


def test_roles_cannot_cross_and_tenants_stay_separate(tmp_path):
    kit, client = setup(tmp_path)
    _, members = provision(client, "shukang-members", "membership")
    _, door = provision(client, "wecom-door", "consumer", "door_open")
    sync = {"Authorization": f"Bearer {members['client_key']}"}
    use = {"Authorization": f"Bearer {door['client_key']}"}
    # A service client cannot manage members, and a member-sync client cannot use Services.
    assert client.get("/v1/members/42", headers=use).status_code == 401
    redeem = {"subject_id": 42, "service_code": "door_open", "request_key": "x"}
    assert client.post("/v1/redeem", headers=sync, json=redeem).status_code == 401
    assert client.get("/v1/members/42", headers=ADMIN).status_code == 401
    # Writes land in the credential's own business system only.
    client.put("/v1/members/7", headers=sync, json={"level_code": "member", "renew_term": True})
    assert kit.membership(TENANT, 7)["level_code"] == "member"
    assert kit.membership("other-business", 7) is None
