from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


ID_TYPE = BigInteger().with_variant(Integer, "sqlite")
SUBJECT_ID_TYPE = BigInteger().with_variant(Integer, "sqlite")
UTC_DATETIME = MySQLDateTime(fsp=6).with_variant(DateTime(), "sqlite")


class Client(Base):
    __tablename__ = "tq_clients"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    service_code: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class Level(Base):
    __tablename__ = "tq_levels"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    level_code: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "level_code", name="uq_tq_level_code"),)


class Quota(Base):
    __tablename__ = "tq_quotas"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_code: Mapped[str] = mapped_column(String(64), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(32), nullable=False)
    metering_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "quota_code", name="uq_tq_quota_code"),)


class Service(Base):
    __tablename__ = "tq_services"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    service_code: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_code: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "service_code", name="uq_tq_service_code"),
        ForeignKeyConstraint(
            ["tenant_id", "quota_code"], ["tq_quotas.tenant_id", "tq_quotas.quota_code"]
        ),
    )


class Limit(Base):
    __tablename__ = "tq_limits"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    level_code: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_code: Mapped[str] = mapped_column(String(64), nullable=False)
    limit_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    limit_value: Mapped[int | None] = mapped_column(BigInteger)
    period_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "level_code", "quota_code", name="uq_tq_limit_code"),
        ForeignKeyConstraint(
            ["tenant_id", "level_code"], ["tq_levels.tenant_id", "tq_levels.level_code"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "quota_code"], ["tq_quotas.tenant_id", "tq_quotas.quota_code"]
        ),
    )


class Assignment(Base):
    __tablename__ = "tq_assignments"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[int] = mapped_column(SUBJECT_ID_TYPE, nullable=False)
    level_code: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    __table_args__ = (
        Index("ix_tq_assignment_subject", "tenant_id", "subject_id", "effective_at"),
        ForeignKeyConstraint(
            ["tenant_id", "level_code"], ["tq_levels.tenant_id", "tq_levels.level_code"]
        ),
    )


class Usage(Base):
    __tablename__ = "tq_usage"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[int] = mapped_column(SUBJECT_ID_TYPE, nullable=False)
    quota_code: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False)
    period_end: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False)
    used_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "subject_id", "quota_code", "period_start", name="uq_tq_usage_period"
        ),
    )


class Token(Base):
    __tablename__ = "tq_tokens"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[int] = mapped_column(SUBJECT_ID_TYPE, nullable=False)
    service_code: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_code: Mapped[str] = mapped_column(String(64), nullable=False)
    usage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    issuer_client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_client_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    admitted_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    period_end: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    unit_code: Mapped[str | None] = mapped_column(String(32))
    metering_mode: Mapped[str | None] = mapped_column(String(16))
    limit_value: Mapped[int | None] = mapped_column(BigInteger)
    consumed_units: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "issuer_client_id",
            "subject_id",
            "service_code",
            "request_key",
            name="uq_tq_token_request",
        ),
        Index("ix_tq_unsettled", "status", "admitted_at"),
    )


class Ledger(Base):
    __tablename__ = "tq_ledger"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[int] = mapped_column(SUBJECT_ID_TYPE, nullable=False)
    service_code: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_code: Mapped[str] = mapped_column(String(64), nullable=False)
    usage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    delta_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False)
    __table_args__ = (
        UniqueConstraint("token_hash", "event_type", name="uq_tq_ledger_token_event"),
        Index("ix_tq_ledger_subject", "tenant_id", "subject_id", "quota_code", "period_start"),
    )
