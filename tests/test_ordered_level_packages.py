from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_quota_flow import ADMIN_KEY, PROVIDER_KEY, configured, issue, provider_headers, redeem

from tekes_quota_kit.core import QuotaError, utc_now
from tekes_quota_kit.models import LevelPackageCharge, LevelPackageUsage

TENANT = "demo-tenant"
ADMIN = {"Authorization": f"Bearer {ADMIN_KEY}"}


def existing(kit):
    state = kit.user_packages(TENANT, 42)
    items = [
        dict(id=p["id"], level_code=p["level_code"], expires_at=None) for p in state["packages"]
    ]
    return state, items


def add(kit, levels, *, before=False):
    state, items = existing(kit)
    extra = [dict(level_code=level) for level in levels]
    return kit.save_user_packages(
        TENANT, 42, extra + items if before else items + extra, state["revision"]
    )


def level(kit, code, value, period="month", quota="example"):
    kit.put_level(TENANT, code)
    kit.put_limit(
        TENANT,
        code,
        quota,
        "unlimited" if value is None else "finite",
        value,
        period,
        "Asia/Shanghai",
    )


def balance(kit):
    return kit.balance(kit.client(PROVIDER_KEY, "provider"), 42)


def test_order_change_preserves_usage_and_refund_original_package(tmp_path):
    kit, client, service = configured(tmp_path, mode="per_use", limit=2)
    level(kit, "extra", 3)
    add(kit, ["extra", "extra"], before=True)
    token = redeem(client, service, "first").json()["token"]
    assert [p["used"] for p in balance(kit)["level_packages"]] == [1, 0, 0]
    state, items = existing(kit)
    kit.save_user_packages(TENANT, 42, list(reversed(items)), state["revision"])
    assert [p["used"] for p in balance(kit)["level_packages"]] == [0, 0, 1]
    assert redeem(client, service, "next").status_code == 200
    state, items = existing(kit)
    # Removing the original charged package cannot move its refund onto another package.
    kit.save_user_packages(TENANT, 42, items[:-1], state["revision"])
    assert (
        client.post(
            "/v1/token/refund", headers=provider_headers(), json={"token": token}
        ).status_code
        == 200
    )
    with kit.sessions() as db:
        assert db.scalar(select(LevelPackageUsage.used_units)) == 0
    assert balance(kit)["used"] == 1
    assert balance(kit)["remaining"] == 4


def test_service_supported_only_by_later_level_and_shared_quota(tmp_path):
    kit, _, _ = configured(tmp_path, mode="per_use", limit=1)
    kit.put_quota(TENANT, "special", "use", "per_use")
    kit.put_service(TENANT, "special.one", "special")
    kit.put_service(TENANT, "special.two", "special")
    level(kit, "addon", 2, quota="special")
    add(kit, ["addon"])
    for n, service in enumerate(["special.one", "special.two"]):
        key = f"special-provider-key-0000000000000{n}"
        kit.put_client(f"special{n}", key, TENANT, service, "consumer")
        caller = kit.client(key, "provider")
        assert kit.redeem(caller, 42, service, f"special{n}")["status"] == "settled"
    assert kit.balance(caller, 42)["remaining"] == 0
    with pytest.raises(QuotaError, match="Quota exhausted"):
        kit.redeem(caller, 42, "special.two", "exhausted")
    assert balance(kit)["remaining"] == 1
    assert {q["quota_code"] for q in kit.subject_usage(TENANT, 42)["quotas"]} == {
        "example",
        "special",
    }


def test_split_charge_across_periods_and_exact_refund(tmp_path):
    kit, client, _ = configured(tmp_path, mode="per_use", limit=1)
    level(kit, "weekly", 2, "week")
    add(kit, ["weekly"])
    kit.put_service(TENANT, "example.run", "example", redemption_mode="durable", charge_units=3)
    caller = kit.client(PROVIDER_KEY, "provider")
    token = kit.begin(caller, 42, "example.run", "split")["token"]
    assert balance(kit)["remaining"] == 0
    with kit.sessions() as db:
        assert [
            c.units for c in db.scalars(select(LevelPackageCharge).order_by(LevelPackageCharge.id))
        ] == [1, 2]
    assert kit.refund(token, caller, 42)["status"] == "refunded"
    assert kit.refund(token, caller, 42)["idempotent"]
    assert balance(kit)["remaining"] == 3


def test_independent_period_reset_and_pending_expired_packages(tmp_path, monkeypatch):
    kit, client, service = configured(tmp_path, mode="per_use", limit=0)
    level(kit, "daily", 1, "day")
    level(kit, "term", 2, "level_term")
    now = utc_now()
    state, items = existing(kit)
    items += [
        dict(level_code="daily"),
        dict(level_code="term", expires_at=now + timedelta(days=40)),
        dict(level_code="daily", effective_at=now + timedelta(days=35)),
        dict(
            level_code="daily",
            effective_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        ),
    ]
    kit.save_user_packages(TENANT, 42, items, state["revision"])
    assert balance(kit)["remaining"] == 3
    for n in range(2):
        assert redeem(client, service, f"today{n}").status_code == 200
    assert balance(kit)["remaining"] == 1
    monkeypatch.setattr("tekes_quota_kit.core.utc_now", lambda: now + timedelta(days=1))
    assert balance(kit)["remaining"] == 2  # daily reset; term still consumed once


def test_metered_snapshot_survives_reorder_expiry_and_removal(tmp_path, monkeypatch):
    kit, client, service = configured(tmp_path, mode="reported_usage", limit=1)
    level(kit, "extra", 2)
    add(kit, ["extra"], before=True)
    token = issue(client, service, "admit")
    state, items = existing(kit)
    kit.save_user_packages(TENANT, 42, items[1:], state["revision"])
    monkeypatch.setattr("tekes_quota_kit.core.utc_now", lambda: utc_now() + timedelta(days=32))
    response = client.post(
        "/v1/token/settle", headers=provider_headers(), json={"token": token, "consumed_units": 5}
    )
    assert response.status_code == 200
    with kit.sessions() as db:
        assert db.scalar(select(LevelPackageUsage.used_units)) == 4  # 2 + reported overage
        assert sum(c.units for c in db.scalars(select(LevelPackageCharge))) == 5
    assert client.post(
        "/v1/token/settle", headers=provider_headers(), json={"token": token, "consumed_units": 5}
    ).json()["idempotent"]


def test_unlimited_obeys_priority(tmp_path):
    kit, client, service = configured(tmp_path, mode="per_use", limit=1)
    level(kit, "forever", None)
    add(kit, ["forever"])
    for n in range(3):
        assert redeem(client, service, str(n)).status_code == 200
    assert [p["used"] for p in balance(kit)["level_packages"]] == [1, 2]
    assert balance(kit)["remaining"] is None


def test_revision_id_validation_and_atomic_rollback(tmp_path):
    kit, client, _ = configured(tmp_path, mode="per_use", limit=2)
    endpoint = f"/v1/admin/tenants/{TENANT}/subjects/42/level-packages"
    state = client.get(endpoint, headers=ADMIN).json()
    row = state["packages"][0]
    payload = dict(
        revision=state["revision"],
        packages=[dict(id=row["id"], level_code=row["level_code"], expires_at=row["expires_at"])],
    )
    assert client.put(endpoint, headers=ADMIN, json=payload).status_code == 200
    assert client.put(endpoint, headers=ADMIN, json=payload).json()["code"] == "packages_changed"
    payload["revision"] += 1
    payload["packages"].append(dict(id=999, level_code="regular"))
    assert client.put(endpoint, headers=ADMIN, json=payload).json()["code"] == "invalid_package"
    assert client.get(endpoint, headers=ADMIN).json()["revision"] == payload["revision"]
    assert client.put(endpoint, json=payload).status_code == 401
    other = endpoint.replace("demo-tenant", "other")
    assert client.get(other, headers=ADMIN).json()["packages"] == []
    payload["revision"] = 0
    assert client.put(other, headers=ADMIN, json=payload).json()["code"] == "unknown_level"


def test_concurrent_last_unit_and_sync_preserves_extras(tmp_path):
    kit, client, service = configured(tmp_path, mode="per_use", limit=0)
    level(kit, "extra", 1)
    add(kit, ["extra"], before=True)
    kit.assign(TENANT, 42, "regular")
    assert [p["level_code"] for p in kit.user_packages(TENANT, 42)["packages"]] == [
        "extra",
        "regular",
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda n: redeem(client, service, f"race{n}"), range(4)))
    assert sorted(r.status_code for r in results) == [200, 409, 409, 409]
    assert kit.end_membership(TENANT, 42)
    assert kit.membership(TENANT, 42) is None
    assert redeem(client, service, "after-end").json()["code"] == "no_level"


def test_issued_expiry_is_read_only_and_duplicate_levels_are_independent(tmp_path):
    kit, client, service = configured(tmp_path, mode="per_use", limit=0)
    level(kit, "term", 1, "level_term")
    now = utc_now()
    state, items = existing(kit)
    items += [dict(level_code="term", expires_at=now + timedelta(days=1))] * 2
    kit.save_user_packages(TENANT, 42, items, state["revision"])
    assert redeem(client, service, "term1").status_code == 200
    state = kit.user_packages(TENANT, 42)
    items = [
        dict(
            id=p["id"],
            level_code=p["level_code"],
            expires_at=now + timedelta(days=10) if p["level_code"] == "term" else None,
        )
        for p in state["packages"]
    ]
    with pytest.raises(QuotaError, match="Issued package expiry is read-only"):
        kit.save_user_packages(TENANT, 42, items, state["revision"])
    assert kit.user_packages(TENANT, 42) == state
    assert balance(kit)["remaining"] == 1
    assert redeem(client, service, "term2").status_code == 200
    assert redeem(client, service, "term3").json()["code"] == "quota_exhausted"


def test_end_cancels_scheduled_packages_and_user_list_pages_people(tmp_path, monkeypatch):
    kit, client, _ = configured(tmp_path, mode="per_use", limit=1)
    level(kit, "extra", 1)
    now = utc_now()
    state, items = existing(kit)
    kit.save_user_packages(
        TENANT,
        42,
        items + [dict(level_code="extra", effective_at=now + timedelta(days=1))],
        state["revision"],
    )
    assert kit.end_membership(TENANT, 42)
    assert not kit.end_membership(TENANT, 42)
    monkeypatch.setattr("tekes_quota_kit.core.utc_now", lambda: now + timedelta(days=2))
    assert kit.membership(TENANT, 42) is None
    monkeypatch.undo()
    for subject in range(100, 112):
        kit.assign(TENANT, subject, "regular")
        kit.assign(TENANT, subject, "regular")
    url = f"/v1/admin/tenants/{TENANT}/subjects"
    result = client.get(url, headers=ADMIN).json()
    assert result["total"] == 13 and len(result["rows"]) == 10
    next_page = client.get(url, headers=ADMIN, params={"offset": 10}).json()
    assert len(next_page["rows"]) == 3
    huge = 9223372036854775807
    kit.assign(TENANT, huge, "regular")
    assert client.get(url, headers=ADMIN).json()["rows"][0]["subject_key"] == str(huge)
