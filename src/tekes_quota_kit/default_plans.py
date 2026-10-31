"""Tenant default plans and a durable, once-per-subject initialization fact."""

import secrets
from datetime import timedelta

from sqlalchemy import func, select

from .models import (
    Assignment,
    DefaultInitialization,
    DefaultInitializationPackage,
    DefaultPlan,
    DefaultPlanRevision,
    Level,
    Limit,
)


class DefaultPlans:
    @staticmethod
    def _default_plan(db, tenant, plan_id, *, lock=False):
        from .core import _require

        query = select(DefaultPlan).where(
            DefaultPlan.tenant_id == tenant, DefaultPlan.plan_id == plan_id
        )
        row = db.scalar(query.with_for_update() if lock else query)
        _require(row is not None, "default_plan_not_found", "Default plan not found", 404)
        return row

    @staticmethod
    def _default_revision(db, plan):
        return db.scalar(
            select(DefaultPlanRevision).where(
                DefaultPlanRevision.plan_id == plan.id,
                DefaultPlanRevision.revision == plan.revision,
            )
        )

    @staticmethod
    def _default_plan_payload(db, plan, *, summary=False):
        revision = DefaultPlans._default_revision(db, plan)
        fields = ("code", "name") if summary else ("code", "name", "level_code", "validity_days")
        return dict(
            id=plan.plan_id,
            name=plan.name,
            enabled=plan.enabled,
            revision=plan.revision,
            packages=[{key: item[key] for key in fields} for item in revision.packages],
        )

    @staticmethod
    def _default_package_snapshots(db, tenant, name, enabled, packages):
        from .core import _require

        _require(
            isinstance(name, str) and 0 < len(name.strip()) <= 100,
            "invalid_default_plan",
            "Plan name must be 1 to 100 non-blank characters",
            400,
        )
        _require(type(enabled) is bool, "invalid_default_plan", "Enabled must be boolean", 400)
        _require(1 <= len(packages) <= 100, "invalid_default_plan", "Use 1 to 100 packages", 400)
        codes = set()
        result = []
        for item in packages:
            code, package_name, level = item["code"], item["name"], item["level_code"]
            days = item["validity_days"]
            _require(
                isinstance(code, str) and 0 < len(code.strip()) <= 64 and code not in codes,
                "invalid_default_package",
                "Package codes must be non-blank and unique",
                400,
            )
            _require(
                isinstance(package_name, str) and 0 < len(package_name.strip()) <= 100,
                "invalid_default_package",
                "Package name must be 1 to 100 characters",
                400,
            )
            _require(
                days is None or (type(days) is int and 1 <= days <= 3660),
                "invalid_default_package",
                "Validity must be null or 1 to 3660 days",
                400,
            )
            target = db.scalar(
                select(Level)
                .where(Level.tenant_id == tenant, Level.level_code == level)
                .with_for_update()
            )
            _require(target is not None, "unknown_level", "Level not found", 404)
            rules = list(
                db.scalars(
                    select(Limit)
                    .where(Limit.tenant_id == tenant, Limit.level_code == level)
                    .order_by(Limit.quota_code)
                    .with_for_update()
                )
            )
            _require(bool(rules), "invalid_default_package", "Level must define quota limits", 400)
            _require(
                days is not None
                or all(
                    rule.period_kind != "level_term"
                    or (rule.limit_mode == "finite" and rule.limit_value == 0)
                    for rule in rules
                ),
                "missing_term_end",
                "Level term requires a validity",
                400,
            )
            result.append(
                dict(
                    code=code,
                    name=package_name,
                    level_code=level,
                    validity_days=days,
                    limits=[
                        {
                            key: getattr(rule, key)
                            for key in (
                                "quota_code",
                                "limit_mode",
                                "limit_value",
                                "period_kind",
                                "timezone",
                            )
                        }
                        for rule in rules
                    ],
                )
            )
            codes.add(code)
        return result

    def create_default_plan(self, tenant, name, enabled, packages):
        from .core import utc_now

        with self.sessions.begin() as db:
            snapshots = self._default_package_snapshots(db, tenant, name, enabled, packages)
            now = utc_now()
            row = DefaultPlan(
                tenant_id=tenant,
                plan_id=secrets.token_urlsafe(24),
                name=name,
                enabled=enabled,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()
            db.add(
                DefaultPlanRevision(
                    plan_id=row.id,
                    revision=1,
                    name=name,
                    enabled=enabled,
                    packages=snapshots,
                    created_at=now,
                )
            )
            db.flush()
            return self._default_plan_payload(db, row)

    def update_default_plan(self, tenant, plan_id, name, enabled, packages, revision):
        from .core import _require, utc_now

        with self.sessions.begin() as db:
            row = self._default_plan(db, tenant, plan_id, lock=True)
            _require(
                row.revision == revision,
                "default_plan_changed",
                "Reload the changed plan",
                409,
            )
            snapshots = self._default_package_snapshots(db, tenant, name, enabled, packages)
            now = utc_now()
            row.name, row.enabled, row.updated_at = name, enabled, now
            row.revision += 1
            db.add(
                DefaultPlanRevision(
                    plan_id=row.id,
                    revision=row.revision,
                    name=name,
                    enabled=enabled,
                    packages=snapshots,
                    created_at=now,
                )
            )
            db.flush()
            return self._default_plan_payload(db, row)

    def get_default_plan(self, tenant, plan_id):
        with self.sessions() as db:
            return self._default_plan_payload(db, self._default_plan(db, tenant, plan_id))

    def set_default_plan_enabled(self, tenant, plan_id, enabled, revision):
        from .core import _require, utc_now

        _require(type(enabled) is bool, "invalid_default_plan", "Enabled must be boolean", 400)
        with self.sessions.begin() as db:
            row = self._default_plan(db, tenant, plan_id, lock=True)
            _require(
                row.revision == revision,
                "default_plan_changed",
                "Reload the changed plan",
                409,
            )
            previous = self._default_revision(db, row)
            now = utc_now()
            row.enabled, row.updated_at = enabled, now
            row.revision += 1
            db.add(
                DefaultPlanRevision(
                    plan_id=row.id,
                    revision=row.revision,
                    name=row.name,
                    enabled=enabled,
                    packages=previous.packages,
                    created_at=now,
                )
            )
            db.flush()
            return self._default_plan_payload(db, row)

    def list_default_plans(self, tenant, limit=100, offset=0, *, summary=False):
        from .core import _require

        _require(1 <= limit <= 100 and offset >= 0, "invalid_pagination", "Invalid page", 400)
        with self.sessions() as db:
            # Locking reads keep metadata and the referenced revision coherent on MySQL too.
            rows = list(
                db.scalars(
                    select(DefaultPlan)
                    .where(DefaultPlan.tenant_id == tenant)
                    .order_by(DefaultPlan.id)
                    .limit(limit)
                    .offset(offset)
                    .with_for_update()
                )
            )
            total = db.scalar(
                select(func.count()).select_from(DefaultPlan).where(DefaultPlan.tenant_id == tenant)
            )
            return dict(
                items=[self._default_plan_payload(db, row, summary=summary) for row in rows],
                total=total,
                limit=limit,
                offset=offset,
            )

    @staticmethod
    def _initialization_payload(db, fact, replayed):
        package_ids = list(
            db.scalars(
                select(DefaultInitializationPackage.assignment_id)
                .where(DefaultInitializationPackage.initialization_id == fact.id)
                .order_by(DefaultInitializationPackage.sort_order)
            )
        )
        return dict(
            subject_id=fact.subject_id,
            initialization=dict(
                id=fact.initialization_id,
                default_plan_id=fact.default_plan_id,
                plan_revision=fact.plan_revision,
                status="applied",
                applied_at=fact.applied_at.isoformat() + "Z",
                package_ids=[str(value) for value in package_ids],
            ),
            replayed=replayed,
        )

    def initialize_defaults(
        self, tenant, subject, plan_id, source, package_codes: list[str] | None = None
    ):
        from .core import _require, _subject, utc_now

        subject = _subject(subject)
        with self.sessions.begin() as db:
            state = self._lock_subject(db, tenant, subject)
            fact = db.scalar(
                select(DefaultInitialization)
                .where(
                    DefaultInitialization.tenant_id == tenant,
                    DefaultInitialization.subject_id == subject,
                )
                .with_for_update()
            )
            if fact is not None:
                return self._initialization_payload(db, fact, True)
            plan = self._default_plan(db, tenant, plan_id, lock=True)
            _require(plan.enabled, "default_plan_disabled", "Default plan is disabled", 409)
            revision = self._default_revision(db, plan)
            packages = revision.packages
            if package_codes is not None:
                selected = set(package_codes)
                _require(
                    bool(selected)
                    and len(selected) == len(package_codes)
                    and selected <= {item["code"] for item in packages},
                    "invalid_package_selection",
                    "Selected packages must be unique members of the plan",
                    400,
                )
                packages = [item for item in packages if item["code"] in selected]
            now = utc_now()
            fact = DefaultInitialization(
                initialization_id=secrets.token_urlsafe(24),
                tenant_id=tenant,
                subject_id=subject,
                plan_revision_id=revision.id,
                default_plan_id=plan.plan_id,
                plan_revision=plan.revision,
                source=source,
                applied_at=now,
            )
            db.add(fact)
            db.flush()
            existing = self._user_packages(db, tenant, subject)
            order = max((row.sort_order for row in existing), default=-1) + 1
            for position, item in enumerate(packages):
                end = now + timedelta(days=item["validity_days"]) if item["validity_days"] else None
                assignment = Assignment(
                    tenant_id=tenant,
                    subject_id=subject,
                    level_code=item["level_code"],
                    package_code=secrets.token_hex(16),
                    sort_order=order + position,
                    effective_at=now,
                    expires_at=end,
                    term_start=now,
                    term_end=end,
                )
                db.add(assignment)
                db.flush()
                db.add(
                    DefaultInitializationPackage(
                        initialization_id=fact.id,
                        assignment_id=assignment.id,
                        sort_order=position,
                        code=item["code"],
                        name=item["name"],
                        limits=item["limits"],
                    )
                )
            state.revision += 1
            db.flush()
            return self._initialization_payload(db, fact, False)
