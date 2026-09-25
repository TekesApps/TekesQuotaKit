from __future__ import annotations

import argparse
import os

import uvicorn

from .api import create_app
from .core import QuotaKit
from .models import Base


def run() -> None:
    parser = argparse.ArgumentParser(description="TekesQuotaKit")
    parser.add_argument("command", choices=["serve", "init-schema"])
    args = parser.parse_args()
    database_url = os.environ["TEKES_QUOTA_DATABASE_URL"]
    kit = QuotaKit(database_url, os.environ["TEKES_QUOTA_TOKEN_SECRET"])
    if args.command == "init-schema":
        Base.metadata.create_all(kit.engine)
        print("TekesQuotaKit tables ready in configured database")
        return
    app = create_app(kit, os.environ["TEKES_QUOTA_ADMIN_KEY"])
    uvicorn.run(
        app,
        host=os.getenv("TEKES_QUOTA_HOST", "127.0.0.1"),
        port=int(os.getenv("TEKES_QUOTA_PORT", "9460")),
    )
