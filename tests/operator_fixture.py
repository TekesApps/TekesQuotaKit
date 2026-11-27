"""Seed an individual operator session for quota API contract tests."""

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import select

from tekes_quota_kit.admin_auth import SESSION_TTL
from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import utc_now
from tekes_quota_kit.models import AdminSession, AdminUser


def configured_client(kit, session_token):
    now = utc_now()
    with kit.sessions.begin() as db:
        digest = hashlib.sha256(session_token.encode()).hexdigest()
        if db.scalar(select(AdminSession.id).where(AdminSession.token_hash == digest)) is None:
            user = AdminUser(
                username="fixture-system",
                role="system_admin",
                status="active",
                password_hash="fixture-no-password-login",
                password_changed_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
            db.flush()
            db.add(
                AdminSession(
                    token_hash=digest,
                    admin_user_id=user.id,
                    created_at=now,
                    expires_at=now + SESSION_TTL,
                )
            )
    return TestClient(create_app(kit))
