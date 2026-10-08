from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys

import uvicorn

from .admin_auth import AdminAccountError, AdminAccounts
from .api import create_app
from .core import QuotaKit
from .models import Base

ADMIN_USER_ACTIONS = ["add", "passwd", "disable", "enable", "list"]


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("New password: ")
    if getpass.getpass("Repeat password: ") != first:
        raise AdminAccountError("Passwords do not match")
    return first


def _admin_user(kit: QuotaKit, args: argparse.Namespace) -> int:
    accounts = AdminAccounts(kit)
    action = args.action
    if action == "list":
        for user in accounts.list():
            last = user["last_login_at"].isoformat() + "Z" if user["last_login_at"] else "-"
            print(f"{user['username']}\t{user['status']}\tlast login {last}")
        return 0
    if not args.username:
        raise AdminAccountError(f"admin-user {action} needs --username")
    if action == "add":
        accounts.create(args.username, _read_password(args.password_stdin))
        print(f"Created admin user {args.username}")
    elif action == "passwd":
        accounts.set_password(args.username, _read_password(args.password_stdin))
        print(f"Password changed for {args.username}; existing sessions were signed out")
    elif action == "disable":
        accounts.set_status(args.username, "disabled")
        print(f"Disabled {args.username}; existing sessions were signed out")
    elif action == "enable":
        accounts.set_status(args.username, "active")
        print(f"Enabled {args.username}")
    return 0


def run() -> None:
    parser = argparse.ArgumentParser(description="TekesQuotaKit")
    parser.add_argument(
        "command",
        choices=["serve", "init-schema", "expire-sessions", "generate-secrets", "admin-user"],
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=ADMIN_USER_ACTIONS,
        help="admin-user only: add, passwd, disable, enable, or list console accounts",
    )
    parser.add_argument("--limit", type=int, default=500, help="Maximum sessions per expiry run")
    parser.add_argument("--username", help="admin-user: console account name")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="admin-user add/passwd: read the password from one line of stdin",
    )
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
    if args.command == "admin-user":
        if args.action is None:
            parser.error("admin-user needs an action: " + ", ".join(ADMIN_USER_ACTIONS))
        try:
            sys.exit(_admin_user(kit, args))
        except AdminAccountError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    app = create_app(kit, os.environ["TEKES_QUOTA_ADMIN_KEY"])
    uvicorn.run(
        app,
        host=os.getenv("TEKES_QUOTA_HOST", "127.0.0.1"),
        port=int(os.getenv("TEKES_QUOTA_PORT", "9460")),
    )
