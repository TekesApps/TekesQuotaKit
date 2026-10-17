from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from test_membership_sync import TENANT, provision, setup

from tekes_quota_kit.core import QuotaError, utc_now
from tekes_quota_kit.models import Assignment, MembershipPurchase


def fixture(tmp_path):
    kit, client = setup(tmp_path)
    for level, value in [("星会员", 4), ("银会员", 8), ("金会员", 16)]:
        kit.put_level(TENANT, level)
        kit.put_limit(TENANT, level, "door_open_count", "finite", value, "week", "Asia/Shanghai")
    kit.assign(TENANT, "42", "星会员")
    return kit, client


def test_purchase_retries_are_exactly_once_and_do_not_stack_main_quotas(tmp_path):
    kit, _ = fixture(tmp_path)
    first = kit.membership_purchase(TENANT, "42", "order1", "银会员", "星会员", 365)
    assert kit.membership_purchase(TENANT, "42", "order1", "银会员", "星会员", 365) == first
    assert kit.membership(TENANT, "42")["level_code"] == "银会员"
    quotas = kit.subject_usage(TENANT, "42")["quotas"]
    assert quotas[0]["limit"] == 8
    with kit.sessions() as db:
        assert db.scalar(select(func.count()).select_from(MembershipPurchase)) == 1
        assert db.scalar(select(func.count()).select_from(Assignment)) == 2


def test_parallel_notifications_share_one_receipt(tmp_path):
    kit, _ = fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda _: kit.membership_purchase(
                    TENANT, "42", "parallel", "银会员", "星会员", 365
                ),
                range(2),
            )
        )
    assert receipts[0] == receipts[1]


def test_identity_conflict_and_changed_membership_do_not_downgrade(tmp_path):
    kit, _ = fixture(tmp_path)
    kit.membership_purchase(TENANT, "42", "order1", "银会员", "星会员", 365)
    with pytest.raises(QuotaError, match="differ"):
        kit.membership_purchase(TENANT, "43", "order1", "银会员", "星会员", 365)
    with pytest.raises(QuotaError, match="changed"):
        kit.membership_purchase(TENANT, "42", "order2", "金会员", "星会员", 365)
    assert kit.membership(TENANT, "42")["level_code"] == "银会员"


def test_refund_restores_previous_level_without_touching_later_upgrade(tmp_path):
    kit, _ = fixture(tmp_path)
    kit.membership_purchase(TENANT, "42", "silver", "银会员", "星会员", 365)
    kit.membership_purchase(TENANT, "42", "gold", "金会员", "银会员", 365)
    kit.membership_purchase(TENANT, "42", "silver", "银会员", "星会员", 365, revoke=True)
    assert kit.membership(TENANT, "42")["level_code"] == "金会员"
    receipt = kit.membership_purchase(TENANT, "42", "gold", "金会员", "银会员", 365, revoke=True)
    assert receipt["status"] == "revoked"
    assert kit.membership(TENANT, "42")["level_code"] == "星会员"
    assert (
        kit.membership_purchase(TENANT, "42", "gold", "金会员", "银会员", 365, revoke=True)
        == receipt
    )


def test_refund_before_delivery_blocks_late_paid_notification(tmp_path):
    kit, _ = fixture(tmp_path)
    kit.membership_purchase(TENANT, "42", "cancelled", "银会员", "星会员", 365, revoke=True)
    with pytest.raises(QuotaError, match="refunded"):
        kit.membership_purchase(TENANT, "42", "cancelled", "银会员", "星会员", 365)
    assert kit.membership(TENANT, "42")["level_code"] == "星会员"


def test_expired_purchase_falls_back_to_default_without_inflating_quota(tmp_path):
    kit, _ = fixture(tmp_path)
    receipt = kit.membership_purchase(TENANT, "42", "expired", "银会员", "星会员", 365)
    with kit.sessions.begin() as db:
        db.get(Assignment, receipt["assignment_id"]).expires_at = utc_now() - timedelta(seconds=1)
    assert kit.membership(TENANT, "42")["level_code"] == "星会员"
    assert kit.subject_usage(TENANT, "42")["quotas"][0]["limit"] == 4


def test_missing_default_is_explicit_and_refund_does_not_revoke_other_packages(tmp_path):
    kit, _ = fixture(tmp_path)
    kit.membership_purchase(TENANT, "44", "new", "银会员", "星会员", 365)
    assert kit.membership(TENANT, "44")["level_code"] == "银会员"
    kit.membership_purchase(TENANT, "44", "new", "银会员", "星会员", 365, revoke=True)
    assert kit.membership(TENANT, "44")["level_code"] == "星会员"


def test_business_api_credentials_validate_payload_and_are_tenant_scoped(tmp_path):
    kit, client = fixture(tmp_path)
    _, contract = provision(client, "paid-memberships", "membership")
    auth = {"Authorization": "Bearer " + contract["client_key"]}
    path = "/v1/members/42/purchases/http-order"
    data = {"level_code": "银会员", "expected_level": "星会员", "period_days": 365}
    assert client.put(path, json=data).status_code == 401
    first = client.put(path, headers=auth, json=data)
    assert first.status_code == 200
    assert first.headers["Cache-Control"] == "no-store"
    assert client.put(path, headers=auth, json=data).json() == first.json()
    assert client.put(path, headers=auth, json={**data, "period_days": True}).status_code == 422
    assert client.post(path + "/revoke", headers=auth, json=data).json()["status"] == "revoked"
    assert kit.membership(TENANT, "42")["level_code"] == "星会员"
