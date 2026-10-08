from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    Assignment,
    Client,
    Ledger,
    Level,
    Limit,
    Quota,
    Service,
    ServiceMember,
    Token,
    TokenItem,
    Usage,
)

MAX_SUBJECT_ID = 9223372036854775807


def _valid_subject(subject: int) -> bool:
    return type(subject) is int and 0 < subject <= MAX_SUBJECT_ID


MEMBERSHIP_ROLE = "membership"
# Membership clients are not bound to a Service; tq_clients.service_code stores this placeholder.
MEMBERSHIP_SERVICE = "*"
CLIENT_ROLES = {"issuer", "provider", "consumer", MEMBERSHIP_ROLE}


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class QuotaError(Exception):
    def __init__(
        self, code: str, message: str, status: int = 409, details: dict | None = None
    ) -> None:
        self.code, self.message, self.status = code, message, status
        self.details = details or {}
        super().__init__(message)


def _require(condition: bool, code: str, message: str, status: int = 409) -> None:
    if not condition:
        raise QuotaError(code, message, status)


def _period(
    now: datetime, kind: str, timezone: str, assignment: Assignment
) -> tuple[datetime, datetime]:
    if kind == "level_term":
        _require(
            assignment.term_end is not None or assignment.expires_at is not None,
            "missing_term_end",
            "Level term requires expiry",
        )
        return (
            assignment.term_start or assignment.effective_at,
            assignment.term_end or assignment.expires_at,
        )
    local = now.replace(tzinfo=UTC).astimezone(ZoneInfo(timezone))
    if kind == "day":
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif kind == "week":
        # Natural week: Monday 00:00 to the next Monday 00:00 in the Limit's timezone.
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        start = midnight - timedelta(days=local.weekday())
        end = start + timedelta(days=7)
    elif kind == "month":
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
    else:
        raise QuotaError("invalid_period", "Unsupported period kind", 400)
    return start.astimezone(UTC).replace(tzinfo=None), end.astimezone(UTC).replace(tzinfo=None)


class QuotaKit:
    """MySQL-backed authority. Database URL points at the consumer's existing schema."""

    def __init__(self, database_url: str, token_secret: str) -> None:
        _require(
            database_url.startswith(("mysql+pymysql://", "sqlite:///")),
            "unsupported_database",
            "MySQL or SQLite URL required",
            500,
        )
        _require(
            len(token_secret) >= 32,
            "weak_secret",
            "Token secret must be at least 32 characters",
            500,
        )
        self.engine = create_engine(database_url, pool_pre_ping=True)
        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def _sqlite_connect(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=10000")

            @event.listens_for(self.engine, "begin")
            def _sqlite_begin(connection):
                # Serialize read/check/write transactions before the first read.
                connection.exec_driver_sql("BEGIN IMMEDIATE")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.secret = token_secret.encode()

    @staticmethod
    def key_hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def client(self, key: str, role: str) -> Client:
        # `consumer` covers issuer and provider endpoints only; member sync needs its own role.
        allowed = {role} if role == MEMBERSHIP_ROLE else {role, "consumer"}
        with self.sessions() as db:
            client = db.scalar(select(Client).where(Client.key_hash == self.key_hash(key)))
            _require(
                client is not None and client.role in allowed,
                "unauthorized",
                "Invalid client credential",
                401,
            )
            return client

    def put_client(
        self, client_id: str, key: str, tenant_id: str, service_code: str, role: str
    ) -> None:
        _require(role in CLIENT_ROLES, "invalid_role", "Invalid client role", 400)
        if role == MEMBERSHIP_ROLE:
            service_code = MEMBERSHIP_SERVICE
        _require(
            len(key) >= 32, "weak_client_key", "Client key must be at least 32 characters", 400
        )
        with self.sessions.begin() as db:
            row = db.scalar(select(Client).where(Client.client_id == client_id))
            if row is None:
                row = Client(client_id=client_id)
                db.add(row)
            row.key_hash, row.tenant_id, row.service_code, row.role = (
                self.key_hash(key),
                tenant_id,
                service_code,
                role,
            )

    def provision_client(
        self,
        client_id: str,
        tenant_id: str,
        service_code: str,
        role: str,
        *,
        rotate: bool = False,
    ) -> tuple[str, Service | None]:
        """Generate a client key; only its hash is persisted.

        A `membership` client maintains the member list and is not bound to a Service, so it
        returns no Service and stores the placeholder `*`.
        """
        _require(role in CLIENT_ROLES, "invalid_role", "Invalid client role", 400)
        key = secrets.token_urlsafe(48)
        with self.sessions.begin() as db:
            if role == MEMBERSHIP_ROLE:
                service, service_code = None, MEMBERSHIP_SERVICE
            else:
                service = db.scalar(
                    select(Service).where(
                        Service.tenant_id == tenant_id,
                        Service.service_code == service_code,
                    )
                )
                _require(service is not None, "unknown_service", "Service not found", 404)
            row = db.scalar(select(Client).where(Client.client_id == client_id).with_for_update())
            _require(
                row is None or rotate,
                "client_exists",
                "Client exists; request an explicit key rotation",
            )
            if row is None:
                row = Client(client_id=client_id)
                db.add(row)
            row.key_hash = self.key_hash(key)
            row.tenant_id = tenant_id
            row.service_code = service_code
            row.role = role
            db.flush()
            if service is not None:
                db.expunge(service)
        return key, service

    def put_quota(self, tenant: str, code: str, unit: str, mode: str) -> None:
        _require(
            mode in {"per_use", "reported_usage"}, "invalid_mode", "Invalid metering mode", 400
        )
        _require(
            mode != "per_use" or unit == "use",
            "invalid_unit",
            "Per-use quota unit must be use",
            400,
        )
        with self.sessions.begin() as db:
            row = db.scalar(
                select(Quota).where(Quota.tenant_id == tenant, Quota.quota_code == code)
            )
            if row is None:
                row = Quota(tenant_id=tenant, quota_code=code)
                db.add(row)
            else:
                _require(
                    row.unit_code == unit and row.metering_mode == mode,
                    "quota_definition_immutable",
                    "Quota unit and metering mode cannot be changed in place",
                )
            row.unit_code, row.metering_mode = unit, mode

    def put_service(
        self,
        tenant: str,
        code: str,
        quota: str | None,
        *,
        kind: str = "atomic",
        redemption_mode: str | None = None,
        charge_units: int | None = None,
        session_ttl_seconds: int | None = None,
    ) -> int:
        redemption_mode = redemption_mode or ("durable" if kind == "composite" else "instant")
        _require(
            kind in {"atomic", "composite"}, "invalid_service_kind", "Invalid Service kind", 400
        )
        _require(
            redemption_mode in {"instant", "durable"}
            and (kind != "composite" or redemption_mode == "durable")
            and (
                (
                    redemption_mode == "instant"
                    and kind == "atomic"
                    and charge_units is None
                    and session_ttl_seconds is None
                )
                or (
                    redemption_mode == "durable"
                    and quota is not None
                    and charge_units is not None
                    and charge_units > 0
                    and (session_ttl_seconds is None or 60 <= session_ttl_seconds <= 86400)
                )
            ),
            "invalid_service_config",
            "Invalid Service configuration",
            400,
        )
        with self.sessions.begin() as db:
            if quota is not None:
                quota_row = self._quota(db, tenant, quota)
                _require(quota_row is not None, "unknown_quota", "Quota not found", 404)
                if redemption_mode == "durable":
                    _require(
                        quota_row.metering_mode == "per_use",
                        "wrong_mode",
                        "Durable Service needs per-use Quota",
                    )
            row = db.scalar(
                select(Service).where(Service.tenant_id == tenant, Service.service_code == code)
            )
            if row is None:
                row = Service(tenant_id=tenant, service_code=code)
                db.add(row)
            row.quota_code = quota
            row.service_kind = kind
            row.redemption_mode = redemption_mode
            row.charge_units = charge_units
            row.session_ttl_seconds = session_ttl_seconds
            db.flush()
            return row.id

    def put_service_member(
        self, tenant: str, parent: str, child: str, max_uses: int | None = None
    ) -> None:
        _require(parent != child, "invalid_member", "Parent and child must differ", 400)
        _require(
            max_uses is None or (type(max_uses) is int and 1 <= max_uses <= 2147483647),
            "invalid_member",
            "Max uses must be positive or null",
            400,
        )
        with self.sessions.begin() as db:
            services = db.scalars(
                select(Service).where(
                    Service.tenant_id == tenant, Service.service_code.in_([parent, child])
                )
            ).all()
            by_code = {row.service_code: row for row in services}
            _require(
                parent in by_code and child in by_code, "unknown_service", "Service not found", 404
            )
            _require(
                by_code[parent].service_kind == "composite"
                and by_code[child].service_kind == "atomic",
                "invalid_member",
                "Parent must be composite and child must be atomic",
                400,
            )
            row = db.scalar(
                select(ServiceMember).where(
                    ServiceMember.tenant_id == tenant,
                    ServiceMember.parent_service_code == parent,
                    ServiceMember.child_service_code == child,
                )
            )
            if row is None:
                row = ServiceMember(
                    tenant_id=tenant, parent_service_code=parent, child_service_code=child
                )
                db.add(row)
            row.max_uses = max_uses

    def delete_service_member(self, tenant: str, parent: str, child: str) -> None:
        with self.sessions.begin() as db:
            row = db.scalar(
                select(ServiceMember).where(
                    ServiceMember.tenant_id == tenant,
                    ServiceMember.parent_service_code == parent,
                    ServiceMember.child_service_code == child,
                )
            )
            _require(row is not None, "unknown_member", "Service member not found", 404)
            db.delete(row)

    def put_level(self, tenant: str, code: str) -> None:
        with self.sessions.begin() as db:
            if self._level(db, tenant, code) is None:
                db.add(Level(tenant_id=tenant, level_code=code))

    def put_limit(
        self,
        tenant: str,
        level: str,
        quota: str,
        mode: str,
        value: int | None,
        period: str,
        timezone: str,
    ) -> None:
        _require(mode in {"finite", "unlimited"}, "invalid_limit_mode", "Invalid limit mode", 400)
        _require(
            (mode == "finite" and value is not None and value >= 0)
            or (mode == "unlimited" and value is None),
            "invalid_limit",
            "Invalid limit value",
            400,
        )
        periods = {"day", "week", "month", "level_term"}
        _require(period in periods, "invalid_period", "Invalid period", 400)
        try:
            ZoneInfo(timezone)
        except (KeyError, ValueError) as exc:
            raise QuotaError("invalid_timezone", "Unknown timezone", 400) from exc
        with self.sessions.begin() as db:
            _require(
                self._level(db, tenant, level) is not None, "unknown_level", "Level not found", 404
            )
            _require(
                self._quota(db, tenant, quota) is not None, "unknown_quota", "Quota not found", 404
            )
            row = db.scalar(
                select(Limit).where(
                    Limit.tenant_id == tenant, Limit.level_code == level, Limit.quota_code == quota
                )
            )
            if row is None:
                row = Limit(tenant_id=tenant, level_code=level, quota_code=quota)
                db.add(row)
            else:
                _require(
                    row.period_kind == period and row.timezone == timezone,
                    "period_immutable",
                    "Limit period and timezone cannot be changed in place",
                )
            row.limit_mode, row.limit_value, row.period_kind, row.timezone = (
                mode,
                value,
                period,
                timezone,
            )

    def assign(
        self,
        tenant: str,
        subject: int,
        level: str,
        expires_at: datetime | None = None,
        *,
        effective_at: datetime | None = None,
        renew_term: bool = False,
    ) -> None:
        now = utc_now()
        effective_at = effective_at or now
        _require(
            _valid_subject(subject), "invalid_subject", "Subject ID must be a positive integer", 400
        )
        _require(
            effective_at >= now - timedelta(seconds=5),
            "invalid_effective_at",
            "Effective time is in past",
            400,
        )
        _require(
            expires_at is None or expires_at > effective_at,
            "invalid_expiry",
            "Expiry must follow start",
            400,
        )
        with self.sessions.begin() as db:
            _require(
                self._level(db, tenant, level) is not None, "unknown_level", "Level not found", 404
            )
            active = self._assignment(db, tenant, subject, effective_at, lock=True, required=False)
            if active and not renew_term:
                term_start = active.term_start or active.effective_at
                term_end = active.term_end or active.expires_at
                _require(
                    expires_at is None or expires_at == term_end,
                    "term_change_requires_renewal",
                    "Changing term end requires renewal",
                    400,
                )
                expires_at = term_end
            else:
                term_start, term_end = effective_at, expires_at
            if active:
                active.expires_at = effective_at
            db.add(
                Assignment(
                    tenant_id=tenant,
                    subject_id=subject,
                    level_code=level,
                    effective_at=effective_at,
                    expires_at=expires_at,
                    term_start=term_start,
                    term_end=term_end,
                )
            )

    def end_membership(self, tenant: str, subject: int) -> bool:
        """Close the subject's current assignment now. Returns False if none was active."""
        _require(
            _valid_subject(subject), "invalid_subject", "Subject ID must be a positive integer", 400
        )
        now = utc_now()
        with self.sessions.begin() as db:
            active = self._assignment(db, tenant, subject, now, lock=True, required=False)
            if active is None:
                return False
            active.expires_at = now
            if active.term_end is None or active.term_end > now:
                active.term_end = now
            return True

    def membership(self, tenant: str, subject: int) -> dict | None:
        """The subject's current assignment, or None when the subject has no active Level."""
        _require(
            _valid_subject(subject), "invalid_subject", "Subject ID must be a positive integer", 400
        )
        with self.sessions() as db:
            active = self._assignment(db, tenant, subject, utc_now(), required=False)
            if active is None:
                return None
            stamp = lambda value: value.isoformat() + "Z" if value else None  # noqa: E731
            return {
                "level_code": active.level_code,
                "effective_at": stamp(active.effective_at),
                "expires_at": stamp(active.expires_at),
                "term_start": stamp(active.term_start),
                "term_end": stamp(active.term_end),
            }

    @staticmethod
    def _level(db: Session, tenant: str, code: str) -> Level | None:
        return db.scalar(select(Level).where(Level.tenant_id == tenant, Level.level_code == code))

    @staticmethod
    def _quota(db: Session, tenant: str, code: str) -> Quota | None:
        return db.scalar(select(Quota).where(Quota.tenant_id == tenant, Quota.quota_code == code))

    @staticmethod
    def _assignment(
        db: Session,
        tenant: str,
        subject: int,
        now: datetime,
        *,
        lock: bool = False,
        required: bool = True,
    ) -> Assignment | None:
        query = (
            select(Assignment)
            .where(
                Assignment.tenant_id == tenant,
                Assignment.subject_id == subject,
                Assignment.effective_at <= now,
                (Assignment.expires_at.is_(None) | (Assignment.expires_at > now)),
            )
            .order_by(Assignment.effective_at.desc(), Assignment.id.desc())
            .limit(1)
        )
        if lock:
            query = query.with_for_update()
        assignment = db.scalar(query)
        if required:
            _require(assignment is not None, "no_level", "No active Level")
        return assignment

    @staticmethod
    def _policy(db: Session, tenant: str, subject: int, service_code: str, now: datetime):
        service = db.scalar(
            select(Service).where(Service.tenant_id == tenant, Service.service_code == service_code)
        )
        _require(service is not None, "unknown_service", "Service not found", 404)
        quota = QuotaKit._quota(db, tenant, service.quota_code)
        _require(quota is not None, "no_quota", "Service has no direct Quota")
        assignment = QuotaKit._assignment(db, tenant, subject, now)
        limit = db.scalar(
            select(Limit).where(
                Limit.tenant_id == tenant,
                Limit.level_code == assignment.level_code,
                Limit.quota_code == quota.quota_code,
            )
        )
        _require(limit is not None, "no_limit", "Level has no Limit for Service")
        start, end = _period(now, limit.period_kind, limit.timezone, assignment)
        return quota, limit, start, end

    @staticmethod
    def _usage(
        db: Session, tenant: str, subject: int, quota: str, start: datetime, end: datetime
    ) -> Usage:
        values = dict(
            tenant_id=tenant,
            subject_id=subject,
            quota_code=quota,
            period_start=start,
            period_end=end,
            used_units=0,
        )
        if db.bind.dialect.name == "mysql":
            stmt = mysql_insert(Usage).values(**values)
            db.execute(stmt.on_duplicate_key_update(id=Usage.id))
        else:
            stmt = sqlite_insert(Usage).values(**values)
            db.execute(
                stmt.on_conflict_do_nothing(
                    index_elements=["tenant_id", "subject_id", "quota_code", "period_start"]
                )
            )
        return db.scalar(
            select(Usage)
            .where(
                Usage.tenant_id == tenant,
                Usage.subject_id == subject,
                Usage.quota_code == quota,
                Usage.period_start == start,
            )
            .with_for_update()
        )

    @staticmethod
    def _service_for_caller(db: Session, caller: Client, service_id: int | str) -> Service:
        identity = (
            Service.id == service_id
            if type(service_id) is int
            else Service.service_code == service_id
        )
        service = db.scalar(select(Service).where(identity, Service.tenant_id == caller.tenant_id))
        _require(service is not None, "unknown_service", "Service not found", 404)
        _require(
            hmac.compare_digest(caller.service_code, service.service_code),
            "service_forbidden",
            "Client cannot access this Service",
            403,
        )
        return service

    def _create_token(
        self,
        db: Session,
        caller: Client,
        subject: int,
        service: Service,
        request_key: str,
        now: datetime,
    ) -> tuple[str, Token, bool]:
        token = self._token_for_request(caller, subject, service.service_code, request_key)
        token_hash = self.key_hash(token)
        values = dict(
            token_hash=token_hash,
            tenant_id=caller.tenant_id,
            subject_id=subject,
            service_code=service.service_code,
            quota_code=service.quota_code,
            usage_key=token_hash,
            request_key=request_key,
            issuer_client_id=caller.client_id,
            status="admitted",
            admitted_at=now,
        )
        if db.bind.dialect.name == "mysql":
            # INSERT IGNORE reports 0 on a duplicate even with MySQL FOUND_ROWS enabled.
            stmt = mysql_insert(Token).values(**values).prefix_with("IGNORE")
            result = db.execute(stmt)
            inserted = result.rowcount == 1
        else:
            stmt = sqlite_insert(Token).values(**values)
            result = db.execute(stmt.on_conflict_do_nothing(index_elements=["token_hash"]))
            inserted = result.rowcount == 1
        row = db.scalar(select(Token).where(Token.token_hash == token_hash).with_for_update())
        return token, row, inserted

    def _token_for_request(
        self, caller: Client, subject: int, service_code: str, request_key: str
    ) -> str:
        identity = json.dumps(
            [caller.tenant_id, caller.client_id, subject, service_code, request_key],
            separators=(",", ":"),
        )
        return "tq_" + hmac.new(self.secret, identity.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _validate_request(subject: int, service_id: int) -> None:
        _require(
            _valid_subject(subject) and type(service_id) is int and service_id > 0,
            "missing_identity",
            "Subject and Service required",
            400,
        )

    @staticmethod
    def _validate_request_key(request_key: str | None) -> None:
        _require(
            request_key is None or (type(request_key) is str and 0 < len(request_key) <= 128),
            "invalid_request_key",
            "Request key must be 1 to 128 characters",
            400,
        )

    def issue(
        self, issuer: Client, subject: int, service_id: int, request_key: str | None = None
    ) -> dict:
        """Admit reported usage and return the token needed for later settlement."""
        self._validate_request(subject, service_id)
        self._validate_request_key(request_key)
        request_key = request_key or secrets.token_hex(16)
        now = utc_now()
        with self.sessions.begin() as db:
            service = self._service_for_caller(db, issuer, service_id)
            _require(
                service.service_kind == "atomic",
                "wrong_service_kind",
                "Composite Service requires begin",
            )
            quota = self._quota(db, issuer.tenant_id, service.quota_code)
            _require(quota is not None, "no_quota", "Service has no direct Quota")
            _require(
                quota.metering_mode == "reported_usage",
                "wrong_mode",
                "Per-use Service requires direct redemption",
            )
            token, row, inserted = self._create_token(
                db, issuer, subject, service, request_key, now
            )
            if not inserted:
                return {"token": token, "status": row.status, "idempotent": True}
            quota, limit, start, end = self._policy(
                db, issuer.tenant_id, subject, service.service_code, now
            )
            usage = self._usage(db, issuer.tenant_id, subject, quota.quota_code, start, end)
            _require(
                limit.limit_mode == "unlimited" or usage.used_units < limit.limit_value,
                "quota_exhausted",
                "Quota exhausted",
            )
            row.period_start, row.period_end = start, end
            row.unit_code, row.metering_mode, row.limit_value = (
                quota.unit_code,
                quota.metering_mode,
                limit.limit_value,
            )
            return {
                "token": token,
                "status": row.status,
                "idempotent": False,
            }

    def redeem(
        self,
        provider: Client,
        subject: int,
        service_id: int | str,
        request_key: str | None = None,
        duration_seconds: int | None = None,
    ) -> dict:
        """Atomically charge instant use or open a charged durable use."""
        _require(
            _valid_subject(subject)
            and (
                (type(service_id) is int and service_id > 0)
                or (type(service_id) is str and 0 < len(service_id) <= 64)
            ),
            "missing_identity",
            "Subject and Service required",
            400,
        )
        self._validate_request_key(request_key)
        _require(
            duration_seconds is None or 60 <= duration_seconds <= 86400,
            "invalid_duration",
            "Duration must be 60 to 86400 seconds",
            400,
        )
        with self.sessions() as db:
            service = self._service_for_caller(db, provider, service_id)
            durable = service.redemption_mode == "durable"
            service_code = service.service_code
            if request_key is not None:
                token = self._token_for_request(provider, subject, service_code, request_key)
                prior = db.scalar(select(Token).where(Token.token_hash == self.key_hash(token)))
                durable = durable or (prior is not None and prior.session_status is not None)
        if durable:
            _require(
                request_key is not None,
                "missing_request_key",
                "Durable use needs request key",
                400,
            )
            return self.begin(provider, subject, service_code, request_key, duration_seconds)
        _require(
            duration_seconds is None,
            "wrong_mode",
            "Instant use does not accept duration",
            400,
        )
        request_key = request_key or secrets.token_hex(16)
        now = utc_now()
        with self.sessions.begin() as db:
            service = self._service_for_caller(db, provider, service_id)
            _require(
                service.redemption_mode == "instant" and service.service_kind == "atomic",
                "wrong_service_kind",
                "Durable Service requires durable redemption",
            )
            quota = self._quota(db, provider.tenant_id, service.quota_code)
            _require(quota is not None, "no_quota", "Service has no direct Quota")
            _require(
                quota.metering_mode == "per_use", "wrong_mode", "Metered Service requires token"
            )
            token, row, inserted = self._create_token(
                db, provider, subject, service, request_key, now
            )
            if not inserted:
                raise QuotaError(
                    "already_redeemed",
                    "Request already redeemed",
                    details={"token": token, "status": row.status},
                )
            quota, limit, start, end = self._policy(
                db, provider.tenant_id, subject, service.service_code, now
            )
            usage = self._usage(db, row.tenant_id, subject, quota.quota_code, start, end)
            _require(
                limit.limit_mode == "unlimited" or usage.used_units < limit.limit_value,
                "quota_exhausted",
                "Quota exhausted",
            )
            row.status = "settled"
            row.provider_client_id = provider.client_id
            row.period_start, row.period_end = start, end
            row.unit_code, row.metering_mode, row.limit_value = (
                quota.unit_code,
                quota.metering_mode,
                limit.limit_value,
            )
            usage.used_units += 1
            row.consumed_units = 1
            self._ledger(db, row, "consume", 1, now)
            return {
                "token": token,
                "status": row.status,
                "unit": row.unit_code,
                "usage_key": row.usage_key,
                "quota_code": row.quota_code,
            }

    def begin(
        self,
        provider: Client,
        subject: int,
        service_code: str,
        request_key: str,
        duration_seconds: int | None = None,
    ) -> dict:
        """Charge once and snapshot the allowed Services for durable use."""
        _require(_valid_subject(subject), "invalid_subject", "Invalid subject", 400)
        self._validate_request_key(request_key)
        _require(
            duration_seconds is None or 60 <= duration_seconds <= 86400,
            "invalid_duration",
            "Duration must be 60 to 86400 seconds",
            400,
        )
        _require(
            request_key is not None, "missing_request_key", "Composite use needs request key", 400
        )
        now = utc_now()
        with self.sessions.begin() as db:
            service = db.scalar(
                select(Service).where(
                    Service.tenant_id == provider.tenant_id,
                    Service.service_code == service_code,
                )
            )
            _require(service is not None, "unknown_service", "Service not found", 404)
            _require(
                hmac.compare_digest(provider.service_code, service_code),
                "service_forbidden",
                "Client cannot access this Service",
                403,
            )
            token = self._token_for_request(provider, subject, service_code, request_key)
            prior = db.scalar(
                select(Token).where(Token.token_hash == self.key_hash(token)).with_for_update()
            )
            if prior is not None:
                _require(
                    duration_seconds is None
                    or prior.session_expires_at
                    == prior.admitted_at + timedelta(seconds=duration_seconds),
                    "duration_conflict",
                    "Request key already has another duration",
                )
                grants = db.scalars(
                    select(TokenItem).where(
                        TokenItem.token_id == prior.id, TokenItem.slot_no == 0
                    )
                ).all()
                return {
                    "token": token,
                    "status": prior.status,
                    "session_status": prior.session_status,
                    "expires_at": (
                        prior.session_expires_at.isoformat() + "Z"
                        if prior.session_expires_at is not None
                        else None
                    ),
                    "authorized_services": [item.child_service_code for item in grants],
                    "idempotent": True,
                }
            _require(
                service.redemption_mode == "durable",
                "wrong_service_kind",
                "Service is not durable",
            )
            token, row, inserted = self._create_token(
                db, provider, subject, service, request_key, now
            )
            if not inserted:
                _require(
                    duration_seconds is None
                    or row.session_expires_at
                    == row.admitted_at + timedelta(seconds=duration_seconds),
                    "duration_conflict",
                    "Request key already has another duration",
                )
                grants = db.scalars(
                    select(TokenItem).where(
                        TokenItem.token_id == row.id, TokenItem.slot_no == 0
                    )
                ).all()
                return {
                    "token": token,
                    "status": row.status,
                    "session_status": row.session_status,
                    "expires_at": (
                        row.session_expires_at.isoformat() + "Z"
                        if row.session_expires_at is not None
                        else None
                    ),
                    "authorized_services": [item.child_service_code for item in grants],
                    "idempotent": True,
                }
            quota, limit, start, end = self._policy(
                db, provider.tenant_id, subject, service_code, now
            )
            _require(
                quota.metering_mode == "per_use", "wrong_mode", "Composite needs per-use Quota"
            )
            members = db.scalars(
                select(ServiceMember)
                .where(
                    ServiceMember.tenant_id == provider.tenant_id,
                    ServiceMember.parent_service_code == service_code,
                )
                .order_by(ServiceMember.child_service_code)
            ).all()
            if service.service_kind == "composite":
                _require(bool(members), "empty_composite", "Composite Service has no members")
            usage = self._usage(db, provider.tenant_id, subject, quota.quota_code, start, end)
            units = service.charge_units
            _require(
                limit.limit_mode == "unlimited" or usage.used_units + units <= limit.limit_value,
                "quota_exhausted",
                "Quota exhausted",
            )
            row.status = "settled"
            row.provider_client_id = provider.client_id
            row.period_start, row.period_end = start, end
            row.unit_code, row.metering_mode, row.limit_value = (
                quota.unit_code,
                quota.metering_mode,
                limit.limit_value,
            )
            row.consumed_units = units
            row.session_status = "open"
            ttl_seconds = (
                duration_seconds
                if duration_seconds is not None
                else service.session_ttl_seconds
            )
            row.session_expires_at = (
                now + timedelta(seconds=ttl_seconds)
                if ttl_seconds is not None
                else None
            )
            usage.used_units += units
            self._ledger(db, row, "consume", units, now)
            child_grants = (
                [(member.child_service_code, member.max_uses) for member in members]
                if service.service_kind == "composite"
                else [(service.service_code, None)]
            )
            for child_code, max_uses in child_grants:
                db.add(
                    TokenItem(
                        token_id=row.id,
                        child_service_code=child_code,
                        slot_no=0,
                        max_uses=max_uses,
                        status="available",
                    )
                )
            return {
                "token": token,
                "status": "settled",
                "session_status": "open",
                "expires_at": (
                    row.session_expires_at.isoformat() + "Z"
                    if row.session_expires_at is not None
                    else None
                ),
                "authorized_services": [code for code, _ in child_grants],
                "idempotent": False,
            }

    def use(
        self, token: str, provider: Client, subject: int, child_code: str, request_key: str
    ) -> dict:
        self._validate_request_key(request_key)
        _require(request_key is not None, "missing_request_key", "Child use needs request key", 400)
        now = utc_now()
        with self.sessions.begin() as db:
            row = db.scalar(
                select(Token).where(Token.token_hash == self.key_hash(token)).with_for_update()
            )
            self._check_token(row, provider, subject)
            _require(
                row.provider_client_id == provider.client_id,
                "wrong_provider",
                "Only beginning client may use this session",
                403,
            )
            _require(
                row.status == "settled"
                and row.session_status == "open"
                and (row.session_expires_at is None or row.session_expires_at > now),
                "session_not_open",
                "Session is closed or expired",
            )
            prior = db.scalar(
                select(TokenItem).where(
                    TokenItem.token_id == row.id, TokenItem.request_key == request_key
                )
            )
            if prior is not None:
                _require(
                    prior.child_service_code == child_code,
                    "request_key_conflict",
                    "Request key belongs to another child",
                )
                return {
                    "child_service_code": child_code,
                    "slot_no": prior.slot_no,
                    "status": prior.status,
                    "idempotent": True,
                }
            grant = db.scalar(
                select(TokenItem)
                .where(
                    TokenItem.token_id == row.id,
                    TokenItem.child_service_code == child_code,
                    TokenItem.slot_no == 0,
                    TokenItem.status == "available",
                )
                .with_for_update()
            )
            _require(grant is not None, "child_unavailable", "Child is not included")
            last_no = db.scalar(
                select(func.max(TokenItem.slot_no)).where(
                    TokenItem.token_id == row.id,
                    TokenItem.child_service_code == child_code,
                )
            )
            use_no = (last_no or 0) + 1
            _require(
                grant.max_uses is None or use_no <= grant.max_uses,
                "child_unavailable",
                "Child use limit reached",
            )
            db.add(
                TokenItem(
                    token_id=row.id,
                    child_service_code=child_code,
                    slot_no=use_no,
                    request_key=request_key,
                    status="used",
                    used_at=now,
                )
            )
            return {
                "child_service_code": child_code,
                "slot_no": use_no,
                "status": "used",
                "idempotent": False,
            }

    def close(self, token: str, provider: Client, subject: int) -> dict:
        now = utc_now()
        with self.sessions.begin() as db:
            row = db.scalar(
                select(Token).where(Token.token_hash == self.key_hash(token)).with_for_update()
            )
            self._check_token(row, provider, subject)
            _require(
                row.provider_client_id == provider.client_id,
                "wrong_provider",
                "Only beginning client may close this session",
                403,
            )
            _require(
                row.session_status is not None, "wrong_service_kind", "Not a composite session"
            )
            if row.session_status != "open":
                return {"session_status": row.session_status, "idempotent": True}
            row.session_status = (
                "expired"
                if row.session_expires_at is not None and row.session_expires_at <= now
                else "closed"
            )
            row.closed_at = now
            for item in db.scalars(
                select(TokenItem).where(
                    TokenItem.token_id == row.id, TokenItem.status == "available"
                )
            ).all():
                item.status = "expired"
            return {"session_status": row.session_status, "idempotent": False}

    def expire_sessions(self, limit: int = 500) -> int:
        _require(1 <= limit <= 5000, "invalid_limit", "Limit must be 1 to 5000", 400)
        now = utc_now()
        count = 0
        with self.sessions.begin() as db:
            rows = db.scalars(
                select(Token)
                .where(Token.session_status == "open", Token.session_expires_at <= now)
                .order_by(Token.session_expires_at, Token.id)
                .limit(limit)
                .with_for_update()
            ).all()
            for row in rows:
                row.session_status, row.closed_at = "expired", now
                for item in db.scalars(
                    select(TokenItem).where(
                        TokenItem.token_id == row.id, TokenItem.status == "available"
                    )
                ).all():
                    item.status = "expired"
                count += 1
        return count

    def settle(self, token: str, provider: Client, consumed_units: int | None) -> dict:
        _require(
            consumed_units is None or consumed_units >= 0,
            "invalid_units",
            "Consumed units must be nonnegative",
            400,
        )
        now = utc_now()
        with self.sessions.begin() as db:
            row = db.scalar(
                select(Token).where(Token.token_hash == self.key_hash(token)).with_for_update()
            )
            self._check_token(row, provider)
            _require(
                row.provider_client_id in (None, provider.client_id),
                "wrong_provider",
                "Only the settling provider may retry settlement",
                403,
            )
            if row.session_status is not None:
                _require(
                    consumed_units is None,
                    "wrong_mode",
                    "Durable settle does not consume units again",
                    400,
                )
                if row.session_status != "open":
                    return {
                        "status": row.status,
                        "session_status": row.session_status,
                        "idempotent": True,
                    }
                row.session_status = (
                    "expired"
                    if row.session_expires_at is not None and row.session_expires_at <= now
                    else "closed"
                )
                row.closed_at = now
                for item in db.scalars(
                    select(TokenItem).where(
                        TokenItem.token_id == row.id, TokenItem.status == "available"
                    )
                ).all():
                    item.status = "expired"
                return {
                    "status": row.status,
                    "session_status": row.session_status,
                    "idempotent": False,
                }
            _require(row.metering_mode == "reported_usage", "wrong_mode", "Quota is not metered")
            _require(consumed_units is not None, "missing_units", "Metered settle needs units", 400)
            if row.status == "settled":
                _require(
                    row.consumed_units == consumed_units,
                    "settlement_conflict",
                    "Conflicting settlement",
                )
                return {"status": "settled", "consumed_units": consumed_units, "idempotent": True}
            _require(row.status == "admitted", "not_admitted", "Token was not admitted")
            row.provider_client_id = provider.client_id
            usage = self._usage(
                db, row.tenant_id, row.subject_id, row.quota_code, row.period_start, row.period_end
            )
            usage.used_units += consumed_units
            row.consumed_units, row.status = consumed_units, "settled"
            self._ledger(db, row, "consume", consumed_units, now)
            return {"status": "settled", "consumed_units": consumed_units, "idempotent": False}

    def refund(self, token: str, provider: Client, subject: int) -> dict:
        now = utc_now()
        with self.sessions.begin() as db:
            row = db.scalar(
                select(Token).where(Token.token_hash == self.key_hash(token)).with_for_update()
            )
            self._check_token(row, provider, subject)
            _require(
                row.provider_client_id == provider.client_id,
                "wrong_provider",
                "Only redeeming provider may refund",
                403,
            )
            _require(row.metering_mode == "per_use", "wrong_mode", "Only per-use quota can refund")
            if row.status == "refunded":
                return {"status": "refunded", "idempotent": True}
            _require(row.status == "settled", "not_redeemed", "Token was not redeemed")
            if row.session_status is not None:
                used = db.scalar(
                    select(TokenItem.id)
                    .where(TokenItem.token_id == row.id, TokenItem.status == "used")
                    .limit(1)
                )
                _require(used is None, "child_already_used", "Used composite session cannot refund")
                row.session_status, row.closed_at = "closed", now
                for item in db.scalars(
                    select(TokenItem).where(
                        TokenItem.token_id == row.id, TokenItem.status == "available"
                    )
                ).all():
                    item.status = "expired"
            usage = self._usage(
                db, row.tenant_id, subject, row.quota_code, row.period_start, row.period_end
            )
            usage.used_units -= row.consumed_units
            row.status = "refunded"
            self._ledger(db, row, "refund", -row.consumed_units, now)
            return {"status": "refunded", "idempotent": False}

    @staticmethod
    def _ledger(db: Session, row: Token, event_type: str, delta: int, now: datetime) -> None:
        db.add(
            Ledger(
                token_hash=row.token_hash,
                tenant_id=row.tenant_id,
                subject_id=row.subject_id,
                service_code=row.service_code,
                quota_code=row.quota_code,
                usage_key=row.usage_key,
                unit_code=row.unit_code,
                period_start=row.period_start,
                event_type=event_type,
                delta_units=delta,
                created_at=now,
            )
        )

    @staticmethod
    def _check_token(row: Token | None, provider: Client, subject: int | None = None) -> None:
        _require(row is not None, "invalid_token", "Token not found", 404)
        _require(
            hmac.compare_digest(row.tenant_id, provider.tenant_id)
            and hmac.compare_digest(row.service_code, provider.service_code)
            and (subject is None or row.subject_id == subject),
            "scope_mismatch",
            "Token is for another subject or Service",
            403,
        )

    def balance(self, caller: Client, subject: int) -> dict:
        now = utc_now()
        with self.sessions() as db:
            quota, limit, start, end = self._policy(
                db, caller.tenant_id, subject, caller.service_code, now
            )
            usage = db.scalar(
                select(Usage).where(
                    Usage.tenant_id == caller.tenant_id,
                    Usage.subject_id == subject,
                    Usage.quota_code == quota.quota_code,
                    Usage.period_start == start,
                )
            )
            used = usage.used_units if usage else 0
            remaining = (
                None if limit.limit_mode == "unlimited" else max(0, limit.limit_value - used)
            )
            return {
                "quota_code": quota.quota_code,
                "unit": quota.unit_code,
                "limit": limit.limit_value,
                "used": used,
                "remaining": remaining,
                "period_end": end.isoformat() + "Z",
            }

    def unsettled(self, provider: Client) -> list[dict]:
        with self.sessions() as db:
            rows = db.scalars(
                select(Token).where(
                    Token.tenant_id == provider.tenant_id,
                    Token.service_code == provider.service_code,
                    (Token.provider_client_id.is_(None))
                    | (Token.provider_client_id == provider.client_id),
                    Token.status == "admitted",
                )
            ).all()
            return [
                {
                    "token_hash": row.token_hash,
                    "subject_id": row.subject_id,
                    "admitted_at": row.admitted_at.isoformat() + "Z",
                }
                for row in rows
            ]

    def token_status(self, token: str, provider: Client, subject: int) -> dict:
        with self.sessions() as db:
            row = db.scalar(select(Token).where(Token.token_hash == self.key_hash(token)))
            self._check_token(row, provider, subject)
            _require(
                row.provider_client_id in (None, provider.client_id),
                "wrong_provider",
                "Only redeeming provider may inspect redeemed token",
                403,
            )
            return {
                "status": row.status,
                "session_status": row.session_status,
                "consumed_units": row.consumed_units,
                "usage_key": row.usage_key,
            }
