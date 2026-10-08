from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaKit, _period
from tekes_quota_kit.models import Base

ADMIN_KEY = "week-admin-key-for-tests-00000000000001"
SECRET = "week-token-secret-for-tests-000000000001"
DOOR_KEY = "door-backend-key-for-tests-0000000000001"
MEASURE_KEY = "measure-backend-key-for-tests-000000001"
TENANT = "shukang-zhiyi"


@pytest.mark.parametrize(
    ("now_utc", "start_utc", "end_utc"),
    [
        # Wednesday 2026-10-07 10:00 in Shanghai: week of Monday 2026-10-05.
        ("2026-10-07T02:00:00", "2026-10-04T16:00:00", "2026-10-11T16:00:00"),
        # Monday 00:00 exactly in Shanghai starts a new week.
        ("2026-10-11T16:00:00", "2026-10-11T16:00:00", "2026-10-18T16:00:00"),
        # One second earlier is still Sunday of the previous week.
        ("2026-10-11T15:59:59", "2026-10-04T16:00:00", "2026-10-11T16:00:00"),
        # Across a year boundary: Thursday 2027-01-01 is in the week of Monday 2026-12-28.
        ("2027-01-01T04:00:00", "2026-12-27T16:00:00", "2027-01-03T16:00:00"),
    ],
)
def test_week_runs_monday_to_monday_in_the_limit_timezone(now_utc, start_utc, end_utc):
    start, end = _period(datetime.fromisoformat(now_utc), "week", "Asia/Shanghai", None)
    assert (start.isoformat(), end.isoformat()) == (start_utc, end_utc)


def _shukang(tmp_path) -> TestClient:
    """The Shukang rules: door opens charge at once, measurements charge on completion."""
    kit = QuotaKit(f"sqlite:///{tmp_path / 'week.db'}", SECRET)
    Base.metadata.create_all(kit.engine)
    client = TestClient(create_app(kit, ADMIN_KEY))
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    base = f"/v1/admin/tenants/{TENANT}"
    calls = [
        ("/quotas/door_open_count", {"unit_code": "use", "metering_mode": "per_use"}),
        (
            "/quotas/measurement_count",
            {"unit_code": "measurement", "metering_mode": "reported_usage"},
        ),
        ("/levels/member", {}),
    ]
    for quota in ("door_open_count", "measurement_count"):
        calls.append(
            (
                f"/levels/member/limits/{quota}",
                {
                    "limit_mode": "finite",
                    "limit_value": 2,
                    "period_kind": "week",
                    "timezone": "Asia/Shanghai",
                },
            )
        )
    calls += [
        ("/services/door_open", {"quota_code": "door_open_count"}),
        ("/services/health_measurement", {"quota_code": "measurement_count"}),
        ("/subjects/42/level", {"level_code": "member"}),
    ]
    for path, body in calls:
        response = client.put(base + path, json=body, headers=admin)
        assert response.status_code == 200, (path, response.text)
    kit.put_client("door-backend", DOOR_KEY, TENANT, "door_open", "consumer")
    kit.put_client("measure-backend", MEASURE_KEY, TENANT, "health_measurement", "consumer")
    return client


def test_door_open_allows_two_redemptions_a_week(tmp_path):
    client = _shukang(tmp_path)
    door = {"Authorization": f"Bearer {DOOR_KEY}"}
    for key in ("door-1", "door-2"):
        response = client.post(
            "/v1/redeem",
            headers=door,
            json={"subject_id": 42, "service_code": "door_open", "request_key": key},
        )
        assert response.status_code == 200, response.text
    third = client.post(
        "/v1/redeem",
        headers=door,
        json={"subject_id": 42, "service_code": "door_open", "request_key": "door-3"},
    )
    assert third.status_code == 409 and third.json()["code"] == "quota_exhausted"


def test_measurement_charges_only_when_completed(tmp_path):
    client = _shukang(tmp_path)
    measure = {"Authorization": f"Bearer {MEASURE_KEY}"}
    service_id = 2  # health_measurement, created second

    def start(key: str):
        return client.post(
            "/v1/token",
            headers=measure,
            json={"subject_id": 42, "service_id": service_id, "request_key": key},
        )

    def finish(token: str, units: int):
        response = client.post(
            "/v1/token/settle", headers=measure, json={"token": token, "consumed_units": units}
        )
        assert response.status_code == 200, response.text

    # A failed measurement settles 0 and does not use the allowance.
    finish(start("m-failed").json()["token"], 0)
    for key in ("m-1", "m-2"):
        finish(start(key).json()["token"], 1)
    third = start("m-3")
    assert third.status_code == 409 and third.json()["code"] == "quota_exhausted"
