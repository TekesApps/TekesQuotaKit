from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from tekes_quota_kit.core import QuotaError, QuotaKit
from tekes_quota_kit.models import Base


@pytest.mark.skipif(
    not os.getenv("TEKES_QUOTA_TEST_DATABASE_URL"), reason="No disposable MySQL URL"
)
def test_mysql_atomic_redeem_and_reported_settlement():
    url = os.environ["TEKES_QUOTA_TEST_DATABASE_URL"]
    assert make_url(url).database.startswith("tekes_quota_test_")
    kit = QuotaKit(url, "test-token-secret-with-at-least-32-characters")
    Base.metadata.create_all(kit.engine)
    schema = inspect(kit.engine)
    for table in Base.metadata.tables:
        assert schema.get_pk_constraint(table)["constrained_columns"] == ["id"]
        assert next(column for column in schema.get_columns(table) if column["name"] == "id")[
            "autoincrement"
        ]
    kit.put_quota("first-consumer", "visits", "use", "per_use")
    visit_id = kit.put_service("first-consumer", "visit", "visits")
    kit.put_level("first-consumer", "regular")
    kit.put_limit("first-consumer", "regular", "visits", "finite", 1, "month", "Asia/Shanghai")
    kit.assign("first-consumer", 42, "regular")
    kit.put_client(
        "mysql-issuer",
        "mysql-issuer-key-with-at-least-32-characters",
        "first-consumer",
        "visit",
        "issuer",
    )
    kit.put_client(
        "mysql-provider",
        "mysql-provider-key-with-at-least-32-characters",
        "first-consumer",
        "visit",
        "provider",
    )
    issuer = kit.client("mysql-issuer-key-with-at-least-32-characters", "issuer")
    provider = kit.client("mysql-provider-key-with-at-least-32-characters", "provider")

    def redeem(request_key: str) -> str:
        try:
            kit.redeem(provider, 42, visit_id, request_key)
            return "allowed"
        except QuotaError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(redeem, ["visit-0", "visit-1"])) == [
            "allowed",
            "quota_exhausted",
        ]
    assert sorted([redeem("visit-0"), redeem("visit-1")]) == [
        "already_redeemed",
        "quota_exhausted",
    ]
    assert kit.balance(issuer, 42)["used"] == 1

    kit.put_quota("first-consumer", "model", "model_token", "reported_usage")
    chat_id = kit.put_service("first-consumer", "chat", "model")
    kit.put_limit("first-consumer", "regular", "model", "finite", 10, "month", "Asia/Shanghai")
    kit.put_client(
        "chat-issuer",
        "chat-issuer-key-with-at-least-32-characters",
        "first-consumer",
        "chat",
        "issuer",
    )
    kit.put_client(
        "chat-provider",
        "chat-provider-key-with-at-least-32-characters",
        "first-consumer",
        "chat",
        "provider",
    )
    chat_issuer = kit.client("chat-issuer-key-with-at-least-32-characters", "issuer")
    chat_provider = kit.client("chat-provider-key-with-at-least-32-characters", "provider")
    token = kit.issue(chat_issuer, 42, chat_id, "chat-one")["token"]
    retry = kit.issue(chat_issuer, 42, chat_id, "chat-one")
    assert retry["token"] == token and retry["idempotent"] is True
    assert kit.settle(token, chat_provider, 7)["consumed_units"] == 7
    assert kit.settle(token, chat_provider, 7)["idempotent"] is True
    assert kit.balance(chat_issuer, 42)["used"] == 7
