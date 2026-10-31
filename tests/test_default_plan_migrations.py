"""The default-plan SQLite migration adds tables without rewriting existing quota data."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, select

from tekes_quota_kit.models import Assignment, Base, Level

ROOT = Path(__file__).resolve().parents[1]
NEW_TABLES = {
    "tq_default_plans",
    "tq_default_plan_revisions",
    "tq_default_initializations",
    "tq_default_initialization_packages",
}


def test_sqlite_upgrade_preserves_existing_assignments_and_is_repeatable(tmp_path):
    path = tmp_path / "existing-quota.sqlite3"
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    old_tables = [table for name, table in Base.metadata.tables.items() if name not in NEW_TABLES]
    Base.metadata.create_all(engine, tables=old_tables)
    with engine.begin() as db:
        db.execute(
            Level.__table__.insert().values(
                tenant_id="old-tenant", level_code="member", publication_status="formal"
            )
        )
        db.execute(
            Assignment.__table__.insert().values(
                tenant_id="old-tenant",
                subject_id="User_42",
                level_code="member",
                effective_at=datetime(2026, 1, 1),
                package_code="membership",
                sort_order=0,
            )
        )

    migration = (ROOT / "migrations" / "add_default_plans_sqlite.sql").read_text()
    with engine.begin() as db:
        db.connection.driver_connection.executescript(migration)
        db.connection.driver_connection.executescript(migration)

    with engine.connect() as db:
        assignment = db.scalar(select(Assignment).where(Assignment.subject_id == "User_42"))
        assert assignment is not None
        assert (
            db.scalar(select(Assignment.level_code).where(Assignment.subject_id == "User_42"))
            == "member"
        )
        assert NEW_TABLES <= set(inspect(db).get_table_names())
        for table in NEW_TABLES:
            assert inspect(db).get_pk_constraint(table)["constrained_columns"] == ["id"]

    engine.dispose()


def test_mysql_incremental_and_fresh_ddl_include_same_default_plan_tables():
    incremental = (ROOT / "migrations" / "add_default_plans_mysql.sql").read_text()
    fresh = (ROOT / "migrations" / "create_tables_mysql.sql").read_text()
    for table in NEW_TABLES:
        statement = f"CREATE TABLE {table} ("
        assert incremental.count(statement) == 1
        assert fresh.count(statement) == 1
    assert "CHARACTER SET ascii COLLATE ascii_bin" in incremental
    assert "source JSON NOT NULL" in incremental
    assert "packages JSON NOT NULL" in incremental
    assert "limits JSON NOT NULL" in incremental
    assert "FOREIGN KEY(assignment_id) REFERENCES tq_assignments (id)" in incremental
    assert "ALTER TABLE tq_assignments" not in incremental
