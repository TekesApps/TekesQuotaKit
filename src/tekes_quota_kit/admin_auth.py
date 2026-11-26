"""Named operator identities, role authorization and browser sessions."""

from __future__ import annotations

import getpass
import hashlib
import hmac
import re
import secrets
import string
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select, update

from .core import QuotaKit, utc_now
from .models import AdminLoginAttempt, AdminOperation, AdminSession, AdminUser

COOKIE = "tq_admin_session"
SESSION_TTL = timedelta(hours=12)
THROTTLE_WINDOW = timedelta(minutes=15)
THROTTLE_FAILURES = 5
PBKDF2_ROUNDS = 600_000
MIN_PASSWORD_LENGTH = 12
BOOTSTRAP_HASH_PREFIX = "initial$"
PASSWORD_AGE = timedelta(days=90)
ROLES = ("system_admin", "security_admin", "auditor")
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
        algorithm, rounds, salt, expected = encoded.removeprefix(BOOTSTRAP_HASH_PREFIX).split("$")
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


def validate_new_password(username: str, password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminAccountError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 128:
        raise AdminAccountError("Password must be at most 128 characters")

    if username.casefold() in password.casefold():
        raise AdminAccountError("Password must not contain the account name")
    if not all(
        any(c in group for c in password)
        for group in (
            string.ascii_uppercase,
            string.ascii_lowercase,
            string.digits,
            string.punctuation,
        )
    ):
        raise AdminAccountError("Password requires uppercase, lowercase, digit and punctuation")


class AdminAccounts:
    def __init__(self, kit: QuotaKit) -> None:
        self.kit = kit

    @staticmethod
    def _record(db, actor: str, operation: str, target: str, status: int = 200) -> None:
        db.add(
            AdminOperation(
                actor=actor,
                operation=operation,
                target=target,
                status=status,
                recorded_at=utc_now(),
            )
        )

    def record_request(self, actor: str, operation: str, target: str, status: int) -> None:
        with self.kit.sessions.begin() as db:
            self._record(db, actor, operation, target, status)

    def operations(self) -> list[dict]:
        with self.kit.sessions() as db:
            return [
                dict(row)
                for row in db.execute(
                    select(
                        AdminOperation.id,
                        AdminOperation.actor,
                        AdminOperation.operation,
                        AdminOperation.target,
                        AdminOperation.status,
                        AdminOperation.recorded_at,
                    )
                    .order_by(AdminOperation.id.desc())
                    .limit(200)
                ).mappings()
            ]

    # Account management, used by the CLI.
    def create(self, username: str, password: str, role: str) -> None:
        if not USERNAME.fullmatch(username):
            raise AdminAccountError("Username may use letters, digits, and . _ @ - (max 100)")
        if role not in ROLES:
            raise AdminAccountError("Choose an operator role")
        validate_new_password(username, password)
        now = utc_now()
        with self.kit.sessions.begin() as db:
            if db.scalar(select(AdminUser.id).where(AdminUser.username == username)):
                raise AdminAccountError(f"Admin user {username} already exists")
            db.add(
                AdminUser(
                    username=username,
                    role=role,
                    password_changed_at=now,
                    password_hash=hash_password(password),
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )

            self._record(db, f"host:{getpass.getuser()}", f"add {role}", username)

    def set_password(self, username: str, password: str, *, enroll_role: str | None = None) -> None:
        with self.kit.sessions.begin() as db:
            user = self._user(db, username)
            if enroll_role is not None:
                if enroll_role not in ROLES or user.role is not None:
                    raise AdminAccountError("Enrollment requires a legacy account and a valid role")
                user.role = enroll_role
            self._replace_password(db, user, password)
            self._record(
                db,
                f"host:{getpass.getuser()}",
                f"enroll {enroll_role}" if enroll_role else "passwd",
                username,
            )

    def _replace_password(self, db, user: AdminUser, password: str) -> None:
        validate_new_password(user.username, password)
        if verify_password(password, user.password_hash):
            raise AdminAccountError("New password must differ from the current password")
        user.password_hash = hash_password(password)
        user.password_changed_at = user.updated_at = utc_now()
        self._revoke_all(db, user.id)

    def revoke(self, username: str) -> None:
        with self.kit.sessions.begin() as db:
            self._revoke_all(db, self._user(db, username).id)

    def set_status(self, username: str, status: str, *, actor: str | None = None) -> None:
        if status not in {"active", "disabled"}:
            raise AdminAccountError("Status must be active or disabled")
        with self.kit.sessions.begin() as db:
            user = self._user(db, username)
            self._record(db, actor or f"host:{getpass.getuser()}", f"set {status}", username)
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
                    "role": u.role,
                    "created_at": u.created_at,
                    "last_login_at": u.last_login_at,
                }
                for u in db.scalars(select(AdminUser).order_by(AdminUser.username))
            ]

    def check_roster(self) -> None:
        with self.kit.sessions() as db:
            rows = db.execute(
                select(
                    AdminUser.role,
                    AdminUser.password_changed_at,
                    AdminUser.password_hash.startswith(BOOTSTRAP_HASH_PREFIX),
                ).where(AdminUser.status == "active", AdminUser.role.in_(ROLES))
            ).all()
        now = utc_now()
        current = {role for role, changed, _ in rows if changed and changed > now - PASSWORD_AGE}
        pending = {role for role, _, temporary in rows if temporary} - current
        missing = set(ROLES) - current - pending
        problems = []
        if missing:
            problems.append(
                "Missing active operator with current password: " + ", ".join(sorted(missing))
            )
        if pending:
            problems.append("Awaiting initial password change: " + ", ".join(sorted(pending)))
        if problems:
            raise AdminAccountError("; ".join(problems))

    def audit_log(self) -> list[dict]:
        with self.kit.sessions() as db:
            return [
                dict(row)
                for row in db.execute(
                    select(
                        AdminLoginAttempt.id,
                        AdminLoginAttempt.username,
                        AdminLoginAttempt.success,
                        AdminLoginAttempt.attempted_at,
                    )
                    .order_by(AdminLoginAttempt.id.desc())
                    .limit(200)
                ).mappings()
            ]

    # Browser sign-in.
    def login(self, username: str, password: str, new_password: str | None = None) -> str:
        now = utc_now()
        token = None
        with self.kit.sessions() as db:
            db.execute(
                delete(AdminLoginAttempt).where(
                    AdminLoginAttempt.attempted_at < now - timedelta(days=90)
                )
            )
            failures = db.scalar(
                select(func.count())
                .select_from(AdminLoginAttempt)
                .where(
                    AdminLoginAttempt.username == username,
                    AdminLoginAttempt.success.is_(False),
                    AdminLoginAttempt.attempted_at >= now - THROTTLE_WINDOW,
                )
            )
            if failures >= THROTTLE_FAILURES:
                db.commit()
                raise HTTPException(429, "尝试过于频繁，请 15 分钟后再试")
            user = db.scalar(
                select(AdminUser)
                .where(
                    AdminUser.username == username,
                    AdminUser.status == "active",
                    AdminUser.role.in_(ROLES),
                )
                .with_for_update()
            )
            valid = verify_password(password, user.password_hash if user else _DUMMY_HASH)
            temporary = bool(user and user.password_hash.startswith(BOOTSTRAP_HASH_PREFIX))
            ok = bool(valid and user and (user.password_changed_at or temporary))
            attempt = AdminLoginAttempt(username=username, success=False, attempted_at=now)
            db.add(attempt)
            if ok:
                if temporary and new_password is None:
                    db.commit()
                    raise HTTPException(403, "首次登录请设置新密码")
                if new_password is not None:
                    self._replace_password(db, user, new_password)
                    self._record(db, user.username, "rotate password", user.username)
                deadline = user.password_changed_at + PASSWORD_AGE
                if deadline <= now:
                    self._record(db, user.username, "expired password", user.username, 403)
                    db.commit()
                    raise HTTPException(403, "密码已到期，请输入当前密码并设置新密码")
                attempt.success = True
                token = secrets.token_urlsafe(32)
                user.last_login_at = now
                db.add(
                    AdminSession(
                        token_hash=_token_hash(token),
                        admin_user_id=user.id,
                        created_at=now,
                        expires_at=min(deadline, now + SESSION_TTL),
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
                select(AdminUser.username, AdminUser.role, AdminSession.expires_at)
                .join(AdminUser, AdminUser.id == AdminSession.admin_user_id)
                .where(
                    AdminSession.token_hash == _token_hash(token),
                    AdminSession.revoked_at.is_(None),
                    AdminSession.expires_at > utc_now(),
                    AdminUser.status == "active",
                    AdminUser.role.in_(ROLES),
                    AdminUser.password_changed_at > utc_now() - PASSWORD_AGE,
                )
            ).first()
        if row is None:
            return None
        return {
            "username": row.username,
            "role": row.role,
            "expires_at": row.expires_at.isoformat() + "Z",
        }

    def logout(self, token: str) -> None:
        with self.kit.sessions.begin() as db:
            db.execute(
                update(AdminSession)
                .where(AdminSession.token_hash == _token_hash(token))
                .values(revoked_at=utc_now())
            )

    @staticmethod
    def _user(db, username: str) -> AdminUser:
        user = db.scalar(select(AdminUser).where(AdminUser.username == username).with_for_update())
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


def require_admin_factory(accounts: AdminAccounts, role: str | None = None):
    def require_admin(request: Request) -> dict:
        authorization = request.headers.get("Authorization")
        if authorization is None:
            token = request.cookies.get(COOKIE, "")
        else:
            scheme, _, token = authorization.partition(" ")
            if scheme != "Bearer":
                raise HTTPException(401, "Invalid admin credential")
        user = accounts.session_user(token)
        if user is None:
            raise HTTPException(401, "登录已失效，请重新登录")
        request.state.operator_name = user["username"]
        if role is not None and user["role"] != role:
            raise HTTPException(403, "账号无此权限")
        if (
            authorization is None
            and request.method in UNSAFE_METHODS
            and request.headers.get("X-Admin-Request") != "1"
        ):
            raise HTTPException(403, "缺少请求保护头")
        return {**user, "via": "bearer" if authorization else "session"}

    return require_admin
