"""Exactly-once membership assignment from an order verified by its business backend."""

from datetime import timedelta

from sqlalchemy import select

from .models import Assignment, MembershipPurchase


class MembershipPurchases:
    def membership_purchase(self, tenant, subject, order, level, expected, days, *, revoke=False):
        from .core import _period, _require, _subject, utc_now
        from .models import Limit

        subject = _subject(subject)
        with self.sessions.begin() as db:
            state = self._lock_subject(db, tenant, subject)
            # Evaluate the latest membership after waiting for competing subject writes.
            now = utc_now()
            receipt = db.scalar(
                select(MembershipPurchase)
                .where(
                    MembershipPurchase.tenant_id == tenant,
                    MembershipPurchase.order_code == order,
                )
                .with_for_update()
            )
            if receipt:
                _require(
                    (
                        receipt.subject_id,
                        receipt.level_code,
                        receipt.expected_level,
                        receipt.period_days,
                    )
                    == (subject, level, expected, days),
                    "purchase_conflict",
                    "Order identity or membership parameters differ",
                )
                if revoke:
                    if receipt.revoked_at is None:
                        receipt.revoked_at = now
                        if receipt.assignment_id:
                            db.get(Assignment, receipt.assignment_id).revoked_at = now
                        state.revision += 1
                else:
                    _require(receipt.revoked_at is None, "purchase_revoked", "Order was refunded")
                return self._purchase_receipt(db, receipt)
            _require(
                self._level(db, tenant, level) is not None, "unknown_level", "Level not found", 404
            )
            # A refund tombstone must be retained even when delivery has not arrived yet.
            # It prevents a delayed or concurrent payment notification from granting later.
            receipt = MembershipPurchase(
                tenant_id=tenant,
                subject_id=subject,
                order_code=order,
                level_code=level,
                expected_level=expected,
                period_days=days,
                created_at=now,
                revoked_at=now if revoke else None,
            )
            if not revoke:
                current = self._assignment(db, tenant, subject, now, lock=True, required=False)
                _require(
                    current is None or current.level_code == expected,
                    "membership_changed",
                    "Current membership changed after checkout",
                )
                _require(
                    self._level(db, tenant, expected) is not None,
                    "unknown_level",
                    "Previous Level not found",
                    404,
                )
                _require(level != expected, "same_level_purchase", "Use a higher membership Level")
                if current is None:
                    # The caller explicitly supplies its default Level. No hard-coded tenant policy.
                    current = Assignment(
                        tenant_id=tenant,
                        subject_id=subject,
                        level_code=expected,
                        package_code="membership",
                        sort_order=0,
                        effective_at=now,
                        expires_at=None,
                        term_start=now,
                        term_end=None,
                    )
                    db.add(current)
                end = now + timedelta(days=days)
                assignment = Assignment(
                    tenant_id=tenant,
                    subject_id=subject,
                    level_code=level,
                    package_code="membership",
                    sort_order=current.sort_order - 1,
                    effective_at=now,
                    expires_at=end,
                    term_start=now,
                    term_end=end,
                )
                for rule in db.scalars(
                    select(Limit).where(Limit.tenant_id == tenant, Limit.level_code == level)
                ):
                    _period(now, rule.period_kind, rule.timezone, assignment)
                db.add(assignment)
                db.flush()
                receipt.assignment_id = assignment.id
            db.add(receipt)
            state.revision += 1
            db.flush()
            return self._purchase_receipt(db, receipt)

    @staticmethod
    def _purchase_receipt(db, receipt):
        assignment = db.get(Assignment, receipt.assignment_id) if receipt.assignment_id else None
        return {
            "order_code": receipt.order_code,
            "subject_id": receipt.subject_id,
            "level_code": receipt.level_code,
            "status": "revoked" if receipt.revoked_at else "granted",
            "assignment_id": receipt.assignment_id,
            "effective_at": assignment.effective_at.isoformat() + "Z" if assignment else None,
            "expires_at": assignment.expires_at.isoformat() + "Z" if assignment else None,
        }
