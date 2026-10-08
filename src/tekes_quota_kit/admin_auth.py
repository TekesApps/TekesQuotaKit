"""Operator accounts and browser sessions for the web administration console.

The console signs in with a username and password and then uses an HttpOnly
session cookie. Scripts may keep using the deployment admin key as a Bearer
credential. Both reach the same /v1/admin endpoints through `require_admin`.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select, update

from .core import QuotaKit, utc_now
from .models import AdminLoginAttempt, AdminSession, AdminUser

COOKIE = "tq_admin_session"
SESSION_TTL = timedelta(hours=12)
THROTTLE_WINDOW = timedelta(minutes=15)
THROTTLE_FAILURES = 5
PBKDF2_ROUNDS = 600_000
MIN_PASSWORD_LENGTH = 12
USERNAME = re.compile(r"^[A-Za-z0-9._@-]{1,100}$")
# Path prefix the browser reached the console under, e.g. "/user-quota". Only shapes the
# cookie path, so a forged value can only make the caller's own cookie unusable.
BASE_PATH = re.compile(r"^(/[A-Za-z0-9._~-]+){0,8}$")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
    return hmac.compare_digest(actual, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# An unknown username still pays for one full password check.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


class AdminAccountError(ValueError):
    pass


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminAccountError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 128:
        raise AdminAccountError("Password must be at most 128 characters")


class AdminAccounts:
    def __init__(self, kit: QuotaKit) -> None:
        self.kit = kit

    # Account management, used by the CLI.
    def create(self, username: str, password: str) -> None:
        if not USERNAME.fullmatch(username):
            raise AdminAccountError("Username may use letters, digits, and . _ @ - (max 100)")
        validate_new_password(password)
        now = utc_now()
        with self.kit.sessions.begin() as db:
            if db.scalar(select(AdminUser.id).where(AdminUser.username == username)):
                raise AdminAccountError(f"Admin user {username} already exists")
            db.add(
                AdminUser(
                    username=username,
                    password_hash=hash_password(password),
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )

    def set_password(self, username: str, password: str) -> None:
        validate_new_password(password)
        with self.kit.sessions.begin() as db:
            user = self._user(db, username)
            user.password_hash = hash_password(password)
            user.updated_at = utc_now()
            self._revoke_all(db, user.id)

    def set_status(self, username: str, status: str) -> None:
        if status not in {"active", "disabled"}:
            raise AdminAccountError("Status must be active or disabled")
        with self.kit.sessions.begin() as db:
            user = self._user(db, username)
            user.status = status
            user.updated_at = utc_now()
            if status == "disabled":
                self._revoke_all(db, user.id)

    def list(self) -> list[dict]:
        with self.kit.sessions() as db:
            return [
                {
                    "username": u.username,
                    "status": u.status,
                    "created_at": u.created_at,
                    "last_login_at": u.last_login_at,
                }
                for u in db.scalars(select(AdminUser).order_by(AdminUser.username))
            ]

    # Browser sign-in.
    def login(self, username: str, password: str) -> str:
        now = utc_now()
        token = None
        with self.kit.sessions() as db:
            db.execute(
                delete(AdminLoginAttempt).where(
                    AdminLoginAttempt.attempted_at < now - THROTTLE_WINDOW
                )
            )
            failures = db.scalar(
                select(func.count())
                .select_from(AdminLoginAttempt)
                .where(
                    AdminLoginAttempt.username == username,
                    AdminLoginAttempt.success.is_(False),
                )
            )
            if failures >= THROTTLE_FAILURES:
                db.commit()
                raise HTTPException(429, "尝试过于频繁，请 15 分钟后再试")
            user = db.scalar(
                select(AdminUser).where(
                    AdminUser.username == username, AdminUser.status == "active"
                )
            )
            valid = verify_password(password, user.password_hash if user else _DUMMY_HASH)
            ok = bool(valid and user)
            db.add(AdminLoginAttempt(username=username, success=ok, attempted_at=now))
            if ok:
                token = secrets.token_urlsafe(32)
                user.last_login_at = now
                db.add(
                    AdminSession(
                        token_hash=_token_hash(token),
                        admin_user_id=user.id,
                        created_at=now,
                        expires_at=now + SESSION_TTL,
                    )
                )
            # Commit failed attempts too, so the throttle counts them.
            db.commit()
        if token is None:
            raise HTTPException(401, "账号或密码错误")
        return token

    def session_user(self, token: str) -> dict | None:
        if not token:
            return None
        with self.kit.sessions() as db:
            row = db.execute(
                select(AdminUser.username, AdminSession.expires_at)
                .join(AdminUser, AdminUser.id == AdminSession.admin_user_id)
                .where(
                    AdminSession.token_hash == _token_hash(token),
                    AdminSession.revoked_at.is_(None),
                    AdminSession.expires_at > utc_now(),
                    AdminUser.status == "active",
                )
            ).first()
        if row is None:
            return None
        return {"username": row.username, "expires_at": row.expires_at.isoformat() + "Z"}

    def logout(self, token: str) -> None:
        with self.kit.sessions.begin() as db:
            db.execute(
                update(AdminSession)
                .where(AdminSession.token_hash == _token_hash(token))
                .values(revoked_at=utc_now())
            )

    @staticmethod
    def _user(db, username: str) -> AdminUser:
        user = db.scalar(select(AdminUser).where(AdminUser.username == username))
        if user is None:
            raise AdminAccountError(f"Admin user {username} not found")
        return user

    @staticmethod
    def _revoke_all(db, user_id: int) -> None:
        db.execute(
            update(AdminSession)
            .where(AdminSession.admin_user_id == user_id, AdminSession.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )


def cookie_path(request: Request) -> str:
    base = request.headers.get("X-Admin-Base", "")
    if not BASE_PATH.fullmatch(base):
        raise HTTPException(400, "Invalid X-Admin-Base")
    return f"{base}/v1/admin"


def cookie_secure(request: Request) -> bool:
    proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


def require_admin_factory(accounts: AdminAccounts, admin_key: str):
    """Accept the deployment admin key as Bearer, or a console session cookie.

    Cookie-authenticated writes must carry `X-Admin-Request: 1`. The app adds no CORS
    headers, so a cross-site page cannot send that header, which blocks CSRF.
    """

    def require_admin(request: Request) -> dict:
        authorization = request.headers.get("Authorization")
        if authorization is not None:
            supplied = authorization.removeprefix("Bearer ")
            if authorization.startswith("Bearer ") and hmac.compare_digest(supplied, admin_key):
                return {"username": None, "via": "admin_key"}
            raise HTTPException(status_code=401, detail="Invalid admin credential")
        user = accounts.session_user(request.cookies.get(COOKIE, ""))
        if user is None:
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
        if request.method in UNSAFE_METHODS and request.headers.get("X-Admin-Request") != "1":
            raise HTTPException(status_code=403, detail="缺少请求保护头")
        return {**user, "via": "session"}

    return require_admin
