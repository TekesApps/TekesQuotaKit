"""Optional first-initialization package selection remains tenant scoped and replay safe."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaError, QuotaKit
from tekes_quota_kit.models import (
    Base,
    DefaultInitialization,
    DefaultInitializationPackage,
    LevelPackageCharge,
)

TENANT = "default-selection-tenant"
ADMIN_KEY = "default-selection-admin-key-for-tests-0001"
TOKEN_SECRET = "default-selection-token-secret-for-tests-0001"
INIT_KEY = "default-selection-init-key-for-tests-000001"
INIT_HEADERS = {"Authorization": f"Bearer {INIT_KEY}"}


def setup(tmp_path, filename="default-selection.db"):
    kit = QuotaKit(f"sqlite:///{tmp_path / filename}", TOKEN_SECRET)
    Base.metadata.create_all(kit.engine)
    kit.put_client("admin-init", INIT_KEY, TENANT, "*", "default_init")
    kit.put_quota(TENANT, "visits", "use", "per_use")
    kit.put_service(TENANT, "visit", "visits")
    for level, amount in (("paid", 1), ("gift-a", 2), ("gift-b", 3), ("gift-c", 5)):
        kit.put_level(TENANT, level, publication_status="formal")
        kit.put_limit(TENANT, level, "visits", "finite", amount, "month", "UTC")
    client = TestClient(create_app(kit, ADMIN_KEY))
    return kit, client


def package(code, level):
    return {"code": code, "name": f"Name {code}", "level_code": level, "validity_days": 30}


def make_plan(kit):
    return kit.create_default_plan(
        TENANT,
        "Three packages",
        True,
        [package("pkg-a", "gift-a"), package("pkg-b", "gift-b"), package("pkg-c", "gift-c")],
    )


def payload(plan_id, *, codes_marker=...):
    result = {
        "default_plan_id": plan_id,
        "source": {
            "system": "skzy",
            "event": "first_estate_binding",
            "estate_id": "estate-selection",
            "binding_id": "binding-selection",
        },
    }
    if codes_marker is not ...:
        result["package_codes"] = codes_marker
    return result


def test_omitted_package_selection_grants_every_package_in_plan_order(tmp_path):
    kit, client = setup(tmp_path)
    plan = make_plan(kit)

    response = client.post(
        "/v1/subjects/subject-all/default-initialization",
        headers=INIT_HEADERS,
        json=payload(plan["id"]),
    )

    assert response.status_code == 200, response.text
    fact = response.json()["initialization"]
    assert len(fact["package_ids"]) == 3
    with kit.sessions() as db:
        rows = list(
            db.scalars(
                select(DefaultInitializationPackage)
                .where(
                    DefaultInitializationPackage.assignment_id.in_(
                        map(int, fact["package_ids"])
                    )
                )
                .order_by(DefaultInitializationPackage.sort_order)
            )
        )
    assert [row.code for row in rows] == ["pkg-a", "pkg-b", "pkg-c"]
    assert [row.name for row in rows] == ["Name pkg-a", "Name pkg-b", "Name pkg-c"]


def test_selected_subset_uses_plan_order_and_only_selected_quota_rules(tmp_path):
    kit, client = setup(tmp_path)
    subject = "paid.Subject_1"
    kit.assign(TENANT, subject, "paid")
    plan = make_plan(kit)

    response = client.post(
        f"/v1/subjects/{subject}/default-initialization",
        headers=INIT_HEADERS,
        json=payload(plan["id"], codes_marker=["pkg-c", "pkg-a"]),
    )

    assert response.status_code == 200, response.text
    fact = response.json()["initialization"]
    assert len(fact["package_ids"]) == 2
    package_ids = list(map(int, fact["package_ids"]))
    assignments = kit.user_packages(TENANT, subject)["packages"]
    assert [row["level_code"] for row in assignments] == ["paid", "gift-a", "gift-c"]
    assert [row["id"] for row in assignments] == [assignments[0]["id"], *package_ids]

    with kit.sessions() as db:
        package_rows = list(
            db.scalars(
                select(DefaultInitializationPackage)
                .where(DefaultInitializationPackage.assignment_id.in_(package_ids))
                .order_by(DefaultInitializationPackage.sort_order)
            )
        )
        assert [row.code for row in package_rows] == ["pkg-a", "pkg-c"]
        assert [row.limits[0]["limit_value"] for row in package_rows] == [2, 5]
        assert db.scalar(
            select(func.count()).select_from(DefaultInitializationPackage).where(
                DefaultInitializationPackage.initialization_id
                == db.scalar(
                    select(DefaultInitializationPackage.initialization_id).where(
                        DefaultInitializationPackage.assignment_id == package_ids[0]
                    )
                ),
                DefaultInitializationPackage.code == "pkg-b",
            )
        ) == 0

    provider_key = "default-selection-provider-key-for-tests-0001"
    kit.put_client("provider", provider_key, TENANT, "visit", "provider")
    provider = kit.client(provider_key, "provider")
    for index in range(8):
        kit.redeem(provider, subject, "visit", f"selected-{index}")
    with pytest.raises(QuotaError) as exhausted:
        kit.redeem(provider, subject, "visit", "selected-exhausted")
    assert getattr(exhausted.value, "code", None) == "quota_exhausted"
    with kit.sessions() as db:
        charges = list(db.scalars(select(LevelPackageCharge)))
    units_by_assignment = {}
    for charge in charges:
        units_by_assignment[charge.assignment_id] = (
            units_by_assignment.get(charge.assignment_id, 0) + charge.units
        )
    assert units_by_assignment == {assignments[0]["id"]: 1, package_ids[0]: 2, package_ids[1]: 5}


def test_selection_validation_and_unknown_code_do_not_consume_first_claim(tmp_path):
    kit, client = setup(tmp_path)
    plan = make_plan(kit)
    invalid_values = [None, [], ["pkg-a", "pkg-a"], ["   "], [17], ["x" * 65], ["pkg-a"] * 101]
    for codes in invalid_values:
        response = client.post(
            "/v1/subjects/subject-validation/default-initialization",
            headers=INIT_HEADERS,
            json=payload(plan["id"], codes_marker=codes),
        )
        assert response.status_code == 422, (codes, response.text)
        assert "detail" in response.json()

    unknown = client.post(
        "/v1/subjects/subject-validation/default-initialization",
        headers=INIT_HEADERS,
        json=payload(plan["id"], codes_marker=["unknown"]),
    )
    assert unknown.status_code == 400
    assert unknown.json()["code"] == "invalid_package_selection"
    with kit.sessions() as db:
        assert db.scalar(
            select(func.count()).select_from(DefaultInitialization).where(
                DefaultInitialization.subject_id == "subject-validation"
            )
        ) == 0

    applied = client.post(
        "/v1/subjects/subject-validation/default-initialization",
        headers=INIT_HEADERS,
        json=payload(plan["id"], codes_marker=["pkg-b"]),
    )
    assert applied.status_code == 200, applied.text
    assert len(applied.json()["initialization"]["package_ids"]) == 1


def test_replay_returns_original_subset_before_selection_lookup_after_restart(tmp_path):
    kit, client = setup(tmp_path, "replay.db")
    plan = make_plan(kit)
    first = client.post(
        "/v1/subjects/replay-subject/default-initialization",
        headers=INIT_HEADERS,
        json=payload(plan["id"], codes_marker=["pkg-c", "pkg-a"]),
    )
    assert first.status_code == 200, first.text

    kit.engine.dispose()
    reopened, reopened_client = setup(tmp_path, "replay.db")
    replay = reopened_client.post(
        "/v1/subjects/replay-subject/default-initialization",
        headers=INIT_HEADERS,
        json=payload("does-not-exist", codes_marker=["no-longer-a-member"]),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["initialization"] == first.json()["initialization"]

    malformed_replay = reopened_client.post(
        "/v1/subjects/replay-subject/default-initialization",
        headers=INIT_HEADERS,
        json=payload("does-not-exist", codes_marker=[]),
    )
    assert malformed_replay.status_code == 422


def test_concurrent_different_selections_grant_one_original_selection(tmp_path):
    kit, _ = setup(tmp_path, "concurrent.db")
    plan = make_plan(kit)

    def initialize(codes):
        return kit.initialize_defaults(
            TENANT,
            "concurrent-subject",
            plan["id"],
            payload(plan["id"])["source"],
            package_codes=codes,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(initialize, (["pkg-a"], ["pkg-b"], ["pkg-c"], None)))

    assert sum(result["replayed"] is False for result in results) == 1
    assert all(result["initialization"] == results[0]["initialization"] for result in results)
    assert len(results[0]["initialization"]["package_ids"]) in {1, 3}
