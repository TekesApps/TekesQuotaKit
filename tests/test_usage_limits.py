"""Usage table caps refer to the membership counter's current period only."""

from datetime import timedelta

from sqlalchemy import select
from test_quota_flow import ADMIN_KEY, configured, issue, provider_headers

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
    assert rows[0]["limit_status"] == "historical"
    assert rows[0]["limit_value"] is None and rows[0]["limit_mode"] is None
    assert rows[1]["limit_status"] == "current"
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
