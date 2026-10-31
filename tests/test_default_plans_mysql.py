"""Default-plan transactions on a disposable real MySQL schema."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import make_url

from tekes_quota_kit.core import QuotaError, QuotaKit
from tekes_quota_kit.models import (
    Assignment,
    Base,
    DefaultInitialization,
    LevelPackageCharge,
)

DATABASE_URL = os.getenv("TEKES_QUOTA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="No disposable MySQL URL for integration tests"
)


def setup_mysql():
    url = DATABASE_URL
    assert url is not None
    assert make_url(url).database.startswith("tekes_quota_test_")
    kit = QuotaKit(url, "default-plan-mysql-secret-for-tests-000001")
    Base.metadata.create_all(kit.engine)
    suffix = uuid.uuid4().hex[:12]
    tenant = f"default-plan-{suffix}"
    kit.put_quota(tenant, "visits", "use", "per_use")
    kit.put_service(tenant, "visit", "visits")
    for level, units in (("paid", 1), ("gift-a", 3), ("gift-b", 4)):
        kit.put_level(tenant, level, publication_status="formal")
        kit.put_limit(tenant, level, "visits", "finite", units, "month", "UTC")
    return kit, tenant


def package(code, level):
    return {"code": code, "name": code.title(), "level_code": level, "validity_days": 30}


def source(event_id):
    return {
        "system": "skzy",
        "event": "first_estate_binding",
        "estate_id": "mysql-estate",
        "binding_id": event_id,
    }


def test_mysql_parallel_different_plans_and_restart_replay():
    kit, tenant = setup_mysql()
    a = kit.create_default_plan(tenant, "A", True, [package("a", "gift-a")])
    b = kit.create_default_plan(tenant, "B", True, [package("b", "gift-b")])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda plan: kit.initialize_defaults(
                    tenant, "RaceSubject", plan[0], source(plan[1])
                ),
                ((a["id"], "a"), (b["id"], "b")),
            )
        )
    assert sum(result["replayed"] is False for result in results) == 1
    assert results[0]["initialization"] == results[1]["initialization"]

    reopened = QuotaKit(DATABASE_URL, "default-plan-mysql-secret-for-tests-000001")
    replay = reopened.initialize_defaults(tenant, "RaceSubject", "missing", source("restart"))
    assert replay["replayed"] is True
    assert replay["initialization"] == results[0]["initialization"]


def test_mysql_package_failure_rolls_back_all_and_allows_fresh_attempt():
    kit, tenant = setup_mysql()
    plan = kit.create_default_plan(
        tenant, "Two packages", True, [package("a", "gift-a"), package("b", "gift-b")]
    )

    def fail_on_second(_mapper, _connection, target):
        if target.level_code == "gift-b":
            raise RuntimeError("injected MySQL package failure")

    event.listen(Assignment, "before_insert", fail_on_second)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            kit.initialize_defaults(tenant, "subject", plan["id"], source("rollback"))
    finally:
        event.remove(Assignment, "before_insert", fail_on_second)

    with kit.sessions() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Assignment).where(Assignment.tenant_id == tenant)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(DefaultInitialization)
                .where(DefaultInitialization.tenant_id == tenant)
            )
            == 0
        )
    result = kit.initialize_defaults(tenant, "subject", plan["id"], source("retry"))
    assert result["replayed"] is False and len(result["initialization"]["package_ids"]) == 2


def test_mysql_level_snapshot_consumption_preserves_paid_priority_and_refunds():
    kit, tenant = setup_mysql()
    subject = f"u_{uuid.uuid4().hex[:12]}"
    kit.assign(tenant, subject, "paid")
    plan = kit.create_default_plan(tenant, "Snapshot", True, [package("a", "gift-a")])
    applied = kit.initialize_defaults(tenant, subject, plan["id"], source("snapshot"))
    key = f"default-plan-provider-{uuid.uuid4().hex}-for-mysql"
    kit.put_client(f"provider-{uuid.uuid4().hex[:8]}", key, tenant, "visit", "provider")
    provider = kit.client(key, "provider")

    kit.update_default_plan(tenant, plan["id"], "Edited", False, [package("a", "gift-a")], 1)
    kit.put_limit(tenant, "gift-a", "visits", "finite", 0, "month", "UTC")
    tokens = [
        kit.redeem(provider, subject, "visit", f"mysql-{index}")["token"] for index in range(4)
    ]
    with pytest.raises(QuotaError) as exhausted:
        kit.redeem(provider, subject, "visit", "mysql-exhausted")
    assert exhausted.value.code == "quota_exhausted"

    gift_id = int(applied["initialization"]["package_ids"][0])
    with kit.sessions() as db:
        charges = list(
            db.scalars(
                select(LevelPackageCharge).where(LevelPackageCharge.assignment_id == gift_id)
            )
        )
        assert sum(row.units for row in charges) == 3
    assert kit.balance(provider, subject)["remaining"] == 0
    assert kit.refund(tokens[-1], provider, subject)["status"] == "refunded"
    assert kit.balance(provider, subject)["remaining"] == 1
    assert (
        kit.initialize_defaults(tenant, subject, "other", source("retry"))["initialization"]
        == applied["initialization"]
    )


def test_mysql_subject_case_and_tenant_are_distinct_initialization_keys():
    kit, tenant = setup_mysql()
    plan = kit.create_default_plan(tenant, "Identity", True, [package("a", "gift-a")])
    upper = kit.initialize_defaults(tenant, "AbC_42", plan["id"], source("upper"))
    lower = kit.initialize_defaults(tenant, "abc_42", plan["id"], source("lower"))
    zeros = kit.initialize_defaults(tenant, "042", plan["id"], source("zeros"))
    assert (
        len(
            {
                upper["initialization"]["id"],
                lower["initialization"]["id"],
                zeros["initialization"]["id"],
            }
        )
        == 3
    )
