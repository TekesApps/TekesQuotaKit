from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError

from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaError, QuotaKit, utc_now
from tekes_quota_kit.models import Assignment, Base, DefaultInitialization, LevelPackageCharge

TENANT = "default-plan-tenant"
ADMIN_KEY = "default-plan-admin-key-for-tests-000001"
SECRET = "default-plan-token-secret-for-tests-0001"
CATALOG_KEY = "default-catalog-key-for-tests-000001"
INIT_KEY = "default-init-key-for-tests-000000001"
ADMIN = {"Authorization": f"Bearer {ADMIN_KEY}"}
CATALOG = {"Authorization": f"Bearer {CATALOG_KEY}"}
INIT = {"Authorization": f"Bearer {INIT_KEY}"}


def setup(tmp_path, *, filename="defaults.db"):
    kit = QuotaKit(f"sqlite:///{tmp_path / filename}", SECRET)
    Base.metadata.create_all(kit.engine)
    kit.put_client("catalog", CATALOG_KEY, TENANT, "*", "default_catalog")
    kit.put_client("initializer", INIT_KEY, TENANT, "*", "default_init")
    kit.put_client("consumer", "ordinary-consumer-key-for-tests-00001", TENANT, "visit", "consumer")
    kit.put_client(
        "membership", "ordinary-membership-key-for-tests-0001", TENANT, "*", "membership"
    )
    kit.put_quota(TENANT, "visits", "use", "per_use")
    kit.put_service(TENANT, "visit", "visits")
    kit.put_level(TENANT, "paid", publication_status="formal")
    kit.put_limit(TENANT, "paid", "visits", "finite", 1, "month", "UTC")
    for level, count in (("gift-a", 3), ("gift-b", 4)):
        kit.put_level(TENANT, level, publication_status="formal")
        kit.put_limit(TENANT, level, "visits", "finite", count, "month", "UTC")
    app = TestClient(create_app(kit, ADMIN_KEY))
    return kit, app


def source(binding_id="binding-1", estate_id="estate-1"):
    return {
        "system": "skzy",
        "event": "first_estate_binding",
        "estate_id": estate_id,
        "binding_id": binding_id,
    }


def packages(*levels):
    return [
        {"code": f"pkg-{i}", "name": f"Package {i}", "level_code": level, "validity_days": 30}
        for i, level in enumerate(levels, start=1)
    ]


def make_plan(kit, levels=("gift-a", "gift-b"), *, enabled=True):
    return kit.create_default_plan(TENANT, "Resident default", enabled, packages(*levels))


def test_initialization_appends_all_packages_and_preserves_paid_package(tmp_path):
    kit, _ = setup(tmp_path)
    kit.assign(TENANT, "u_AbC", "paid")
    paid = kit.user_packages(TENANT, "u_AbC")["packages"][0]["id"]
    plan = make_plan(kit)
    result = kit.initialize_defaults(TENANT, "u_AbC", plan["id"], source())

    assert result["subject_id"] == "u_AbC"
    assert result["replayed"] is False
    fact = result["initialization"]
    assert fact["default_plan_id"] == plan["id"]
    assert fact["plan_revision"] == plan["revision"] == 1
    assert fact["status"] == "applied"
    assert datetime.fromisoformat(fact["applied_at"].replace("Z", "+00:00")).tzinfo == UTC
    assert len(fact["package_ids"]) == 2 and all(isinstance(i, str) for i in fact["package_ids"])
    rows = kit.user_packages(TENANT, "u_AbC")["packages"]
    assert [p["id"] for p in rows] == [paid, *map(int, fact["package_ids"])]
    assert [p["level_code"] for p in rows] == ["paid", "gift-a", "gift-b"]


def test_retry_other_plan_disabled_or_missing_replays_first_fact(tmp_path):
    kit, _ = setup(tmp_path)
    first = make_plan(kit, ("gift-a",))
    other = make_plan(kit, ("gift-b",))
    applied = kit.initialize_defaults(TENANT, "42", first["id"], source())
    kit.update_default_plan(
        TENANT, other["id"], other["name"], False, other["packages"], other["revision"]
    )

    for plan_id in (other["id"], "does-not-exist"):
        replay = kit.initialize_defaults(TENANT, "42", plan_id, source("different", "other-estate"))
        assert replay["replayed"] is True
        assert replay["initialization"] == applied["initialization"]
    assert len(kit.user_packages(TENANT, "42")["packages"]) == 1


def test_disabled_first_attempt_does_not_consume_claim_and_uses_current_revision(tmp_path):
    kit, _ = setup(tmp_path)
    plan = make_plan(kit, ("gift-a",), enabled=False)
    with pytest.raises(QuotaError) as error:
        kit.initialize_defaults(TENANT, "subject", plan["id"], source())
    assert error.value.code == "default_plan_disabled"

    updated = kit.update_default_plan(
        TENANT, plan["id"], plan["name"], True, packages("gift-b"), plan["revision"]
    )
    applied = kit.initialize_defaults(TENANT, "subject", plan["id"], source())
    assert applied["initialization"]["plan_revision"] == updated["revision"] == 2
    assert [p["level_code"] for p in kit.user_packages(TENANT, "subject")["packages"]] == ["gift-b"]


def test_editing_plan_and_level_limit_does_not_change_issued_quota(tmp_path):
    kit, client = setup(tmp_path)
    kit.assign(TENANT, "42", "paid")
    plan = make_plan(kit, ("gift-a",))
    applied = kit.initialize_defaults(TENANT, "42", plan["id"], source())
    kit.update_default_plan(
        TENANT, plan["id"], "Renamed", False, plan["packages"], plan["revision"]
    )
    kit.put_limit(TENANT, "gift-a", "visits", "finite", 0, "month", "UTC")
    key = "default-snapshot-provider-key-for-tests-0001"
    kit.put_client("provider", key, TENANT, "visit", "provider")
    response = client.post(
        "/v1/redeem",
        headers={"Authorization": f"Bearer {key}"},
        json={"subject_id": "42", "service_code": "visit", "request_key": "after-edit"},
    )
    assert response.status_code == 200, response.text
    redeemed = [response.json()]
    for index in range(1, 4):
        response = client.post(
            "/v1/redeem",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "subject_id": "42",
                "service_code": "visit",
                "request_key": f"after-edit-{index}",
            },
        )
        assert response.status_code == 200, response.text
        redeemed.append(response.json())
    exhausted = client.post(
        "/v1/redeem",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "subject_id": "42",
            "service_code": "visit",
            "request_key": "after-edit-exhausted",
        },
    )
    assert exhausted.status_code == 409 and exhausted.json()["code"] == "quota_exhausted"
    gift_assignment_id = int(applied["initialization"]["package_ids"][0])
    with kit.sessions() as db:
        gift_charges = list(
            db.scalars(
                select(LevelPackageCharge).where(
                    LevelPackageCharge.assignment_id == gift_assignment_id
                )
            )
        )
        assert sum(charge.units for charge in gift_charges) == 3
    assert kit.balance(kit.client(key, "provider"), "42")["remaining"] == 0
    refund = client.post(
        "/v1/token/refund",
        headers={"Authorization": f"Bearer {key}", "X-Subject-ID": "42"},
        json={"token": redeemed[-1]["token"]},
    )
    assert refund.status_code == 200, refund.text
    assert kit.balance(kit.client(key, "provider"), "42")["remaining"] == 1
    assert (
        kit.initialize_defaults(TENANT, "42", plan["id"], source())["initialization"]
        == applied["initialization"]
    )


def test_failed_package_insert_rolls_back_assignments_and_initialization(tmp_path):
    kit, _ = setup(tmp_path)
    plan = make_plan(kit)

    def fail_on_second_package(_mapper, _connection, target):
        if target.level_code == "gift-b":
            raise RuntimeError("injected second package failure")

    event.listen(Assignment, "before_insert", fail_on_second_package)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            kit.initialize_defaults(TENANT, "42", plan["id"], source())
    finally:
        event.remove(Assignment, "before_insert", fail_on_second_package)

    with kit.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Assignment)) == 0
        assert db.scalar(select(func.count()).select_from(DefaultInitialization)) == 0
    retried = kit.initialize_defaults(TENANT, "42", plan["id"], source())
    assert retried["replayed"] is False
    assert len(retried["initialization"]["package_ids"]) == 2


def test_concurrent_different_plan_race_and_restart_replay_one_fact(tmp_path):
    kit, _ = setup(tmp_path)
    a = make_plan(kit, ("gift-a",))
    b = make_plan(kit, ("gift-b",))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda pair: kit.initialize_defaults(
                    TENANT, "RaceSubject", pair[0], source(pair[1])
                ),
                ((a["id"], "a"), (b["id"], "b")),
            )
        )
    assert sum(not result["replayed"] for result in results) == 1
    assert results[0]["initialization"] == results[1]["initialization"]

    reopened, _ = setup(tmp_path, filename="defaults.db")
    retry = reopened.initialize_defaults(
        TENANT, "RaceSubject", "nonexistent", source("after-restart")
    )
    assert retry["replayed"] is True
    assert retry["initialization"] == results[0]["initialization"]


def test_source_and_subject_identity_are_tenant_scoped_and_retained(tmp_path):
    kit, _ = setup(tmp_path)
    plan = make_plan(kit, ("gift-a",))
    original = kit.initialize_defaults(TENANT, "AbC_42", plan["id"], source())
    assert (
        kit.initialize_defaults(TENANT, "AbC_42", "other", source("new", "new"))["initialization"]
        == original["initialization"]
    )
    lower = kit.initialize_defaults(TENANT, "abc_42", plan["id"], source("lower"))
    leading_zero = kit.initialize_defaults(TENANT, "042", plan["id"], source("leading-zero"))
    kit.update_default_plan(
        TENANT, plan["id"], plan["name"], False, plan["packages"], plan["revision"]
    )
    kit.put_quota("other-tenant", "visits", "use", "per_use")
    kit.put_level("other-tenant", "gift-a", publication_status="formal")
    kit.put_limit("other-tenant", "gift-a", "visits", "finite", 3, "month", "UTC")
    kit.put_client(
        "other-init", "other-init-key-for-tests-000000001", "other-tenant", "*", "default_init"
    )
    other = kit.create_default_plan("other-tenant", "Other", True, packages("gift-a"))
    other_tenant = kit.initialize_defaults("other-tenant", "AbC_42", other["id"], source("tenant"))
    assert lower["initialization"]["id"] != original["initialization"]["id"]
    assert leading_zero["initialization"]["id"] != original["initialization"]["id"]
    assert other_tenant["initialization"]["id"] != original["initialization"]["id"]
    with kit.sessions() as db:
        fact = db.scalar(
            select(DefaultInitialization).where(
                DefaultInitialization.tenant_id == TENANT,
                DefaultInitialization.subject_id == "AbC_42",
            )
        )
        assert fact.source == source()


def test_expired_or_revoked_packages_do_not_remove_first_initialization_fact(tmp_path):
    kit, _ = setup(tmp_path)
    plan = make_plan(kit, ("gift-a",))
    original = kit.initialize_defaults(TENANT, "42", plan["id"], source())
    package_id = int(original["initialization"]["package_ids"][0])
    with kit.sessions.begin() as db:
        db.execute(
            update(Assignment)
            .where(Assignment.id == package_id)
            .values(expires_at=utc_now() - timedelta(seconds=1))
        )
    replay = kit.initialize_defaults(TENANT, "42", "missing", source("replacement"))
    assert replay["replayed"] is True
    assert replay["initialization"] == original["initialization"]
    assert kit.user_packages(TENANT, "42")["packages"][0]["id"] == package_id
    with kit.sessions.begin() as db:
        db.execute(
            update(Assignment).where(Assignment.id == package_id).values(revoked_at=utc_now())
        )
    revoked_replay = kit.initialize_defaults(TENANT, "42", "missing", source("revoked"))
    assert revoked_replay["replayed"] is True
    assert revoked_replay["initialization"] == original["initialization"]


def test_catalog_contract_paginates_disabled_plans_and_is_role_isolated(tmp_path):
    kit, client = setup(tmp_path)
    first = make_plan(kit, ("gift-a",))
    second = make_plan(kit, ("gift-b",), enabled=False)
    other_key = "other-catalog-client-key-for-tests-00001"
    kit.put_client("other-catalog", other_key, "other-tenant", "*", "default_catalog")
    kit.put_quota("other-tenant", "visits", "use", "per_use")
    kit.put_level("other-tenant", "gift-a", publication_status="formal")
    kit.put_limit("other-tenant", "gift-a", "visits", "finite", 2, "month", "UTC")
    kit.create_default_plan("other-tenant", "Other", True, packages("gift-a"))

    response = client.get("/v1/default-plans?limit=1&offset=0", headers=CATALOG)
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2 and response.json()["limit"] == 1
    assert response.json()["offset"] == 0 and len(response.json()["items"]) == 1
    assert response.json()["items"][0]["id"] == first["id"]
    assert response.json()["items"][0]["packages"] == [{"code": "pkg-1", "name": "Package 1"}]
    next_page = client.get("/v1/default-plans?limit=1&offset=1", headers=CATALOG).json()
    assert next_page["items"][0]["id"] == second["id"] and not next_page["items"][0]["enabled"]
    assert (
        client.get("/v1/default-plans", headers={"Authorization": f"Bearer {INIT_KEY}"}).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/subjects/1/default-initialization",
            headers=CATALOG,
            json={"default_plan_id": first["id"], "source": source()},
        ).status_code
        == 401
    )
    for role_key in (
        "ordinary-consumer-key-for-tests-00001",
        "ordinary-membership-key-for-tests-0001",
    ):
        assert (
            client.get(
                "/v1/default-plans", headers={"Authorization": f"Bearer {role_key}"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/v1/subjects/unauthorized/default-initialization",
                headers={"Authorization": f"Bearer {role_key}"},
                json={"default_plan_id": first["id"], "source": source()},
            ).status_code
            == 401
        )
    assert (
        client.get("/v1/default-plans", headers={"Authorization": f"Bearer {other_key}"}).json()[
            "total"
        ]
        == 1
    )
    assert client.get("/v1/default-plans?limit=101", headers=CATALOG).status_code == 422
    assert client.get("/v1/default-plans?limit=1&offset=-1", headers=CATALOG).status_code == 422


def test_initialization_http_replay_errors_and_strict_request_shape(tmp_path):
    kit, client = setup(tmp_path)
    plan = make_plan(kit, ("gift-a",))
    path = "/v1/subjects/user-1/default-initialization"
    body = {"default_plan_id": plan["id"], "source": source()}
    assert client.post(path, json=body, headers=INIT).status_code == 200
    first = client.post(path, json=body, headers=INIT).json()
    assert first["replayed"] is True
    assert client.post(path, json={**body, "tenant_id": "other"}, headers=INIT).status_code == 422
    assert (
        client.post(
            path,
            json={"default_plan_id": plan["id"], "source": {**source(), "tenant_id": "other"}},
            headers=INIT,
        ).status_code
        == 422
    )

    unknown = client.post(
        path.replace("user-1", "user-2"),
        headers=INIT,
        json={"default_plan_id": "missing", "source": source()},
    )
    assert unknown.status_code == 404 and unknown.json()["code"] == "default_plan_not_found"
    kit.update_default_plan(
        TENANT, plan["id"], plan["name"], False, plan["packages"], plan["revision"]
    )
    blocked = client.post(path.replace("user-1", "user-3"), headers=INIT, json=body)
    assert blocked.status_code == 409 and blocked.json()["code"] == "default_plan_disabled"
    replay = client.post(path, headers=INIT, json={**body, "default_plan_id": "missing"})
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert (
        kit.initialize_defaults(TENANT, "user-1", plan["id"], source())["initialization"]
        == first["initialization"]
    )


def test_admin_plan_writes_require_admin_and_revision_conflicts_are_explicit(tmp_path):
    kit, client = setup(tmp_path)
    endpoint = f"/v1/admin/tenants/{TENANT}/default-plans"
    body = {"name": "Managed", "enabled": True, "packages": packages("gift-a")}
    assert client.post(endpoint, json=body).status_code == 401
    created = client.post(endpoint, json=body, headers=ADMIN)
    assert created.status_code == 200, created.text
    plan = created.json()
    changed = {**body, "name": "Updated", "revision": plan["revision"]}
    updated = client.put(f"{endpoint}/{plan['id']}", json=changed, headers=ADMIN)
    assert updated.status_code == 200, updated.text
    assert updated.json()["id"] == plan["id"] and updated.json()["revision"] == plan["revision"] + 1
    stale = client.put(f"{endpoint}/{plan['id']}", json=changed, headers=ADMIN)
    assert stale.status_code == 409 and stale.json()["code"] == "default_plan_changed"


def test_catalog_and_initialization_database_errors_have_distinct_503_codes(tmp_path, monkeypatch):
    kit, client = setup(tmp_path)

    def unavailable(*_args, **_kwargs):
        raise OperationalError("SELECT", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(kit, "list_default_plans", unavailable)
    catalog = client.get("/v1/default-plans", headers=CATALOG)
    assert catalog.status_code == 503
    assert catalog.json()["code"] == "default_catalog_unavailable"

    monkeypatch.setattr(kit, "initialize_defaults", unavailable)
    initialize = client.post(
        "/v1/subjects/subject/default-initialization",
        headers=INIT,
        json={"default_plan_id": "plan", "source": source()},
    )
    assert initialize.status_code == 503
    assert initialize.json()["code"] == "default_initialization_unavailable"

    monkeypatch.setattr(kit, "client", unavailable)
    credential_lookup = client.get("/v1/default-plans", headers=CATALOG)
    assert credential_lookup.status_code == 503
    assert credential_lookup.json()["code"] == "default_catalog_unavailable"
    init_credential_lookup = client.post(
        "/v1/subjects/subject/default-initialization",
        headers=INIT,
        json={"default_plan_id": "plan", "source": source()},
    )
    assert init_credential_lookup.status_code == 503
    assert init_credential_lookup.json()["code"] == "default_initialization_unavailable"
