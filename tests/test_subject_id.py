"""User IDs are strings; integers from older clients are read as their decimal strings."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from test_quota_flow import ADMIN_KEY, ISSUER_KEY, PROVIDER_KEY, SECRET, configured

from tekes_quota_kit.core import normalize_subject


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        (42, "42"),
        ("42", "42"),
        ("042", "042"),
        ("u_8f3k2", "u_8f3k2"),
        ("oAbC-12.x:y@z", "oAbC-12.x:y@z"),
        ("x" * 128, "x" * 128),
        ("--1", "--1"),
        (9223372036854775807, "9223372036854775807"),
        ("92233720368547758070", "92233720368547758070"),
    ],
)
def test_valid_subjects_normalize(raw, stored):
    assert normalize_subject(raw) == stored


@pytest.mark.parametrize(
    "raw",
    [
        0,
        -1,
        9223372036854775808,
        True,
        False,
        4.0,
        None,
        "",
        "0",
        "00",
        "-1",
        "-0",
        " 42",
        "42 ",
        "a b",
        "a/b",
        "a+b",
        "用户",
        "x" * 129,
    ],
)
def test_invalid_subjects_are_rejected(raw):
    assert normalize_subject(raw) is None


def test_integer_and_string_reach_the_same_user(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="per_use", limit=3)
    headers = {"Authorization": f"Bearer {PROVIDER_KEY}", "X-Subject-ID": "42"}
    for subject, key in ((42, "int"), ("42", "str")):
        response = client.post(
            "/v1/redeem",
            headers=headers,
            json={"subject_id": subject, "service_id": service_id, "request_key": key},
        )
        assert response.status_code == 200, response.text
    balance = client.get("/v1/quota", headers={**headers, "Authorization": f"Bearer {ISSUER_KEY}"})
    assert balance.json()["used"] == 2
    # Leading zeros and case are kept, so these are other users without a Level.
    for other in ("042", "042x"):
        response = client.post(
            "/v1/redeem",
            headers=headers,
            json={"subject_id": other, "service_id": service_id, "request_key": other},
        )
        assert response.json()["code"] == "no_level"


def test_string_subject_full_flow_and_case_sensitivity(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="per_use", limit=1)
    kit.assign("demo-tenant", "oUser_AbC", "regular")
    kit.assign("demo-tenant", "ouser_abc", "regular")
    upper = {"Authorization": f"Bearer {PROVIDER_KEY}", "X-Subject-ID": "oUser_AbC"}
    lower = {**upper, "X-Subject-ID": "ouser_abc"}

    def redeem(subject: str, key: str):
        return client.post(
            "/v1/redeem",
            headers=upper,
            json={"subject_id": subject, "service_id": service_id, "request_key": key},
        )

    first = redeem("oUser_AbC", "a")
    assert first.status_code == 200, first.text
    token = first.json()["token"]
    assert redeem("oUser_AbC", "b").json()["code"] == "quota_exhausted"
    assert redeem("ouser_abc", "c").status_code == 200  # a different user with its own quota
    assert client.post("/v1/token/status", headers=lower, json={"token": token}).status_code == 403
    assert client.post("/v1/token/refund", headers=upper, json={"token": token}).status_code == 200
    assert redeem("oUser_AbC", "d").status_code == 200


def test_responses_return_subject_as_string(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="per_use", limit=1)
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    usage = client.get("/v1/admin/tenants/demo-tenant/subjects/42/usage", headers=admin)
    assert usage.json()["subject_id"] == "42"
    put = client.put(
        "/v1/admin/tenants/demo-tenant/subjects/u_8f3k2/level",
        headers=admin,
        json={"level_code": "regular"},
    )
    assert put.json() == {"subject_id": "u_8f3k2", "level_code": "regular"}
    packages = client.get(
        "/v1/admin/tenants/demo-tenant/subjects/u_8f3k2/level-packages", headers=admin
    ).json()
    assert packages["subject_id"] == packages["subject_key"] == "u_8f3k2"
    listed = client.get("/v1/admin/tenants/demo-tenant/subjects", headers=admin).json()
    # Most recently assigned first.
    assert [row["subject_id"] for row in listed["rows"]] == ["u_8f3k2", "42"]
    found = client.get(
        "/v1/admin/tenants/demo-tenant/subjects", headers=admin, params={"subject_id": 42}
    ).json()
    assert [row["subject_id"] for row in found["rows"]] == ["42"]
    bad = client.get(
        "/v1/admin/tenants/demo-tenant/subjects", headers=admin, params={"subject_id": "a b"}
    )
    assert bad.status_code == 422


def test_numeric_subject_keeps_its_pre_upgrade_token(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="reported_usage", limit=2)
    response = client.post(
        "/v1/token",
        headers={"Authorization": f"Bearer {ISSUER_KEY}"},
        json={"subject_id": "42", "service_id": service_id, "request_key": "retry-me"},
    )
    # The token 0.10.x derived when subject IDs were integers.
    identity = json.dumps(
        ["demo-tenant", "issuer", 42, "example.run", "retry-me"], separators=(",", ":")
    )
    expected = "tq_" + hmac.new(SECRET.encode(), identity.encode(), hashlib.sha256).hexdigest()
    assert response.json()["token"] == expected
