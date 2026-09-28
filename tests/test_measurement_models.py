"""Both measurement quota layouts, configured and consumed through the public API."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaKit, utc_now
from tekes_quota_kit.models import Base, Ledger, Token, TokenItem, Usage

TENANT = "shukang-zhiyi"
SUBJECT = 42
ADMIN_KEY = "measurement-model-admin-key-for-tests-001"
SECRET = "measurement-model-token-secret-for-tests-01"
CHILDREN = (
    "blood_pressure",
    "blood_oxygen",
    "body_temperature",
    "body_composition",
    "body_circumference",
    "ecg",
)


def setup(tmp_path):
    kit = QuotaKit(f"sqlite:///{tmp_path / 'measurement-models.db'}", SECRET)
    Base.metadata.create_all(kit.engine)
    client = TestClient(create_app(kit, ADMIN_KEY))
    admin_put(client, f"/v1/admin/tenants/{TENANT}/levels/basic", {})
    admin_put(
        client,
        f"/v1/admin/tenants/{TENANT}/subjects/{SUBJECT}/level",
        {
            "level_code": "basic",
            "expires_at": (utc_now() + timedelta(days=30)).isoformat() + "Z",
        },
    )
    return kit, client


def admin_put(client, path, payload):
    response = client.put(path, headers={"Authorization": f"Bearer {ADMIN_KEY}"}, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def configure_quota(client, code, limit):
    admin_put(
        client,
        f"/v1/admin/tenants/{TENANT}/quotas/{code}",
        {"unit_code": "use", "metering_mode": "per_use"},
    )
    admin_put(
        client,
        f"/v1/admin/tenants/{TENANT}/levels/basic/limits/{code}",
        {
            "limit_mode": "finite",
            "limit_value": limit,
            "period_kind": "level_term",
            "timezone": "Asia/Shanghai",
        },
    )


def configure_client(client, code, key):
    admin_put(
        client,
        f"/v1/admin/clients/measurement-{code}",
        {
            "client_id": f"measurement-{code}",
            "key": key,
            "tenant_id": TENANT,
            "service_code": code,
            "role": "consumer",
        },
    )


def headers(key):
    return {"Authorization": f"Bearer {key}", "X-Subject-ID": str(SUBJECT)}


def test_one_measurement_session_grants_six_projects_and_charges_once(tmp_path):
    kit, client = setup(tmp_path)
    configure_quota(client, "measurement_count", 2)
    admin_put(
        client,
        f"/v1/admin/tenants/{TENANT}/services/measurement_session",
        {
            "quota_code": "measurement_count",
            "service_kind": "composite",
            "charge_units": 1,
            "session_ttl_seconds": 3600,
        },
    )
    for child in CHILDREN:
        admin_put(client, f"/v1/admin/tenants/{TENANT}/services/{child}", {})
        admin_put(
            client,
            f"/v1/admin/tenants/{TENANT}/services/measurement_session/members/{child}",
            {},
        )
    key = "measurement-session-consumer-key-for-tests-01"
    configure_client(client, "measurement_session", key)

    def begin(request_key):
        return client.post(
            "/v1/redeem",
            headers=headers(key),
            json={
                "subject_id": SUBJECT,
                "service_code": "measurement_session",
                "request_key": request_key,
            },
        )

    first = begin("visit-1")
    assert first.status_code == 200, first.text
    token = first.json()["token"]
    retry = begin("visit-1")
    assert retry.status_code == 200
    assert retry.json()["token"] == token
    assert retry.json()["idempotent"] is True
    with kit.sessions() as db:
        token_row = db.scalar(select(Token).where(Token.token_hash == kit.key_hash(token)))
        slots = db.scalars(select(TokenItem).where(TokenItem.token_id == token_row.id)).all()
        assert len(slots) == 6
        assert {slot.child_service_code for slot in slots} == set(CHILDREN)
        assert {slot.slot_no for slot in slots} == {0}
        assert all(slot.status == "available" for slot in slots)
        usage = db.scalar(select(Usage).where(Usage.quota_code == "measurement_count"))
        assert usage.used_units == 1
        assert [row.delta_units for row in db.scalars(select(Ledger)).all()] == [1]

    for child in CHILDREN:
        result = client.post(
            "/v1/use",
            headers=headers(key),
            json={
                "token": token,
                "child_service_code": child,
                "request_key": f"visit-1-{child}-1",
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "used"
    pressure_repeat = client.post(
        "/v1/use",
        headers=headers(key),
        json={
            "token": token,
            "child_service_code": "blood_pressure",
            "request_key": "visit-1-blood_pressure-2",
        },
    )
    assert pressure_repeat.status_code == 200
    assert pressure_repeat.json()["slot_no"] == 2
    for attempt in range(3, 23):
        repeated = client.post(
            "/v1/use",
            headers=headers(key),
            json={
                "token": token,
                "child_service_code": "blood_pressure",
                "request_key": f"visit-1-blood_pressure-{attempt}",
            },
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["slot_no"] == attempt
    assert (
        client.post("/v1/stop", headers=headers(key), json={"token": token}).json()[
            "session_status"
        ]
        == "closed"
    )
    with kit.sessions() as db:
        usage = db.scalar(select(Usage).where(Usage.quota_code == "measurement_count"))
        assert usage.used_units == 1
        assert [row.delta_units for row in db.scalars(select(Ledger)).all()] == [1]
    second = begin("visit-2")
    assert second.status_code == 200
    assert begin("visit-3").json()["code"] == "quota_exhausted"
    second_token = second.json()["token"]
    assert (
        client.post(
            "/v1/use",
            headers=headers(key),
            json={
                "token": second_token,
                "child_service_code": "blood_pressure",
                "request_key": "visit-2-blood-pressure",
            },
        ).status_code
        == 200
    )
    assert (
        client.post("/v1/stop", headers=headers(key), json={"token": second_token}).status_code
        == 200
    )
    missed = client.post(
        "/v1/use",
        headers=headers(key),
        json={
            "token": second_token,
            "child_service_code": "blood_oxygen",
            "request_key": "visit-2-late-oxygen",
        },
    )
    assert missed.json()["code"] == "session_not_open"


def test_each_measurement_project_has_its_own_quota_and_redeem(tmp_path):
    kit, client = setup(tmp_path)
    keys = {}
    for child in CHILDREN:
        configure_quota(client, f"{child}_count", 2)
        admin_put(
            client,
            f"/v1/admin/tenants/{TENANT}/services/{child}",
            {"quota_code": f"{child}_count", "service_kind": "atomic"},
        )
        keys[child] = f"measurement-{child}-consumer-key-for-tests-001"
        configure_client(client, child, keys[child])

    def redeem(child, request_key):
        return client.post(
            "/v1/redeem",
            headers=headers(keys[child]),
            json={
                "subject_id": SUBJECT,
                "service_code": child,
                "request_key": request_key,
            },
        )

    for child in CHILDREN:
        first = redeem(child, f"visit-1-{child}")
        assert first.status_code == 200, first.text
        assert first.json()["quota_code"] == f"{child}_count"
    assert redeem("blood_pressure", "visit-2-blood_pressure").status_code == 200
    assert redeem("blood_pressure", "visit-3-blood_pressure").json()["code"] == "quota_exhausted"
    retry = redeem("blood_oxygen", "visit-1-blood_oxygen")
    assert retry.status_code == 409
    assert retry.json()["code"] == "already_redeemed"
    assert redeem("blood_oxygen", "visit-2-blood_oxygen").status_code == 200
    for child in CHILDREN:
        balance = client.get("/v1/quota", headers=headers(keys[child]))
        assert balance.status_code == 200, balance.text
        assert balance.json()["quota_code"] == f"{child}_count"
        assert balance.json()["used"] == (
            2 if child in {"blood_pressure", "blood_oxygen"} else 1
        )
    with kit.sessions() as db:
        used = {row.quota_code: row.used_units for row in db.scalars(select(Usage)).all()}
        assert used == {
            f"{child}_count": (2 if child in {"blood_pressure", "blood_oxygen"} else 1)
            for child in CHILDREN
        }
        assert len(db.scalars(select(TokenItem)).all()) == 0
        assert len(db.scalars(select(Ledger)).all()) == 8
    wrong_scope = client.post(
        "/v1/redeem",
        headers=headers(keys["blood_oxygen"]),
        json={
            "subject_id": SUBJECT,
            "service_code": "blood_pressure",
            "request_key": "wrong-client",
        },
    )
    assert wrong_scope.status_code == 403


def test_switch_from_whole_session_to_independent_project_quotas(tmp_path):
    kit, client = setup(tmp_path)
    configure_quota(client, "measurement_count", 2)
    admin_put(
        client,
        f"/v1/admin/tenants/{TENANT}/services/measurement_session",
        {
            "quota_code": "measurement_count",
            "service_kind": "composite",
            "charge_units": 1,
            "session_ttl_seconds": 3600,
        },
    )
    for child in CHILDREN:
        admin_put(client, f"/v1/admin/tenants/{TENANT}/services/{child}", {})
        admin_put(
            client,
            f"/v1/admin/tenants/{TENANT}/services/measurement_session/members/{child}",
            {},
        )
    session_key = "switch-session-consumer-key-for-tests-0001"
    configure_client(client, "measurement_session", session_key)
    old = client.post(
        "/v1/redeem",
        headers=headers(session_key),
        json={
            "subject_id": SUBJECT,
            "service_code": "measurement_session",
            "request_key": "before-switch",
        },
    )
    assert old.status_code == 200, old.text
    old_token = old.json()["token"]

    keys = {}
    for child in CHILDREN:
        configure_quota(client, f"{child}_count", 2)
        admin_put(
            client,
            f"/v1/admin/tenants/{TENANT}/services/{child}",
            {"quota_code": f"{child}_count", "service_kind": "atomic"},
        )
        keys[child] = f"switch-{child}-consumer-key-for-tests-001"
        configure_client(client, child, keys[child])
        removed = client.delete(
            f"/v1/admin/tenants/{TENANT}/services/measurement_session/members/{child}",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        )
        assert removed.status_code == 200, removed.text
    admin_put(client, f"/v1/admin/tenants/{TENANT}/services/measurement_session", {})

    rejected = client.post(
        "/v1/redeem",
        headers=headers(session_key),
        json={
            "subject_id": SUBJECT,
            "service_code": "measurement_session",
            "request_key": "after-switch",
        },
    )
    assert rejected.json()["code"] == "no_quota"
    for child in CHILDREN:
        old_use = client.post(
            "/v1/use",
            headers=headers(session_key),
            json={
                "token": old_token,
                "child_service_code": child,
                "request_key": f"before-switch-{child}",
            },
        )
        assert old_use.status_code == 200, old_use.text
        new_use = client.post(
            "/v1/redeem",
            headers=headers(keys[child]),
            json={
                "subject_id": SUBJECT,
                "service_code": child,
                "request_key": f"after-switch-{child}",
            },
        )
        assert new_use.status_code == 200, new_use.text
    with kit.sessions() as db:
        used = {row.quota_code: row.used_units for row in db.scalars(select(Usage)).all()}
        assert used == {"measurement_count": 1, **{f"{child}_count": 1 for child in CHILDREN}}
