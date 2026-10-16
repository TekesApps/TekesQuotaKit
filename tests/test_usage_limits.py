"""Usage table caps refer to the membership counter's current period only."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from test_quota_flow import ADMIN_KEY, configured, issue, provider_headers

from tekes_quota_kit.core import _period, utc_now
from tekes_quota_kit.models import Usage

TENANT = "demo-tenant"


def sample(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="reported_usage", limit=500000)
    token = issue(client, service_id, "usage-table")
    assert (
        client.post(
            "/v1/token/settle",
            headers=provider_headers(),
            json={"token": token, "consumed_units": 152159},
        ).status_code
        == 200
    )
    return kit, client


def page(client):
    return client.get(
        "/v1/admin/tables/tq_usage",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        params={"tenant": TENANT, "subject_id": "42"},
    ).json()


def test_usage_table_adds_current_finite_limit_without_changing_counter(tmp_path):
    kit, client = sample(tmp_path)
    data = page(client)
    assert "limit_value" in data["columns"]
    row = data["rows"][0]
    assert row["used_units"] == 152159
    assert row["limit_value"] == 500000
    assert row["limit_mode"] == "finite" and row["limit_status"] == "current"
    with kit.sessions() as db:
        assert db.scalar(select(Usage)).used_units == 152159


def test_usage_table_unlimited_and_zero_caps_are_distinct(tmp_path):
    kit, client = sample(tmp_path)
    for mode, value in [("unlimited", None), ("finite", 0), ("finite", 100000)]:
        kit.put_limit(TENANT, "regular", "example", mode, value, "month", "Asia/Shanghai")
        row = page(client)["rows"][0]
        assert row["limit_mode"] == mode
        assert row["limit_value"] == value
        assert row["limit_status"] == "current"


def test_historical_period_and_missing_membership_do_not_invent_limits(tmp_path):
    kit, client = sample(tmp_path)
    with kit.sessions.begin() as db:
        current = db.scalar(select(Usage))
        db.add(
            Usage(
                tenant_id=TENANT,
                subject_id="42",
                quota_code="example",
                used_units=1234,
                period_start=current.period_start - timedelta(days=40),
                period_end=current.period_end - timedelta(days=40),
            )
        )
    rows = page(client)["rows"]
    assert rows[1]["limit_status"] == "historical"
    assert rows[1]["limit_value"] is None and rows[1]["limit_mode"] is None
    assert rows[0]["limit_status"] == "current"
    kit.end_membership(TENANT, "42")
    assert all(r["limit_status"] == "unavailable" for r in page(client)["rows"])


def test_usage_caps_require_admin_and_stay_tenant_scoped(tmp_path):
    _, client = sample(tmp_path)
    assert client.get("/v1/admin/tables/tq_usage?tenant=" + TENANT).status_code == 401
    other = client.get(
        "/v1/admin/tables/tq_usage",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        params={"tenant": "other-tenant"},
    ).json()
    assert other["total"] == 0 and other["rows"] == []


def sorting_sample(tmp_path):
    kit, client = sample(tmp_path)
    for level, cap in [("small", 100), ("over", 100), ("zero", 0), ("VIP", None)]:
        kit.put_level(TENANT, level)
        kit.put_limit(
            TENANT,
            level,
            "example",
            "unlimited" if cap is None else "finite",
            cap,
            "month",
            "Asia/Shanghai",
        )
    with kit.sessions() as db:
        original = db.scalar(select(Usage))
        start, end = original.period_start, original.period_end
    for subject, level, used in [
        ("43", "small", 90),
        ("44", "over", 250),
        ("45", "regular", 125000),
        ("46", "over", 250),
        ("47", "zero", 0),
        ("48", "VIP", 900000),
        ("49", "regular", 400000),
        ("50", "small", 0),
        ("51", "regular", 152158),
    ]:
        kit.assign(TENANT, subject, level)
        with kit.sessions.begin() as db:
            db.add(
                Usage(
                    tenant_id=TENANT,
                    subject_id=subject,
                    quota_code="example",
                    period_start=start,
                    period_end=end,
                    used_units=used,
                )
            )
    kit.end_membership(TENANT, "49")
    with kit.sessions.begin() as db:
        db.add(
            Usage(
                tenant_id=TENANT,
                subject_id="42",
                quota_code="example",
                period_start=start - timedelta(days=60),
                period_end=end - timedelta(days=60),
                used_units=999999,
            )
        )
    # A second Quota and another tenant must not affect filtered ordering/counts.
    kit.put_quota(TENANT, "unrelated", "use", "per_use")
    kit.put_limit(TENANT, "regular", "unrelated", "finite", 100, "month", "Asia/Shanghai")
    with kit.sessions.begin() as db:
        db.add(
            Usage(
                tenant_id=TENANT,
                subject_id="42",
                quota_code="unrelated",
                period_start=start,
                period_end=end,
                used_units=90,
            )
        )
        db.add(
            Usage(
                tenant_id="other-tenant",
                subject_id="43",
                quota_code="example",
                period_start=start,
                period_end=end,
                used_units=999999999,
            )
        )
    return kit, client


@pytest.mark.parametrize(
    "direction, expected",
    [
        ("desc", ["46", "44", "43", "42", "51", "45", "50"]),
        ("asc", ["50", "45", "51", "42", "43", "46", "44"]),
    ],
)
def test_consumption_sort_is_global_before_pagination_with_nulls_last(
    tmp_path, direction, expected
):
    _, client = sorting_sample(tmp_path)
    result = []
    for offset in range(0, 11, 2):
        params = {"tenant": TENANT, "quota_code": "example", "offset": offset, "limit": 2}
        # Omission is the default descending order.
        if direction == "asc":
            params["consumption_order"] = direction
        response = client.get(
            "/v1/admin/tables/tq_usage",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
            params=params,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 11
        result.extend(data["rows"])
    assert [r["subject_id"] for r in result[:7]] == expected
    assert [r["subject_id"] for r in result[7:]] == ["42", "49", "48", "47"]
    assert len({r["id"] for r in result}) == 11
    assert all(r["tenant_id"] == TENANT and r["quota_code"] == "example" for r in result)
    # The explicit descending option has exactly the same first page as the default.
    explicit = client.get(
        "/v1/admin/tables/tq_usage",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        params={
            "tenant": TENANT,
            "quota_code": "example",
            "consumption_order": direction,
            "limit": 2,
        },
    ).json()
    assert [r["id"] for r in explicit["rows"]] == [r["id"] for r in result[:2]]


@pytest.mark.parametrize("period", ["day", "week", "month", "level_term"])
def test_sort_matches_calendar_timezone_and_membership_term_context(tmp_path, period):
    kit, client = sample(tmp_path)
    kit.put_level(TENANT, "calendar")
    kit.put_limit(TENANT, "calendar", "example", "finite", 100, period, "America/New_York")
    kit.assign(TENANT, "43", "calendar", utc_now() + timedelta(days=30))
    with kit.sessions.begin() as db:
        assignment = kit._assignment(db, TENANT, "43", utc_now())
        start, end = _period(utc_now(), period, "America/New_York", assignment)
        db.add(
            Usage(
                tenant_id=TENANT,
                subject_id="43",
                quota_code="example",
                period_start=start,
                period_end=end,
                used_units=99,
            )
        )
        db.add(
            Usage(
                tenant_id=TENANT,
                subject_id="43",
                quota_code="example",
                period_start=start - timedelta(days=60),
                period_end=end - timedelta(days=60),
                used_units=999,
            )
        )
    data = client.get(
        "/v1/admin/tables/tq_usage",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        params={"tenant": TENANT},
    ).json()
    assert [r["subject_id"] for r in data["rows"]] == ["43", "42", "43"]
    assert data["rows"][0]["limit_value"] == 100
    assert data["rows"][0]["limit_status"] == "current"
    assert data["rows"][-1]["limit_status"] == "historical"


def test_consumption_sort_rejects_invalid_direction_and_other_tables(tmp_path):
    _, client = sample(tmp_path)
    h = {"Authorization": f"Bearer {ADMIN_KEY}"}
    assert (
        client.get(
            "/v1/admin/tables/tq_usage",
            headers=h,
            params={"tenant": TENANT, "consumption_order": "invalid"},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/v1/admin/tables/tq_limits",
            headers=h,
            params={"tenant": TENANT, "consumption_order": "asc"},
        ).status_code
        == 400
    )


def test_existing_assignment_priority_filter_is_not_reserved_for_usage_sort(tmp_path):
    _, client = sample(tmp_path)
    response = client.get(
        "/v1/admin/tables/tq_assignments",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        params={"tenant": TENANT, "sort_order": 0},
    )
    assert response.status_code == 200
    assert response.json()["rows"] and all(r["sort_order"] == 0 for r in response.json()["rows"])
