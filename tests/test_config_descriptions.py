"""Configuration notes survive legacy callers and never change entitlement accounting."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaKit
from tekes_quota_kit.models import Base, Client, Level, Limit, Quota, Service

KEY = "description-admin-key-for-tests-0000001"


@pytest.mark.parametrize("kind", ["levels", "quotas", "services", "limits"])
def test_description_roundtrip_and_legacy_compatibility(tmp_path, kind):
    kit = QuotaKit(f"sqlite:///{tmp_path / 'notes.db'}", "description-token-secret-for-tests-0001")
    Base.metadata.create_all(kit.engine)
    for tenant in ("demo", "other"):
        kit.put_level(tenant, "star")
        kit.put_quota(tenant, "measure", "use", "per_use")
        kit.put_service(tenant, "exam", "measure")
        kit.put_limit(tenant, "star", "measure", "finite", 4, "month", "Asia/Shanghai")
    kit.assign("demo", "42", "star")
    client = TestClient(create_app(kit, KEY))
    headers = {"Authorization": f"Bearer {KEY}"}
    configs = {
        "levels": ("levels/star", {}, Level),
        "quotas": ("quotas/measure", {"unit_code": "use", "metering_mode": "per_use"}, Quota),
        "services": ("services/exam", {"quota_code": "measure"}, Service),
        "limits": (
            "levels/star/limits/measure",
            {
                "limit_mode": "finite",
                "limit_value": 4,
                "period_kind": "month",
                "timezone": "Asia/Shanghai",
            },
            Limit,
        ),
    }
    path, body, model = configs[kind]
    url = "/v1/admin/tenants/demo/" + path
    note = "基础体检：每月4次。\n家庭2人，缴费业主免费。"

    def stored(tenant="demo"):
        with kit.sessions() as db:
            return db.scalar(select(model).where(model.tenant_id == tenant)).description

    assert client.put(url, json={**body, "description": note}).status_code == 401
    assert client.put(url, headers=headers, json={**body, "description": note}).status_code == 200
    assert stored() == note
    assert stored("other") is None
    rows = client.get(
        f"/v1/admin/tables/{model.__tablename__}", headers=headers, params={"tenant": "demo"}
    ).json()
    assert rows["rows"][0]["description"] == note
    # Old Level PUT sends no body at all; old callers of other routes omit description.
    args = {} if kind == "levels" else {"json": body}
    assert client.put(url, headers=headers, **args).status_code == 200
    assert stored() == note
    assert client.put(url, headers=headers, json={**body, "description": None}).status_code == 200
    assert stored() == note
    assert (
        client.put(url, headers=headers, json={**body, "description": "中" * 2001}).status_code
        == 422
    )
    assert stored() == note
    assert client.put(url, headers=headers, json={**body, "description": "  "}).status_code == 200
    assert stored() is None
    caller = Client(tenant_id="demo", service_code="exam", client_id="reader", role="consumer")
    balance = kit.balance(caller, "42")
    assert balance["limit"] == 4 and balance["used"] == 0
