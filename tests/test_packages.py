from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_quota_flow import (
    ADMIN_KEY,
    PROVIDER_KEY,
    configured,
    issue,
    provider_headers,
    redeem,
)

from tekes_quota_kit.core import QuotaError, utc_now
from tekes_quota_kit.models import PackageCharge, PackageGrant

TENANT = "demo-tenant"


def grant(kit, code="one", units=2, *, service="example.run", **kwargs):
    kit.put_package(TENANT, "extra", "Extra", service, units)
    kit.put_package_grant(TENANT, 42, code, "extra", **kwargs)


def balances(kit):
    return kit.balance(kit.client(PROVIDER_KEY, "provider"), 42)


def test_main_first_multiple_packages_and_refund_source(tmp_path):
    kit, client, service = configured(tmp_path, mode="per_use", limit=1)
    now = utc_now()
    grant(kit, "later", expires_at=now + timedelta(days=7))
    grant(kit, "earlier", expires_at=now + timedelta(days=1))
    assert redeem(client, service, "main").status_code == 200
    token = redeem(client, service, "aux").json()["token"]
    with kit.sessions() as db:
        rows = {g.grant_code: g.used_units for g in db.scalars(select(PackageGrant))}
    assert rows == {"later": 0, "earlier": 1}
    assert balances(kit)["main_remaining"] == 0
    assert balances(kit)["auxiliary_remaining"] == 3
    assert balances(kit)["remaining"] == 3
    # A revoked source receives its refund, but never becomes spendable again.
    kit.revoke_package_grant(TENANT, 42, "earlier")
    r = client.post("/v1/token/refund", headers=provider_headers(), json={"token": token})
    assert r.status_code == 200
    assert client.post(
        "/v1/token/refund", headers=provider_headers(), json={"token": token}
    ).json()["idempotent"]
    assert balances(kit)["used"] == 1
    assert balances(kit)["remaining"] == 2


def test_auxiliary_final_unit_concurrency_and_retry(tmp_path):
    kit, client, service = configured(tmp_path, mode="per_use", limit=0)
    grant(kit, units=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda n: redeem(client, service, f"op{n}"), range(4)))
    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409]
    index = next(i for i, r in enumerate(responses) if r.status_code == 200)
    assert redeem(client, service, f"op{index}").json()["code"] == "already_redeemed"
    assert balances(kit)["remaining"] == 0
    assert balances(kit)["used"] == 0
    with kit.sessions() as db:
        assert len(list(db.scalars(select(PackageCharge)))) == 1


def test_snapshot_edit_delete_and_repeated_grant(tmp_path):
    kit, _, _ = configured(tmp_path, mode="per_use", limit=0)
    grant(kit, units=3)
    kit.put_package(TENANT, "extra", "Changed", "example.run", 99)
    kit.put_package_grant(TENANT, 42, "one", "extra")
    assert balances(kit)["remaining"] == 3
    kit.put_package_grant(TENANT, 42, "two", "extra")
    assert balances(kit)["remaining"] == 102
    kit.delete_package(TENANT, "extra")
    assert balances(kit)["remaining"] == 102
    kit.put_package_grant(TENANT, 42, "one", "extra", total_units=4)
    assert balances(kit)["remaining"] == 103


def test_service_scope_validity_tenant_and_membership(tmp_path):
    kit, client, service = configured(tmp_path, mode="per_use", limit=0)
    kit.put_service(TENANT, "other", "example")
    grant(kit, "other", service="other")
    now = utc_now()
    grant(kit, "future", effective_at=now + timedelta(days=1))
    grant(kit, "expired", effective_at=now - timedelta(days=2), expires_at=now - timedelta(days=1))
    assert balances(kit)["remaining"] == 0
    assert redeem(client, service, "denied").json()["code"] == "quota_exhausted"
    grant(kit, "valid")
    kit.end_membership(TENANT, 42)
    assert redeem(client, service, "no-member").json()["code"] == "no_level"
    with pytest.raises(QuotaError):
        kit.revoke_package_grant("another-tenant", 42, "valid")


def test_durable_split_and_refund(tmp_path):
    kit, client, _ = configured(tmp_path, mode="per_use", limit=1)
    kit.put_service(TENANT, "example.run", "example", redemption_mode="durable", charge_units=3)
    grant(kit, "a", units=1)
    grant(kit, "b", units=1)
    r = client.post(
        "/v1/redeem",
        headers=provider_headers(),
        json={"subject_id": 42, "service_code": "example.run", "request_key": "session"},
    )
    assert r.status_code == 200, r.text
    assert balances(kit)["remaining"] == 0
    assert balances(kit)["used"] == 1
    token = r.json()["token"]
    assert (
        client.post(
            "/v1/token/refund", headers=provider_headers(), json={"token": token}
        ).status_code
        == 200
    )
    assert balances(kit)["remaining"] == 3


def test_insufficient_durable_rolls_back_all_balances(tmp_path):
    kit, client, _ = configured(tmp_path, mode="per_use", limit=1)
    kit.put_service(TENANT, "example.run", "example", redemption_mode="durable", charge_units=4)
    grant(kit, units=2)
    r = client.post(
        "/v1/redeem",
        headers=provider_headers(),
        json={"subject_id": 42, "service_code": "example.run", "request_key": "session"},
    )
    assert r.json()["code"] == "quota_exhausted"
    assert balances(kit)["remaining"] == 3


def test_metered_admission_and_settlement_uses_packages(tmp_path):
    kit, client, service = configured(tmp_path, mode="reported_usage", limit=1)
    grant(kit, units=5)
    token = issue(client, service, "metered")
    assert balances(kit)["remaining"] == 6  # issuance does not reserve
    r = client.post(
        "/v1/token/settle", headers=provider_headers(), json={"token": token, "consumed_units": 4}
    )
    assert r.status_code == 200, r.text
    assert balances(kit)["used"] == 1
    assert balances(kit)["remaining"] == 2
    assert client.post(
        "/v1/token/settle", headers=provider_headers(), json={"token": token, "consumed_units": 4}
    ).json()["idempotent"]
    token = issue(client, service, "overflow")
    assert (
        client.post(
            "/v1/token/settle",
            headers=provider_headers(),
            json={"token": token, "consumed_units": 3},
        ).status_code
        == 200
    )
    assert balances(kit)["used"] == 2  # existing metered overage policy retained
    assert balances(kit)["remaining"] == 0


def test_admin_crud_is_authenticated_and_preserves_usage(tmp_path):
    kit, client, service = configured(tmp_path, mode="per_use", limit=0)
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
    path = "/v1/admin/tenants/demo-tenant/packages/extra"
    config = {"name": "Extra", "service_code": "example.run", "units": 2}
    assert client.put(path, json=config).status_code == 401
    assert client.put(path, json=config, headers=headers).status_code == 200
    binding = "/v1/admin/tenants/demo-tenant/subjects/42/packages/order-1"
    assert client.put(binding, headers=headers, json={"package_code": "extra"}).status_code == 200
    assert redeem(client, service, "one").status_code == 200
    assert (
        client.put(
            binding, headers=headers, json={"package_code": "extra", "total_units": 0}
        ).status_code
        == 422
    )
    assert (
        client.put(
            binding, headers=headers, json={"package_code": "extra", "total_units": 3}
        ).status_code
        == 200
    )
    rows = client.get(
        "/v1/admin/tables/tq_package_grants?tenant=demo-tenant&subject_id=42", headers=headers
    ).json()["rows"]
    assert rows[0]["used_units"] == 1
    assert rows[0]["total_units"] == 3
    assert client.delete(binding, headers=headers).status_code == 200
    assert balances(kit)["remaining"] == 0
    assert (
        client.put(binding, headers=headers, json={"package_code": "extra"}).json()["code"]
        == "grant_revoked"
    )
    assert client.delete(path, headers=headers).status_code == 200


def test_duplicate_grant_is_one_copy_under_concurrency(tmp_path):
    kit, _, _ = configured(tmp_path, mode="per_use", limit=0)
    kit.put_package(TENANT, "extra", "Extra", "example.run", 2)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: kit.put_package_grant(TENANT, 42, "one", "extra"), range(4)))
    assert balances(kit)["remaining"] == 2
    with kit.sessions() as db:
        assert len(list(db.scalars(select(PackageGrant)))) == 1


def test_unlimited_main_does_not_consume_packages(tmp_path):
    kit, client, service = configured(tmp_path, mode="per_use", limit=0)
    kit.put_limit(TENANT, "regular", "example", "unlimited", None, "month", "Asia/Shanghai")
    grant(kit, units=2)
    assert redeem(client, service, "unlimited").status_code == 200
    assert balances(kit)["remaining"] is None
    assert balances(kit)["auxiliary_remaining"] == 2


def test_token_funding_sources_and_cross_tenant_admin_scope(tmp_path):
    kit, client, service = configured(tmp_path, mode="per_use", limit=0)
    grant(kit, units=2)
    token = redeem(client, service, "aux").json()["token"]
    status = client.post(
        "/v1/token/status", headers=provider_headers(), json={"token": token}
    ).json()
    assert status["main_consumed_units"] == 0
    assert status["package_charges"] == [{"grant_code": "one", "units": 1}]
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
    assert (
        client.get(
            "/v1/admin/tables/tq_package_charges?tenant=another-tenant", headers=headers
        ).json()["total"]
        == 0
    )
    assert (
        client.get(
            "/v1/admin/tables/tq_package_charges?tenant=demo-tenant", headers=headers
        ).json()["total"]
        == 1
    )


def test_main_period_reset_preserves_auxiliary_usage(tmp_path, monkeypatch):
    kit, client, service = configured(tmp_path, mode="per_use", limit=1)
    grant(kit, units=3)
    assert redeem(client, service, "main").status_code == 200
    assert redeem(client, service, "aux").status_code == 200
    later = utc_now() + timedelta(days=32)
    monkeypatch.setattr("tekes_quota_kit.core.utc_now", lambda: later)
    assert balances(kit)["main_remaining"] == 1
    assert balances(kit)["auxiliary_remaining"] == 2
    assert redeem(client, service, "next-month").status_code == 200
    assert balances(kit)["auxiliary_remaining"] == 2


def test_expired_package_refund_does_not_extend_validity(tmp_path, monkeypatch):
    kit, client, service = configured(tmp_path, mode="per_use", limit=0)
    now = utc_now()
    grant(kit, units=2, expires_at=now + timedelta(hours=1))
    token = redeem(client, service, "aux").json()["token"]
    monkeypatch.setattr("tekes_quota_kit.core.utc_now", lambda: now + timedelta(hours=2))
    assert balances(kit)["remaining"] == 0
    kit.refund(token, kit.client(PROVIDER_KEY, "provider"), 42)
    assert balances(kit)["remaining"] == 0
    with kit.sessions() as db:
        assert db.scalar(select(PackageGrant)).used_units == 0


def test_update_cannot_reduce_below_consumption(tmp_path):
    kit, client, service = configured(tmp_path, mode="per_use", limit=0)
    grant(kit, units=3)
    assert redeem(client, service, "one").status_code == 200
    assert redeem(client, service, "two").status_code == 200
    with pytest.raises(QuotaError, match="below usage"):
        kit.put_package_grant(TENANT, 42, "one", "extra", total_units=1)
    assert balances(kit)["remaining"] == 1
