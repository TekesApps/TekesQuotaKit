from __future__ import annotations

import argparse
import os
import secrets

import uvicorn

from .api import create_app
from .core import QuotaKit
from .models import Base


def run() -> None:
    parser = argparse.ArgumentParser(description="TekesQuotaKit")
    parser.add_argument(
        "command", choices=["serve", "init-schema", "expire-sessions", "generate-secrets"]
    )
    parser.add_argument("--limit", type=int, default=500, help="Maximum sessions per expiry run")
    args = parser.parse_args()
    if args.command == "generate-secrets":
        print(f"TEKES_QUOTA_ADMIN_KEY={secrets.token_urlsafe(48)}")
        print(f"TEKES_QUOTA_TOKEN_SECRET={secrets.token_urlsafe(48)}")
        return
    database_url = os.environ["TEKES_QUOTA_DATABASE_URL"]
    kit = QuotaKit(database_url, os.environ["TEKES_QUOTA_TOKEN_SECRET"])
    if args.command == "init-schema":
        Base.metadata.create_all(kit.engine)
        print("TekesQuotaKit tables ready in configured database")
        return
    if args.command == "expire-sessions":
        print(f"Expired {kit.expire_sessions(args.limit)} composite sessions")
        return
    app = create_app(kit, os.environ["TEKES_QUOTA_ADMIN_KEY"])
    uvicorn.run(
        app,
        host=os.getenv("TEKES_QUOTA_HOST", "127.0.0.1"),
        port=int(os.getenv("TEKES_QUOTA_PORT", "9460")),
    )
