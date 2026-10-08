"""Guards against docs and DDL falling behind the models when tables are added."""

from __future__ import annotations

import re
from pathlib import Path

from tekes_quota_kit.models import Base

ROOT = Path(__file__).resolve().parents[1]
MODEL_TABLES = set(Base.metadata.tables)


def test_fresh_install_ddl_creates_exactly_the_model_tables():
    ddl = (ROOT / "migrations" / "create_tables_mysql.sql").read_text(encoding="utf-8")
    assert set(re.findall(r"CREATE TABLE (\w+)", ddl)) == MODEL_TABLES


def test_backup_commands_do_not_hard_code_table_names():
    guide = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    section = guide.split("### Backups", 1)[1].split("\n### ", 1)[0]
    commands = "\n".join(re.findall(r"```sh\n(.*?)```", section, re.S))
    assert "mysqldump" in commands
    # A fixed list silently misses tables added later (0.3.0 and 0.4.0 both added some).
    assert re.findall(r"\btq_[a-z_]+", commands) == []
