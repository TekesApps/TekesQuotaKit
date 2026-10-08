from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaKit, utc_now
from tekes_quota_kit.models import Assignment, Base, Service, ServiceMember, Token, TokenItem, Usage

ADMIN_KEY = "composite-admin-key-for-local-tests-0001"
CLIENT_KEY = "composite-client-key-for-local-tests-001"
SECRET = "composite-token-secret-for-local-tests-01"
TENANT = "demo-tenant"


def configured(tmp_path, *, limit: int = 2, ttl: int | None = 3600):
    kit = QuotaKit(f"sqlite:///{tmp_path / 'composite.db'}", SECRET)
    Base.metadata.create_all(kit.engine)
    kit.put_quota(TENANT, "measurement_count", "use", "per_use")
    kit.put_level(TENANT, "basic")
    kit.put_level(TENANT, "plus")
    kit.put_limit(
        TENANT, "basic", "measurement_count", "finite", limit, "level_term", "Asia/Shanghai"
    )
    kit.put_limit(
        TENANT, "plus", "measurement_count", "finite", limit, "level_term", "Asia/Shanghai"
    )
    kit.assign(TENANT, 42, "basic", utc_now() + timedelta(days=30))
    kit.put_service(
        TENANT,
        "measurement_session",
        "measurement_count",
        kind="composite",
        charge_units=1,
        session_ttl_seconds=ttl,
    )
    kit.put_service(TENANT, "blood_pressure", None)
    kit.put_service(TENANT, "blood_oxygen", None)
    kit.put_service_member(TENANT, "measurement_session", "blood_pressure")
    kit.put_service_member(TENANT, "measurement_session", "blood_oxygen")
    kit.put_client("measurement-backend", CLIENT_KEY, TENANT, "measurement_session", "consumer")
    return kit, TestClient(create_app(kit, ADMIN_KEY))


def headers():
    return {"Authorization": f"Bearer {CLIENT_KEY}", "X-Subject-ID": "42"}


def begin(client, key="session-501"):
    return client.post(
        "/v1/redeem",
        headers=headers(),
        json={
            "subject_id": 42,
            "service_code": "measurement_session",
            "request_key": key,
        },
    )


def use(client, token, child="blood_pressure", key="bp-1"):
    return client.post(
        "/v1/use",
        headers=headers(),
        json={
            "token": token,
            "child_service_code": child,
            "request_key": key,
        },
    )


def test_durable_redeem_snapshots_children_and_charges_once(tmp_path):
    kit, client = configured(tmp_path)
    first = begin(client)
    assert first.status_code == 200, first.text
    token = first.json()["token"]
    assert first.json()["idempotent"] is False
    retry = begin(client)
    assert retry.status_code == 200
    assert retry.json()["token"] == token
    assert retry.json()["idempotent"] is True
    assert kit.balance(kit.client(CLIENT_KEY, "issuer"), 42)["used"] == 1
    with kit.sessions() as db:
        row = db.scalar(select(Token).where(Token.token_hash == kit.key_hash(token)))
        assert row.status == "settled" and row.session_status == "open"
        slots = db.scalars(select(TokenItem).where(TokenItem.token_id == row.id)).all()
        assert len(slots) == 2
        assert {item.slot_no for item in slots} == {0}
    assert use(client, token).json() == {
        "child_service_code": "blood_pressure",
        "slot_no": 1,
        "status": "used",
        "idempotent": False,
    }
    assert use(client, token).json()["idempotent"] is True
    for attempt in range(2, 22):
        assert use(client, token, key=f"bp-{attempt}").json()["slot_no"] == attempt
    assert use(client, token, "not_in_session", "unknown").json()["code"] == "child_unavailable"
    assert use(client, token, "blood_oxygen", "oxygen-1").status_code == 200
    assert kit.balance(kit.client(CLIENT_KEY, "issuer"), 42)["used"] == 1
    assert (
        client.post("/v1/token/refund", headers=headers(), json={"token": token}).json()["code"]
        == "child_already_used"
    )
    assert (
        client.post("/v1/stop", headers=headers(), json={"token": token}).json()["session_status"]
        == "closed"
    )
    assert use(client, token, "blood_oxygen", "oxygen-2").json()["code"] == "session_not_open"


def test_stop_expires_unused_and_zero_use_can_refund(tmp_path):
    kit, client = configured(tmp_path)
    token = begin(client).json()["token"]
    assert (
        client.post("/v1/stop", headers=headers(), json={"token": token}).json()["idempotent"]
        is False
    )
    assert (
        client.post("/v1/stop", headers=headers(), json={"token": token}).json()["idempotent"]
        is True
    )
    with kit.sessions() as db:
        row = db.scalar(select(Token).where(Token.token_hash == kit.key_hash(token)))
        assert row.status == "settled" and row.session_status == "closed"
        assert {
            item.status
            for item in db.scalars(select(TokenItem).where(TokenItem.token_id == row.id))
        } == {"expired"}
    refunded = client.post("/v1/token/refund", headers=headers(), json={"token": token})
    assert refunded.status_code == 200
    assert kit.balance(kit.client(CLIENT_KEY, "issuer"), 42)["used"] == 0


def test_use_and_close_race_serializes_and_expiry_rejects(tmp_path):
    kit, client = configured(tmp_path)
    token = begin(client).json()["token"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: use(client, token, key="bp-race"), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert sorted(r.json()["idempotent"] for r in results) == [False, True]
    with kit.sessions.begin() as db:
        row = db.scalar(select(Token).where(Token.token_hash == kit.key_hash(token)))
        row.session_expires_at = utc_now() - timedelta(seconds=1)
    assert (
        use(client, token, "blood_oxygen", "oxygen-after-expiry").json()["code"]
        == "session_not_open"
    )
    assert use(client, token, key="bp-race").json()["code"] == "session_not_open"
    assert kit.expire_sessions() == 1
    assert kit.expire_sessions() == 0
    assert (
        client.post("/v1/token/refund", headers=headers(), json={"token": token}).json()["code"]
        == "child_already_used"
    )


def test_upgrade_keeps_term_usage_and_renewal_resets_it(tmp_path):
    kit, client = configured(tmp_path, limit=1)
    assert begin(client).status_code == 200
    with kit.sessions() as db:
        original = db.scalar(select(Assignment).where(Assignment.subject_id == 42))
        first_term_start = original.term_start
    kit.assign(TENANT, 42, "plus")
    with kit.sessions() as db:
        current = db.scalars(
            select(Assignment).where(Assignment.subject_id == 42).order_by(Assignment.id.desc())
        ).first()
        assert current.term_start == first_term_start
    assert begin(client, "session-502").json()["code"] == "quota_exhausted"
    kit.assign(TENANT, 42, "basic", utc_now() + timedelta(days=30), renew_term=True)
    assert begin(client, "session-503").status_code == 200


def test_membership_change_only_affects_later_sessions(tmp_path):
    kit, client = configured(tmp_path)
    old_token = begin(client).json()["token"]
    kit.delete_service_member(TENANT, "measurement_session", "blood_oxygen")
    new_token = begin(client, "session-502").json()["token"]
    assert use(client, old_token, "blood_oxygen", "old-oxygen").status_code == 200
    assert (
        use(client, new_token, "blood_oxygen", "new-oxygen").json()["code"]
        == "child_unavailable"
    )


def test_child_can_belong_to_two_composites_with_independent_use_limits(tmp_path):
    kit, client = configured(tmp_path)
    kit.put_service_member(TENANT, "measurement_session", "blood_pressure", 2)
    kit.put_quota(TENANT, "station_count", "use", "per_use")
    kit.put_limit(
        TENANT, "basic", "station_count", "finite", 1, "level_term", "Asia/Shanghai"
    )
    kit.put_service(
        TENANT, "measurement_station", "station_count", kind="composite", charge_units=1
    )
    kit.put_service_member(TENANT, "measurement_station", "blood_pressure", 1)
    station_key = "station-consumer-key-for-local-tests-001"
    kit.put_client("station-backend", station_key, TENANT, "measurement_station", "consumer")
    station_headers = {"Authorization": f"Bearer {station_key}", "X-Subject-ID": "42"}

    with kit.sessions() as db:
        assert len(
            db.scalars(select(Service).where(Service.service_code == "blood_pressure")).all()
        ) == 1
        rows = db.scalars(
            select(ServiceMember).where(ServiceMember.child_service_code == "blood_pressure")
        ).all()
        assert {row.parent_service_code: row.max_uses for row in rows} == {
            "measurement_session": 2,
            "measurement_station": 1,
        }

    session_token = begin(client).json()["token"]
    station_result = client.post(
        "/v1/redeem",
        headers=station_headers,
        json={
            "subject_id": 42,
            "service_code": "measurement_station",
            "request_key": "station-session-501",
        },
    )
    assert station_result.status_code == 200, station_result.text
    station_token = station_result.json()["token"]
    with kit.sessions() as db:
        station_row = db.scalar(
            select(Token).where(Token.token_hash == kit.key_hash(station_token))
        )
        assert station_row.quota_code == "station_count"
        usage = {row.quota_code: row.used_units for row in db.scalars(select(Usage)).all()}
        assert usage == {"measurement_count": 1, "station_count": 1}

    # Changing the membership only changes future grants, not these two tokens.
    kit.put_service_member(TENANT, "measurement_session", "blood_pressure", None)
    kit.put_service_member(TENANT, "measurement_station", "blood_pressure", 3)
    assert use(client, session_token, key="session-bp-1").status_code == 200
    assert use(client, session_token, key="session-bp-2").status_code == 200
    assert use(client, session_token, key="session-bp-2").json()["idempotent"] is True
    assert use(client, session_token, key="session-bp-3").json()["code"] == "child_unavailable"
    station_use = client.post(
        "/v1/use",
        headers=station_headers,
        json={
            "token": station_token,
            "child_service_code": "blood_pressure",
            "request_key": "station-bp-1",
        },
    )
    assert station_use.status_code == 200, station_use.text
    station_repeat = client.post(
        "/v1/use",
        headers=station_headers,
        json={
            "token": station_token,
            "child_service_code": "blood_pressure",
            "request_key": "station-bp-2",
        },
    )
    assert station_repeat.json()["code"] == "child_unavailable"
    with kit.sessions() as db:
        grants = db.scalars(select(TokenItem).where(TokenItem.slot_no == 0)).all()
        assert sorted(
            row.max_uses for row in grants if row.child_service_code == "blood_pressure"
        ) == [1, 2]


def test_begin_retry_recovers_token_after_parent_config_changes(tmp_path):
    kit, client = configured(tmp_path)
    token = begin(client).json()["token"]
    kit.put_service(TENANT, "measurement_session", None)
    retry = begin(client)
    assert retry.status_code == 200
    assert retry.json()["token"] == token
    assert retry.json()["idempotent"] is True
    assert begin(client, "session-502").json()["code"] == "no_quota"


def test_durable_without_ttl_waits_for_stop(tmp_path):
    kit, client = configured(tmp_path, ttl=None)
    started = begin(client)
    assert started.status_code == 200
    assert started.json()["expires_at"] is None
    token = started.json()["token"]
    for attempt in range(1, 12):
        assert use(client, token, key=f"pressure-{attempt}").status_code == 200
    assert kit.expire_sessions() == 0
    assert client.post("/v1/stop", headers=headers(), json={"token": token}).status_code == 200
    assert use(client, token, key="after-stop").json()["code"] == "session_not_open"


def test_atomic_durable_service_and_redeem_duration_override(tmp_path):
    kit, client = configured(tmp_path)
    kit.put_service(
        TENANT,
        "single_device",
        "measurement_count",
        kind="atomic",
        redemption_mode="durable",
        charge_units=1,
    )
    key = "single-device-durable-client-key-for-tests-001"
    kit.put_client("single-device", key, TENANT, "single_device", "consumer")
    device_headers = {"Authorization": f"Bearer {key}", "X-Subject-ID": "42"}
    started = client.post(
        "/v1/redeem",
        headers=device_headers,
        json={
            "subject_id": 42,
            "service_code": "single_device",
            "request_key": "device-session-1",
            "duration_seconds": 60,
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["authorized_services"] == ["single_device"]
    assert started.json()["expires_at"] is not None
    token = started.json()["token"]
    for attempt in range(1, 6):
        result = client.post(
            "/v1/use",
            headers=device_headers,
            json={
                "token": token,
                "child_service_code": "single_device",
                "request_key": f"device-attempt-{attempt}",
            },
        )
        assert result.status_code == 200, result.text
    assert (
        client.post("/v1/stop", headers=device_headers, json={"token": token}).json()[
            "session_status"
        ]
        == "closed"
    )
