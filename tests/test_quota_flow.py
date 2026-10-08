from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaError, QuotaKit
from tekes_quota_kit.models import Base

ADMIN_KEY = "admin-key-for-local-test-only-00000001"
ISSUER_KEY = "issuer-key-for-local-test-only-0000001"
PROVIDER_KEY = "provider-key-for-local-test-only-0001"
SECRET = "token-secret-for-local-test-only-00001"


def configured(tmp_path, *, mode: str, limit: int) -> tuple[QuotaKit, TestClient, int]:
    kit = QuotaKit(f"sqlite:///{tmp_path / 'quota.db'}", SECRET)
    Base.metadata.create_all(kit.engine)
    kit.put_quota("demo-tenant", "example", "use" if mode == "per_use" else "model_token", mode)
    service_id = kit.put_service("demo-tenant", "example.run", "example")
    kit.put_level("demo-tenant", "regular")
    kit.put_limit("demo-tenant", "regular", "example", "finite", limit, "month", "Asia/Shanghai")
    kit.assign("demo-tenant", 42, "regular")
    kit.put_client("issuer", ISSUER_KEY, "demo-tenant", "example.run", "issuer")
    kit.put_client("provider", PROVIDER_KEY, "demo-tenant", "example.run", "provider")
    return kit, TestClient(create_app(kit, ADMIN_KEY)), service_id


def issue(client: TestClient, service_id: int, key: str) -> str:
    response = client.post(
        "/v1/token",
        headers={"Authorization": f"Bearer {ISSUER_KEY}"},
        json={"subject_id": 42, "service_id": service_id, "request_key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def provider_headers(subject: int = 42) -> dict[str, str]:
    return {"Authorization": f"Bearer {PROVIDER_KEY}", "X-Subject-ID": str(subject)}


def redeem(client: TestClient, service_id: int, key: str):
    return client.post(
        "/v1/redeem",
        headers=provider_headers(),
        json={"subject_id": 42, "service_id": service_id, "request_key": key},
    )


def test_per_use_competes_for_final_unit_and_refunds(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="per_use", limit=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda key: redeem(client, service_id, key), ["one", "two"]))
    assert sorted(response.status_code for response in results) == [200, 409]
    accepted_index = 0 if results[0].status_code == 200 else 1
    accepted = results[accepted_index].json()["token"]
    accepted_key = ["one", "two"][accepted_index]
    retry = redeem(client, service_id, accepted_key)
    assert retry.status_code == 409
    assert retry.json()["code"] == "already_redeemed"
    assert retry.json()["token"] == accepted
    assert (
        client.post(
            "/v1/token/status", headers=provider_headers(), json={"token": accepted}
        ).json()["status"]
        == "settled"
    )
    assert kit.balance(kit.client(ISSUER_KEY, "issuer"), 42)["used"] == 1
    assert (
        client.post(
            "/v1/token/refund", headers=provider_headers(), json={"token": accepted}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/v1/token/refund", headers=provider_headers(), json={"token": accepted}
        ).json()["idempotent"]
        is True
    )
    assert kit.balance(kit.client(ISSUER_KEY, "issuer"), 42)["used"] == 0


def test_same_per_use_request_key_cannot_charge_twice(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="per_use", limit=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: redeem(client, service_id, "same-operation"), range(2)))
    assert sorted(response.status_code for response in results) == [200, 409]
    assert kit.balance(kit.client(ISSUER_KEY, "issuer"), 42)["used"] == 1
    assert (
        client.post(
            "/v1/token",
            headers={"Authorization": f"Bearer {ISSUER_KEY}"},
            json={"subject_id": 42, "service_id": service_id},
        ).json()["code"]
        == "wrong_mode"
    )


def test_reported_usage_settles_once_and_may_exceed_limit(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="reported_usage", limit=10)
    one, two = issue(client, service_id, "one"), issue(client, service_id, "two")
    assert kit.balance(kit.client(ISSUER_KEY, "issuer"), 42)["used"] == 0
    pending = client.get(
        "/v1/tokens/unsettled", headers={"Authorization": f"Bearer {PROVIDER_KEY}"}
    )
    assert pending.status_code == 200
    assert len(pending.json()) == 2
    for token in (one, two):
        response = client.post(
            "/v1/token/settle",
            headers={"Authorization": f"Bearer {PROVIDER_KEY}"},
            json={"token": token, "consumed_units": 8},
        )
        assert response.status_code == 200, response.text
    assert kit.balance(kit.client(ISSUER_KEY, "issuer"), 42)["used"] == 16
    assert (
        client.get(
            "/v1/tokens/unsettled", headers={"Authorization": f"Bearer {PROVIDER_KEY}"}
        ).json()
        == []
    )
    assert redeem(client, service_id, "not-per-use").json()["code"] == "wrong_mode"
    assert (
        client.post(
            "/v1/token/settle",
            headers=provider_headers(),
            json={"token": one, "consumed_units": 8},
        ).json()["idempotent"]
        is True
    )
    assert (
        client.post(
            "/v1/token/settle",
            headers=provider_headers(),
            json={"token": one, "consumed_units": 7},
        ).json()["code"]
        == "settlement_conflict"
    )
    exhausted = client.post(
        "/v1/token",
        headers={"Authorization": f"Bearer {ISSUER_KEY}"},
        json={"subject_id": 42, "service_id": service_id, "request_key": "three"},
    )
    assert exhausted.json()["code"] == "quota_exhausted"


def test_token_scope_and_issue_retry(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="reported_usage", limit=2)
    token = issue(client, service_id, "same")
    assert issue(client, service_id, "same") == token
    wrong = client.post("/v1/token/status", headers=provider_headers(43), json={"token": token})
    assert wrong.status_code == 403
    assert wrong.json()["code"] == "scope_mismatch"
    assert (
        client.post(
            "/v1/token/settle",
            headers={"Authorization": f"Bearer {PROVIDER_KEY}"},
            json={"token": token, "consumed_units": 1},
        ).status_code
        == 200
    )
    assert all(
        "id" in table.columns and table.primary_key.columns.keys() == ["id"]
        for table in Base.metadata.tables.values()
    )


def test_subject_id_is_numeric_and_positive(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="per_use", limit=2)
    for bad_subject in ("42", 0, -1, 9223372036854775808):
        response = client.post(
            "/v1/redeem",
            headers=provider_headers(),
            json={"subject_id": bad_subject, "service_id": service_id},
        )
        assert response.status_code == 422
    token = redeem(client, service_id, "numeric-subject").json()["token"]
    assert (
        client.post(
            "/v1/token/status", headers=provider_headers(43), json={"token": token}
        ).status_code
        == 403
    )
    for bad_header in ("0", "abc", "9223372036854775808"):
        response = client.post(
            "/v1/token/status",
            headers={"Authorization": f"Bearer {PROVIDER_KEY}", "X-Subject-ID": bad_header},
            json={"token": token},
        )
        assert response.status_code == 422
    with pytest.raises(QuotaError, match="positive integer"):
        kit.assign("demo-tenant", "42", "regular")


def test_consumer_needs_one_call_to_redeem_per_use(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="per_use", limit=2)
    key = "consumer-key-for-local-test-only-0001"
    kit.put_client("consumer", key, "demo-tenant", "example.run", "consumer")
    headers = {"Authorization": f"Bearer {key}", "X-Subject-ID": "42"}
    payload = {"subject_id": 42, "service_id": service_id}
    first = client.post("/v1/redeem", headers=headers, json=payload)
    second = client.post("/v1/redeem", headers=headers, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["token"] != second.json()["token"]
    assert kit.balance(kit.client(key, "issuer"), 42)["used"] == 2
    other_service_id = kit.put_service("demo-tenant", "other.service", "example")
    forbidden = client.post(
        "/v1/redeem", headers=headers, json={"subject_id": 42, "service_id": other_service_id}
    )
    assert forbidden.status_code == 403
    assert client.post("/v1/token", headers=headers, json=payload).json()["code"] == "wrong_mode"


def test_invalid_limit_configuration(tmp_path):
    kit, _, _ = configured(tmp_path, mode="per_use", limit=1)
    with pytest.raises(QuotaError, match="Invalid limit value"):
        kit.put_limit(
            "demo-tenant", "regular", "example", "unlimited", 0, "month", "Asia/Shanghai"
        )
    with pytest.raises(QuotaError, match="cannot be changed in place"):
        kit.put_quota("demo-tenant", "example", "model_token", "reported_usage")
