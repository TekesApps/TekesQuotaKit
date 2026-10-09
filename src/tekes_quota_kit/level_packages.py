"""Ordered user Level packages. Package identity survives every priority change."""

from __future__ import annotations

import secrets

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import aliased

from .models import (
    Assignment,
    LevelPackageAdmission,
    LevelPackageUsage,
    Limit,
    SubjectPackages,
    Usage,
)


class LevelPackages:
    @staticmethod
    def _lock_subject(db, tenant, subject):
        values = dict(tenant_id=tenant, subject_id=subject, revision=0)
        if db.bind.dialect.name == "mysql":
            stmt = mysql_insert(SubjectPackages).values(**values)
            db.execute(stmt.on_duplicate_key_update(id=SubjectPackages.id))
        else:
            stmt = sqlite_insert(SubjectPackages).values(**values)
            db.execute(stmt.on_conflict_do_nothing(index_elements=["tenant_id", "subject_id"]))
        return db.scalar(
            select(SubjectPackages)
            .where(SubjectPackages.tenant_id == tenant, SubjectPackages.subject_id == subject)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _assignments(db, tenant, subject, now, *, lock=False):
        query = (
            select(Assignment)
            .where(
                Assignment.tenant_id == tenant,
                Assignment.subject_id == subject,
                Assignment.revoked_at.is_(None),
                Assignment.effective_at <= now,
                Assignment.expires_at.is_(None) | (Assignment.expires_at > now),
            )
            .order_by(Assignment.sort_order, Assignment.id)
        )
        return list(db.scalars(query.with_for_update() if lock else query))

    @staticmethod
    def _package_usage(db, assignment, quota, start, end, *, lock=False):
        if assignment.package_code == "membership":
            from .core import QuotaKit

            if lock:
                return QuotaKit._usage(
                    db, assignment.tenant_id, assignment.subject_id, quota, start, end
                )
            return db.scalar(
                select(Usage).where(
                    Usage.tenant_id == assignment.tenant_id,
                    Usage.subject_id == assignment.subject_id,
                    Usage.quota_code == quota,
                    Usage.period_start == start,
                )
            )
        if lock:
            values = dict(
                assignment_id=assignment.id,
                quota_code=quota,
                period_start=start,
                period_end=end,
                used_units=0,
            )
            if db.bind.dialect.name == "mysql":
                stmt = mysql_insert(LevelPackageUsage).values(**values)
                db.execute(stmt.on_duplicate_key_update(id=LevelPackageUsage.id))
            else:
                stmt = sqlite_insert(LevelPackageUsage).values(**values)
                db.execute(
                    stmt.on_conflict_do_nothing(
                        index_elements=["assignment_id", "quota_code", "period_start"]
                    )
                )
        query = select(LevelPackageUsage).where(
            LevelPackageUsage.assignment_id == assignment.id,
            LevelPackageUsage.quota_code == quota,
            LevelPackageUsage.period_start == start,
        )
        return db.scalar(query.with_for_update() if lock else query)

    def _level_sources(self, db, tenant, subject, quota, now, *, lock=False, token=None):
        from .core import _period

        snapshots = []
        if token is not None:
            snapshots = list(
                db.scalars(
                    select(LevelPackageAdmission)
                    .where(LevelPackageAdmission.token_id == token.id)
                    .order_by(LevelPackageAdmission.sort_order)
                )
            )
        if snapshots:
            definitions = [
                (
                    db.get(Assignment, snap.assignment_id),
                    snap.limit_value,
                    snap.period_start,
                    snap.period_end,
                )
                for snap in snapshots
            ]
        else:
            definitions = []
            for assignment in self._assignments(db, tenant, subject, now, lock=lock):
                limit = db.scalar(
                    select(Limit).where(
                        Limit.tenant_id == tenant,
                        Limit.level_code == assignment.level_code,
                        Limit.quota_code == quota,
                    )
                )
                if limit is not None:
                    start, end = _period(now, limit.period_kind, limit.timezone, assignment)
                    definitions.append((assignment, limit.limit_value, start, end))
        return [
            (
                assignment,
                value,
                start,
                end,
                self._package_usage(db, assignment, quota, start, end, lock=lock),
            )
            for assignment, value, start, end in definitions
        ]

    def _user_packages(self, db, tenant, subject):
        """Keep the latest membership row, plus unrevoked independently issued packages."""
        rows = list(
            db.scalars(
                select(Assignment)
                .where(
                    Assignment.tenant_id == tenant,
                    Assignment.subject_id == subject,
                )
                .order_by(Assignment.id.desc())
            )
        )
        membership = next((r for r in rows if r.package_code == "membership"), None)
        return sorted(
            [
                r
                for r in rows
                if r.revoked_at is None and (r.package_code != "membership" or r is membership)
            ],
            key=lambda r: (r.sort_order, r.id),
        )

    def _user_packages_payload(self, db, tenant, subject):
        from .core import QuotaError, _period, utc_now

        stamp = lambda value: value.isoformat() + "Z" if value else None  # noqa: E731
        state = db.scalar(
            select(SubjectPackages).where(
                SubjectPackages.tenant_id == tenant,
                SubjectPackages.subject_id == subject,
            )
        )
        now = utc_now()
        items = []
        for assignment in self._user_packages(db, tenant, subject):
            quotas = []
            for limit in db.scalars(
                select(Limit)
                .where(
                    Limit.tenant_id == tenant,
                    Limit.level_code == assignment.level_code,
                )
                .order_by(Limit.quota_code)
            ):
                item = dict(
                    quota_code=limit.quota_code,
                    limit=limit.limit_value,
                    period_kind=limit.period_kind,
                    timezone=limit.timezone,
                )
                try:
                    start, end = _period(now, limit.period_kind, limit.timezone, assignment)
                    usage = self._package_usage(db, assignment, limit.quota_code, start, end)
                    used = usage.used_units if usage else 0
                    item.update(
                        used=used,
                        remaining=None
                        if limit.limit_value is None
                        else max(0, limit.limit_value - used),
                        period_start=stamp(start),
                        period_end=stamp(end),
                    )
                except QuotaError as exc:
                    item["error"] = exc.code
                quotas.append(item)
            items.append(
                dict(
                    id=assignment.id,
                    package_code=assignment.package_code,
                    level_code=assignment.level_code,
                    sort_order=assignment.sort_order,
                    effective_at=stamp(assignment.effective_at),
                    expires_at=stamp(assignment.expires_at),
                    term_start=stamp(assignment.term_start),
                    term_end=stamp(assignment.term_end),
                    status="pending"
                    if assignment.effective_at > now
                    else "expired"
                    if assignment.expires_at and assignment.expires_at <= now
                    else "active",
                    quotas=quotas,
                )
            )
        return dict(
            subject_id=subject,
            subject_key=str(subject),
            revision=state.revision if state else 0,
            packages=items,
        )

    def user_packages(self, tenant, subject):
        from .core import _require, _valid_subject

        _require(_valid_subject(subject), "invalid_subject", "Invalid subject", 400)
        with self.sessions() as db:
            return self._user_packages_payload(db, tenant, subject)

    def save_user_packages(self, tenant, subject, packages, revision):
        from .core import _period, _require, _valid_subject, utc_now

        _require(_valid_subject(subject), "invalid_subject", "Invalid subject", 400)
        now = utc_now()
        with self.sessions.begin() as db:
            state = self._lock_subject(db, tenant, subject)
            _require(
                state.revision == revision,
                "packages_changed",
                "User packages changed; reload before saving",
            )
            current = {r.id: r for r in self._user_packages(db, tenant, subject)}
            keep = set()
            for order, item in enumerate(packages):
                level = item["level_code"]
                _require(
                    self._level(db, tenant, level) is not None,
                    "unknown_level",
                    "Level not found",
                    404,
                )
                row_id = item.get("id")
                if row_id is not None:
                    _require(
                        row_id in current and row_id not in keep,
                        "invalid_package",
                        "Unknown or repeated user package",
                        400,
                    )
                    row = current[row_id]
                    _require(
                        row.level_code == level,
                        "package_level_immutable",
                        "Remove and issue another package to change its Level",
                        400,
                    )
                    # Issued package dates are retained; this editor only changes list order.
                    _require(
                        item.get("effective_at") in (None, row.effective_at),
                        "package_term_immutable",
                        "Issue another package to renew its term",
                        400,
                    )
                    end = item.get("expires_at")
                    _require(
                        end == row.expires_at,
                        "package_term_immutable",
                        "Issued package expiry is read-only",
                        400,
                    )
                    keep.add(row_id)
                else:
                    start = item.get("effective_at") or now
                    end = item.get("expires_at")
                    _require(
                        end is None or end > start,
                        "invalid_expiry",
                        "Expiry must follow start",
                        400,
                    )
                    row = Assignment(
                        tenant_id=tenant,
                        subject_id=subject,
                        package_code=secrets.token_hex(16),
                        level_code=level,
                        effective_at=start,
                        expires_at=end,
                        term_start=start,
                        term_end=end,
                    )
                    for limit in db.scalars(
                        select(Limit).where(
                            Limit.tenant_id == tenant,
                            Limit.level_code == level,
                        )
                    ):
                        _period(start, limit.period_kind, limit.timezone, row)
                    db.add(row)
                row.sort_order = order
            for row_id, row in current.items():
                if row_id not in keep:
                    row.revoked_at = now
            if not any(r.package_code == "membership" and r.id in keep for r in current.values()):
                for row in db.scalars(
                    select(Assignment).where(
                        Assignment.tenant_id == tenant,
                        Assignment.subject_id == subject,
                        Assignment.package_code == "membership",
                        Assignment.revoked_at.is_(None),
                    )
                ):
                    row.revoked_at = now
            state.revision += 1
            db.flush()
            return self._user_packages_payload(db, tenant, subject)

    def list_package_subjects(self, tenant, limit=10, offset=0, subject=None, level=None):
        with self.sessions() as db:
            query = select(Assignment.subject_id).where(Assignment.tenant_id == tenant)
            if subject is not None:
                query = query.where(Assignment.subject_id == subject)
            if level is not None:
                history = aliased(Assignment)
                latest = (
                    select(func.max(history.id))
                    .where(
                        history.tenant_id == Assignment.tenant_id,
                        history.subject_id == Assignment.subject_id,
                        history.package_code == "membership",
                    )
                    .scalar_subquery()
                )
                query = query.where(
                    Assignment.level_code == level,
                    Assignment.revoked_at.is_(None),
                    (Assignment.package_code != "membership") | (Assignment.id == latest),
                )
            query = query.distinct()
            total = db.scalar(select(func.count()).select_from(query.subquery()))
            ids = db.scalars(
                query.order_by(Assignment.subject_id.desc()).offset(offset).limit(limit)
            )
            return dict(total=total, rows=[self._user_packages_payload(db, tenant, s) for s in ids])
