"""Trusted-host provisioning and per-operator credential delivery."""

from __future__ import annotations

import getpass
import os
import secrets
import stat
from contextlib import ExitStack
from pathlib import Path

from sqlalchemy import func, select

from .admin_auth import (
    BOOTSTRAP_HASH_PREFIX,
    ROLES,
    AdminAccountError,
    AdminAccounts,
    hash_password,
    validate_new_password,
)
from .core import utc_now
from .models import AdminUser


def _temporary_password(role: str) -> str:
    while True:
        password = secrets.token_urlsafe(24)
        try:
            validate_new_password(role, password)
        except AdminAccountError:
            continue
        return password


def bootstrap(accounts: AdminAccounts, directory: Path) -> list[str]:
    created = []
    # Remove incomplete handoffs on file-write or insert failure.
    with ExitStack() as rollback, accounts.kit.sessions.begin() as db:
        users = db.scalars(
            select(AdminUser).where(func.lower(AdminUser.username).in_(ROLES)).with_for_update()
        )
        existing = set()
        for user in users:
            if user.username not in ROLES or user.username != user.role:
                raise AdminAccountError(
                    f"Existing operator conflicts with role name {user.username}"
                )
            existing.add(user.username)
        missing = [role for role in ROLES if role not in existing]
        if not missing:
            return []

        assets = Path(__file__).resolve().parent / "admin_assets"
        if directory.resolve().is_relative_to(assets):
            raise AdminAccountError("Credential directory must be outside the console assets")
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not (
            stat.S_ISDIR(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o700
            and info.st_uid == os.geteuid()
        ):
            raise AdminAccountError(
                "Credential directory must be owned by this user with mode 0700"
            )

        now = utc_now()
        for role in missing:
            password = _temporary_password(role)
            path = directory / f"{role}.txt"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError as exc:
                raise AdminAccountError(f"Credential file already exists: {path}") from exc
            rollback.callback(path.unlink)
            with os.fdopen(fd, "w") as f:
                f.write(password + "\n")
                f.flush()
                os.fsync(f.fileno())
            db.add(
                AdminUser(
                    username=role,
                    role=role,
                    status="active",
                    password_hash=BOOTSTRAP_HASH_PREFIX + hash_password(password),
                    password_changed_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            accounts._record(db, f"host:{getpass.getuser()}", "bootstrap", role)
            created.append(role)
        db.flush()
        # Commit can succeed on the server even if its acknowledgement is lost.
        # Keep the completed handoffs once the transaction is ready to commit.
        rollback.pop_all()
    return created
