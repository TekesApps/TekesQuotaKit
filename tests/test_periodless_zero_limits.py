"""Permanent Levels can carry zero, level-term limits for unavailable services."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from tekes_quota_kit.core import QuotaError, QuotaKit
from tekes_quota_kit.models import (
    Assignment,
    Base,
    Ledger,
    LevelPackageAdmission,
    LevelPackageCharge,
    LevelPackageUsage,
    PackageGrant,
    Service,
    Token,
    Usage,
)

DATABASE_URL = os.getenv("TEKES_QUOTA_TEST_DATABASE_URL")
NOW = datetime(2026, 10, 31, 0, 0, 0)
SECRET = "periodless-zero-tests-token-secret-00001"


@pytest.fixture(params=("sqlite", "mysql"))
def world(request, tmp_path, monkeypatch):
    kind = request.param
    if kind == "mysql":
        if not DATABASE_URL:
            pytest.skip("No disposable MySQL URL configured")
        assert make_url(DATABASE_URL).database.startswith("tekes_quota_test_")
        kit = QuotaKit(DATABASE_URL, SECRET)
    else:
        kit = QuotaKit(f"sqlite:///{tmp_path / 'periodless-zero.db'}", SECRET)
    Base.metadata.create_all(kit.engine)
    monkeypatch.setattr("tekes_quota_kit.core.utc_now", lambda: NOW)

    suffix = uuid.uuid4().hex[:12]
    tenant = f"periodless-zero-{suffix}"
    subject = f"subject-{suffix}"
    for quota, mode in (
        ("calendar", "per_use"),
        ("blocked", "per_use"),
        ("measurements", "reported_usage"),
        ("topup", "per_use"),
    ):
        kit.put_quota(tenant, quota, "use", mode)
    kit.put_service(tenant, "calendar-service", "calendar")
    kit.put_service(tenant, "blocked-service", "blocked")
    kit.put_service(tenant, "metered-service", "measurements")
    kit.put_service(
        tenant,
        "blocked-session",
        "blocked",
        kind="composite",
        redemption_mode="durable",
        charge_units=1,
        session_ttl_seconds=300,
    )
    kit.put_service(tenant, "blocked-child", None)
    kit.put_service_member(tenant, "blocked-session", "blocked-child")
    kit.put_service(tenant, "topup-service", "topup")

    kit.put_level(tenant, "paid-base", publication_status="formal")
    kit.put_limit(tenant, "paid-base", "calendar", "finite", 4, "month", "UTC")
    kit.put_limit(tenant, "paid-base", "blocked", "finite", 0, "level_term", "UTC")
    kit.put_limit(tenant, "paid-base", "measurements", "finite", 0, "level_term", "UTC")
    kit.put_limit(tenant, "paid-base", "topup", "finite", 0, "month", "UTC")
    kit.put_level(tenant, "vip", publication_status="formal")
    kit.put_limit(tenant, "vip", "blocked", "finite", 2, "level_term", "UTC")
    kit.put_limit(tenant, "vip", "measurements", "finite", 5, "level_term", "UTC")
    kit.assign(tenant, subject, "paid-base")

    def caller(service_code: str, role: str):
        client_id = f"{role}-{uuid.uuid4().hex[:8]}"
        key = f"periodless-zero-{role}-{uuid.uuid4().hex}-integration-key"
        kit.put_client(client_id, key, tenant, service_code, role)
        return kit.client(key, role)

    return kit, tenant, subject, caller, kind


def vip_plan(kit, tenant):
    return kit.create_default_plan(
        tenant,
        "Seven-day VIP",
        True,
        [{"code": "vip-seven", "name": "VIP", "level_code": "vip", "validity_days": 7}],
    )


def install_topup(kit, tenant, subject, *, service="topup-service", code="real-period-topup"):
    kit.put_package(tenant, code, "One top-up", service, 1)
    kit.put_package_grant(tenant, subject, code, code)


def count_rows(kit, model, tenant):
    with kit.sessions() as db:
        query = select(func.count()).select_from(model)
        if hasattr(model, "tenant_id"):
            query = query.where(model.tenant_id == tenant)
        else:
            query = query.join(Token, model.token_id == Token.id).where(Token.tenant_id == tenant)
        return db.scalar(query)


def service_id(kit, tenant, code):
    with kit.sessions() as db:
        return db.scalar(
            select(Service.id).where(Service.tenant_id == tenant, Service.service_code == code)
        )


def test_vip_appends_after_paid_identity_without_changing_paid_calendar_counter(world):
    kit, tenant, subject, caller, _ = world
    paid_id = kit.user_packages(tenant, subject)["packages"][0]["id"]
    calendar = caller("calendar-service", "provider")
    kit.redeem(calendar, subject, "calendar-service", "paid-calendar-before-vip")
    before = kit.balance(calendar, subject)
    assert before["remaining"] == 3

    initialized = kit.initialize_defaults(
        tenant,
        subject,
        vip_plan(kit, tenant)["id"],
        {
            "system": "skzy",
            "event": "first_estate_binding",
            "estate_id": "estate-test",
            "binding_id": "binding-test",
        },
    )
    package_id = int(initialized["initialization"]["package_ids"][0])
    rows = kit.user_packages(tenant, subject)["packages"]
    assert [row["id"] for row in rows] == [paid_id, package_id]
    assert [row["level_code"] for row in rows] == ["paid-base", "vip"]
    after = kit.balance(calendar, subject)
    assert after["remaining"] == 3
    with kit.sessions() as db:
        paid = db.get(Assignment, paid_id)
        assert paid.package_code == "membership" and paid.revoked_at is None
        assert (
            db.scalar(
                select(Usage.used_units).where(
                    Usage.tenant_id == tenant,
                    Usage.subject_id == subject,
                    Usage.quota_code == "calendar",
                )
            )
            == 1
        )


def test_new_subject_gets_ordered_vip_and_permanent_base_packages(world, monkeypatch):
    kit, tenant, _existing_subject, caller, _ = world
    subject = f"new-subject-{uuid.uuid4().hex[:12]}"
    plan = kit.create_default_plan(
        tenant,
        "VIP plus permanent base",
        True,
        [
            {
                "code": "vip-seven",
                "name": "Seven-day VIP",
                "level_code": "vip",
                "validity_days": 7,
            },
            {
                "code": "permanent-base",
                "name": "Permanent base",
                "level_code": "paid-base",
                "validity_days": None,
            },
        ],
    )
    result = kit.initialize_defaults(
        tenant,
        subject,
        plan["id"],
        {
            "system": "skzy",
            "event": "first_estate_binding",
            "estate_id": "estate-new-subject",
            "binding_id": "binding-new-subject",
        },
    )
    package_ids = [int(package_id) for package_id in result["initialization"]["package_ids"]]
    assert len(package_ids) == 2
    with kit.sessions() as db:
        assignments = [db.get(Assignment, package_id) for package_id in package_ids]
        assert [row.level_code for row in assignments] == ["vip", "paid-base"]
        assert [row.sort_order for row in assignments] == [0, 1]
        assert [row.expires_at for row in assignments] == [NOW + timedelta(days=7), None]
        assert [row.term_end for row in assignments] == [NOW + timedelta(days=7), None]

    calendar = caller("calendar-service", "provider")
    blocked = caller("blocked-service", "provider")
    assert kit.balance(calendar, subject)["remaining"] == 4
    assert kit.redeem(blocked, subject, "blocked-service", "new-vip-use")["status"] == "settled"
    assert kit.balance(blocked, subject)["remaining"] == 1
    assert [row["level_code"] for row in kit.user_packages(tenant, subject)["packages"]] == [
        "vip",
        "paid-base",
    ]

    monkeypatch.setattr("tekes_quota_kit.core.utc_now", lambda: NOW + timedelta(days=8))
    assert kit.balance(calendar, subject)["remaining"] == 4
    blocked_balance = kit.balance(blocked, subject)
    assert blocked_balance["remaining"] == 0
    assert blocked_balance["level_packages"] == [
        {
            "id": package_ids[1],
            "level_code": "paid-base",
            "sort_order": 1,
            "limit": 0,
            "used": 0,
            "remaining": 0,
            "period_start": None,
            "period_end": None,
        }
    ]
    with pytest.raises(QuotaError) as error:
        kit.redeem(blocked, subject, "blocked-service", "expired-vip-denied")
    assert error.value.code == "quota_exhausted"
    assert [row["level_code"] for row in kit.user_packages(tenant, subject)["packages"]] == [
        "vip",
        "paid-base",
    ]


def test_permanent_zero_term_sources_expire_without_hiding_calendar_or_allowing_mutations(
    world, monkeypatch
):
    kit, tenant, subject, caller, _ = world
    paid_id = kit.user_packages(tenant, subject)["packages"][0]["id"]
    calendar = caller("calendar-service", "provider")
    blocked = caller("blocked-service", "provider")
    blocked_session = caller("blocked-session", "provider")
    measured_issuer = caller("metered-service", "issuer")
    plan = vip_plan(kit, tenant)
    initial = kit.initialize_defaults(
        tenant,
        subject,
        plan["id"],
        {
            "system": "skzy",
            "event": "first_estate_binding",
            "estate_id": "estate-test",
            "binding_id": "binding-test",
        },
    )
    vip_id = int(initial["initialization"]["package_ids"][0])
    vip_end = NOW + timedelta(days=7)

    # The zero periodless base source remains visible as zero, while the active VIP
    # supplies its own real term and can be charged normally.
    assert kit.balance(blocked, subject)["remaining"] == 2
    paid_use = kit.redeem(blocked, subject, "blocked-service", "vip-use-before-expiry")
    metered = kit.issue(
        measured_issuer,
        subject,
        service_id(kit, tenant, "metered-service"),
        "vip-measure-before-expiry",
    )
    assert paid_use["status"] == "settled"
    assert metered["status"] == "admitted"
    with kit.sessions() as db:
        admissions = list(
            db.scalars(
                select(LevelPackageAdmission).where(
                    LevelPackageAdmission.token_id
                    == select(Token.id)
                    .where(Token.token_hash == kit.key_hash(metered["token"]))
                    .scalar_subquery()
                )
            )
        )
        assert [(row.assignment_id, row.limit_value, row.period_end) for row in admissions] == [
            (vip_id, 5, vip_end)
        ]

    monkeypatch.setattr("tekes_quota_kit.core.utc_now", lambda: vip_end + timedelta(seconds=1))
    zero_balance = kit.balance(blocked, subject)
    assert zero_balance["remaining"] == 0
    assert zero_balance["level_packages"] == [
        {
            "id": paid_id,
            "level_code": "paid-base",
            "sort_order": 0,
            "limit": 0,
            "used": 0,
            "remaining": 0,
            "period_start": None,
            "period_end": None,
        }
    ]
    subject_usage = kit.subject_usage(tenant, subject)
    blocked_usage = next(row for row in subject_usage["quotas"] if row["quota_code"] == "blocked")
    assert blocked_usage["limit"] == 0 and blocked_usage["remaining"] == 0
    assert blocked_usage.get("error") is None
    base_package = next(
        row for row in kit.user_packages(tenant, subject)["packages"] if row["id"] == paid_id
    )
    blocked_package_usage = next(
        row for row in base_package["quotas"] if row["quota_code"] == "blocked"
    )
    assert blocked_package_usage["remaining"] == 0
    assert blocked_package_usage["period_start"] is None
    assert blocked_package_usage["period_end"] is None
    assert blocked_package_usage.get("error") is None

    counts_before = {
        model: count_rows(kit, model, tenant)
        for model in (Token, Usage, LevelPackageAdmission, Ledger)
    }
    denied = (
        lambda: kit.redeem(blocked, subject, "blocked-service", "blocked-after-expiry"),
        lambda: kit.issue(
            measured_issuer,
            subject,
            service_id(kit, tenant, "metered-service"),
            "measure-after-expiry",
        ),
        lambda: kit.begin(blocked_session, subject, "blocked-session", "begin-after-expiry"),
    )
    for operation in denied:
        with pytest.raises(QuotaError) as error:
            operation()
        assert error.value.code == "quota_exhausted"
    assert {
        model: count_rows(kit, model, tenant)
        for model in (Token, Usage, LevelPackageAdmission, Ledger)
    } == counts_before

    # The old paid calendar entitlement is still a real monthly source.
    assert (
        kit.redeem(calendar, subject, "calendar-service", "calendar-after-expiry")["status"]
        == "settled"
    )
    assert kit.balance(calendar, subject)["remaining"] == 3


def test_metered_vip_settlement_after_expiry_and_per_use_refund_keep_original_source(
    world, monkeypatch
):
    kit, tenant, subject, caller, _ = world
    plan = vip_plan(kit, tenant)
    applied = kit.initialize_defaults(
        tenant,
        subject,
        plan["id"],
        {
            "system": "skzy",
            "event": "first_estate_binding",
            "estate_id": "estate-test",
            "binding_id": "binding-test",
        },
    )
    vip_id = int(applied["initialization"]["package_ids"][0])
    blocked = caller("blocked-service", "provider")
    measured_issuer = caller("metered-service", "issuer")
    measured_provider = caller("metered-service", "provider")
    token = kit.issue(
        measured_issuer,
        subject,
        service_id(kit, tenant, "metered-service"),
        "metered-vip-admitted",
    )["token"]
    per_use = kit.redeem(blocked, subject, "blocked-service", "vip-refund-after-expiry")["token"]
    end = NOW + timedelta(days=7)
    monkeypatch.setattr("tekes_quota_kit.core.utc_now", lambda: end + timedelta(seconds=1))

    settled = kit.settle(token, measured_provider, 7)
    assert settled["status"] == "settled" and settled["consumed_units"] == 7
    with kit.sessions() as db:
        charges = list(
            db.scalars(
                select(LevelPackageCharge).where(
                    LevelPackageCharge.token_id
                    == select(Token.id)
                    .where(Token.token_hash == kit.key_hash(token))
                    .scalar_subquery()
                )
            )
        )
        assert charges
        assert {charge.assignment_id for charge in charges} == {vip_id}
        assert sum(charge.units for charge in charges) == 7
        counter = db.scalar(
            select(LevelPackageUsage).where(
                LevelPackageUsage.assignment_id == vip_id,
                LevelPackageUsage.quota_code == "measurements",
                LevelPackageUsage.period_start == NOW,
            )
        )
        assert counter.used_units == 7

    refunded = kit.refund(per_use, blocked, subject)
    assert refunded["status"] == "refunded"
    with kit.sessions() as db:
        charge = db.scalar(
            select(LevelPackageCharge).where(
                LevelPackageCharge.token_id
                == select(Token.id)
                .where(Token.token_hash == kit.key_hash(per_use))
                .scalar_subquery()
            )
        )
        assert charge.assignment_id == vip_id and charge.units == 1
        use_counter = db.scalar(
            select(LevelPackageUsage).where(
                LevelPackageUsage.assignment_id == vip_id,
                LevelPackageUsage.quota_code == "blocked",
                LevelPackageUsage.period_start == NOW,
            )
        )
        assert use_counter.used_units == 0


def test_positive_and_unlimited_term_limits_still_require_a_package_term(world):
    kit, tenant, _subject, _caller, _kind = world
    for level, mode, value in (
        ("positive-term", "finite", 2),
        ("unlimited-term", "unlimited", None),
    ):
        kit.put_level(tenant, level, publication_status="formal")
        kit.put_limit(tenant, level, "blocked", mode, value, "level_term", "UTC")
        with pytest.raises(QuotaError) as error:
            kit.create_default_plan(
                tenant,
                level,
                True,
                [{"code": level, "name": level, "level_code": level, "validity_days": None}],
            )
        assert error.value.code == "missing_term_end"


def test_real_period_zero_limit_still_allows_and_refunds_legacy_topup(world):
    kit, tenant, subject, caller, _kind = world
    install_topup(kit, tenant, subject)
    provider = caller("topup-service", "provider")
    before = kit.balance(provider, subject)
    assert before["main_remaining"] == 0
    assert before["auxiliary_remaining"] == 1
    token = kit.redeem(provider, subject, "topup-service", "real-period-topup")["token"]
    assert kit.balance(provider, subject)["remaining"] == 0
    assert kit.refund(token, provider, subject)["status"] == "refunded"
    assert kit.balance(provider, subject)["remaining"] == 1
    with kit.sessions() as db:
        grant = db.scalar(
            select(PackageGrant).where(
                PackageGrant.tenant_id == tenant,
                PackageGrant.subject_id == subject,
                PackageGrant.grant_code == "real-period-topup",
            )
        )
        assert grant.used_units == 0


def test_periodless_zero_limit_does_not_advertise_legacy_topup(world):
    kit, tenant, subject, caller, _kind = world
    install_topup(kit, tenant, subject, service="blocked-service", code="periodless-topup")
    provider = caller("blocked-service", "provider")
    balance = kit.balance(provider, subject)
    assert balance["main_remaining"] == 0
    assert balance["auxiliary_remaining"] == 0
    assert balance["remaining"] == 0
    with pytest.raises(QuotaError) as error:
        kit.redeem(provider, subject, "blocked-service", "periodless-topup-denied")
    assert error.value.code == "quota_exhausted"
