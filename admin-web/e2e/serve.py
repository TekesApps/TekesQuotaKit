"""Seeded Kit behind a proxy that strips /user-quota, for the console browser tests.

Usage: python admin-web/e2e/serve.py <sqlite-path> <port>
"""

import sys

import uvicorn

from tekes_quota_kit.admin_auth import AdminAccounts
from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaKit, utc_now
from tekes_quota_kit.models import Base, Tenant

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
