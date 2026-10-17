"""Disabled services remain identifiable, cannot admit work, and can finish accounting."""

import pytest
from sqlalchemy import func, select
from test_composite_flow import begin, headers, use
from test_composite_flow import configured as composite
from test_quota_flow import (
    ADMIN_KEY,
    ISSUER_KEY,
    configured,
    issue,
    provider_headers,
    redeem,
)

from tekes_quota_kit.models import Service, Token

TENANT = "demo-tenant"
MEMBER_KEY = "membership-service-status-tests-000000001"


def toggle(client, enabled, code="example.run"):
    return client.patch(
        f"/v1/admin/tenants/{TENANT}/services/{code}/status",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        json={"enabled": enabled},
    )


def test_toggle_preserves_disabled_state_and_distinguishes_missing(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="per_use", limit=4)
    with kit.sessions() as db:
        assert db.get(Service, service_id).enabled is True
    assert toggle(client, False).json() == {
        "service_id": service_id,
        "service_code": "example.run",
        "enabled": False,
        "status": "disabled",
    }
    # Old integrations saving the other fields must not silently turn the service on.
    response = client.put(
        f"/v1/admin/tenants/{TENANT}/services/example.run",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        json={"quota_code": "example", "description": "暂未开放"},
    )
    assert response.status_code == 200
    disabled = redeem(client, service_id, "blocked")
    assert disabled.status_code == 403
    assert disabled.json() == {
        "code": "service_disabled",
        "message": "Service is disabled",
        "service_id": service_id,
        "service_code": "example.run",
        "enabled": False,
        "status": "disabled",
    }
    balance = client.get(
        "/v1/quota", headers={"Authorization": f"Bearer {ISSUER_KEY}", "X-Subject-ID": "42"}
    )
    assert balance.status_code == 403 and balance.json()["code"] == "service_disabled"
    missing = toggle(client, True, "missing")
    assert missing.status_code == 404 and missing.json()["code"] == "unknown_service"
    with kit.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Token)) == 0
    assert toggle(client, True).status_code == 200
    assert redeem(client, service_id, "blocked").status_code == 200


def test_status_mutations_require_admin_and_strict_boolean(tmp_path):
    _, client, _ = configured(tmp_path, mode="per_use", limit=4)
    url = f"/v1/admin/tenants/{TENANT}/services/example.run/status"
    assert client.patch(url, json={"enabled": False}).status_code == 401
    for body in [{}, {"enabled": None}, {"enabled": "false"}, {"enabled": 0}]:
        assert (
            client.patch(
                url, headers={"Authorization": f"Bearer {ADMIN_KEY}"}, json=body
            ).status_code
            == 422
        )


def test_shared_entitlements_report_disabled_without_hiding_enabled_sibling(tmp_path):
    kit, client, _ = configured(tmp_path, mode="reported_usage", limit=100)
    kit.put_service(TENANT, "sibling", "example")
    kit.put_client("members", MEMBER_KEY, TENANT, "*", "membership")
    toggle(client, False)
    h = {"Authorization": f"Bearer {MEMBER_KEY}"}
    r = client.get("/v1/members/42/usage", headers=h)
    assert r.status_code == 200
    quota = r.json()["quotas"][0]
    assert quota["services"] == ["sibling"]
    assert [s["service_code"] for s in quota["service_balances"]] == ["sibling"]
    assert {s["service_code"]: s["enabled"] for s in quota["service_details"]} == {
        "sibling": True,
    }
    # The key is tenant-scoped and does not grant Service clients member-list access.
    assert client.get("/v1/members/42/usage", headers=provider_headers()).status_code == 401
    assert client.get("/v1/members/999/usage", headers=h).json()["quotas"] == []
    kit.put_client(
        "sibling", "sibling-service-key-for-test-0000000001", TENANT, "sibling", "issuer"
    )
    assert (
        client.post(
            "/v1/token",
            headers={"Authorization": "Bearer sibling-service-key-for-test-0000000001"},
            json={"subject_id": 42, "service_id": kit.put_service(TENANT, "sibling", "example")},
        ).status_code
        == 200
    )


def test_disable_blocks_metered_retries_but_allows_settlement(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="reported_usage", limit=100)
    token = issue(client, service_id, "already-started")
    toggle(client, False)
    for key in ["already-started", "new"]:
        r = client.post(
            "/v1/token",
            headers={"Authorization": f"Bearer {ISSUER_KEY}"},
            json={"subject_id": 42, "service_id": service_id, "request_key": key},
        )
        assert r.status_code == 403 and r.json()["code"] == "service_disabled"
    assert (
        client.post(
            "/v1/token/settle",
            headers=provider_headers(),
            json={"token": token, "consumed_units": 10},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/v1/token/status",
            headers=provider_headers(),
            json={"token": token},
        ).json()["status"]
        == "settled"
    )
    toggle(client, True)
    assert kit.balance(kit.client(ISSUER_KEY, "issuer"), 42)["used"] == 10


def test_disabled_per_use_service_can_still_refund(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="per_use", limit=1)
    token = redeem(client, service_id, "before-disabled").json()["token"]
    toggle(client, False)
    assert (
        client.post(
            "/v1/token/refund",
            headers=provider_headers(),
            json={"token": token},
        ).status_code
        == 200
    )
    toggle(client, True)
    assert kit.balance(kit.client(ISSUER_KEY, "issuer"), 42)["used"] == 0


@pytest.mark.parametrize("disabled", ["measurement_session", "blood_pressure"])
def test_live_switch_blocks_composite_parent_or_child_without_preventing_close(tmp_path, disabled):
    kit, client = composite(tmp_path, limit=5)
    token = begin(client).json()["token"]
    kit.set_service_enabled(TENANT, disabled, False)
    blocked = use(client, token)
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "service_disabled"
    assert blocked.json()["service_code"] == disabled
    if disabled == "measurement_session":
        assert begin(client, "new-session").json()["code"] == "service_disabled"
        assert begin(client).json()["code"] == "service_disabled"
    else:
        assert begin(client).json()["authorized_services"] == ["blood_oxygen"]
        started = begin(client, "new-session")
        assert started.json()["authorized_services"] == ["blood_oxygen"]
        assert use(client, token, "blood_oxygen").status_code == 200
    assert client.post("/v1/close", headers=headers(), json={"token": token}).status_code == 200
