"""Seeded Kit behind a proxy that strips /user-quota, for the console browser tests.

Usage: python admin-web/e2e/serve.py <sqlite-path> <port>
"""

import sys

import uvicorn

from tekes_quota_kit.admin_auth import AdminAccounts
from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaKit, _period, utc_now
from tekes_quota_kit.models import Base, Tenant, Usage

TENANT = "e2e-business"
kit = QuotaKit(f"sqlite:///{sys.argv[1]}", "e2e-token-secret-0000000000000000000000")
Base.metadata.create_all(kit.engine)
AdminAccounts(kit).create("e2e", "e2e-console-password")
with kit.sessions.begin() as db:
    db.add(Tenant(tenant_id=TENANT, name="端到端测试", created_at=utc_now(), updated_at=utc_now()))
kit.put_quota(TENANT, "visits", "use", "per_use")
kit.put_level(TENANT, "basic")
kit.put_service(
    TENANT, "session", "visits", kind="composite", redemption_mode="durable", charge_units=1
)
kit.put_service(TENANT, "child", None)
kit.put_service(TENANT, "ai_chat", "visits", description="健康管家与报告解读，共享每月AI额度")
kit.put_service(TENANT, "medication_ai", "visits", description="用药提醒，使用同一份AI额度")
kit.put_limit(TENANT, "basic", "visits", "finite", 5, "week", "Asia/Shanghai")
kit.assign(TENANT, 42, "basic")
kit.put_quota(TENANT, "ai_tokens", "token", "reported_usage")
for subject, level, cap, used in [
    ("10139", "usage-plan", 500000, 152159),
    ("10140", "usage-unlimited", None, 152159),
    ("10141", "usage-zero", 0, 0),
    ("10142", "usage-plan", 500000, 600000),
]:
    kit.put_level(TENANT, level)
    kit.put_limit(
        TENANT,
        level,
        "ai_tokens",
        "unlimited" if cap is None else "finite",
        cap,
        "month",
        "Asia/Shanghai",
    )
    kit.assign(TENANT, subject, level)
    with kit.sessions.begin() as db:
        assignment = kit._assignment(db, TENANT, subject, utc_now())
        start, end = _period(utc_now(), "month", "Asia/Shanghai", assignment)
        db.add(
            Usage(
                tenant_id=TENANT,
                subject_id=subject,
                quota_code="ai_tokens",
                period_start=start,
                period_end=end,
                used_units=used,
            )
        )
inner = create_app(kit, "e2e-admin-key-000000000000000000000000000")
PREFIX = "/user-quota"


async def app(scope, receive, send):
    if scope["type"] == "http":
        path = scope["path"]
        if not path.startswith(PREFIX + "/"):
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"outside prefix"})
            return
        stripped = path[len(PREFIX) :]
        scope = dict(scope, path=stripped, raw_path=stripped.encode())
    await inner(scope, receive, send)


uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
