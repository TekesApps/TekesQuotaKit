"""Synthetic localhost HTTP fixture. Never supplies real users or production credentials.

Run with the project's Python environment; see docs/default-plans-integration.md.
"""

import argparse
import json
import os
import secrets
from pathlib import Path

import uvicorn
from sqlalchemy.engine import make_url

from tekes_quota_kit.api import create_app
from tekes_quota_kit.core import QuotaKit, utc_now
from tekes_quota_kit.models import Base, Tenant


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "serve"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=19463)
    parser.add_argument(
        "--database-url", help="Optional disposable MySQL tekes_quota_test_* schema"
    )
    args = parser.parse_args()
    directory = args.directory.resolve()
    manifest = directory / "fixture.json"
    if args.command == "init":
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if manifest.exists():
            parser.error("Fixture already exists; reuse serve or select a fresh directory")
        url = args.database_url or f"sqlite:///{directory / 'quota.sqlite3'}"
        parsed = make_url(url)
        if args.database_url and not (
            parsed.drivername == "mysql+pymysql"
            and parsed.database
            and parsed.database.startswith("tekes_quota_test_")
        ):
            parser.error("Only a disposable MySQL tekes_quota_test_* schema may be supplied")
        config = {
            "database_url": url,
            "base_url": f"http://127.0.0.1:{args.port}",
            "port": args.port,
            "tenant_id": "skzy-default-plan-fixture",
            "token_secret": secrets.token_urlsafe(48),
            "admin_key": secrets.token_urlsafe(48),
            "catalog_key": secrets.token_urlsafe(48),
            "initialization_key": secrets.token_urlsafe(48),
            "consumer_key": secrets.token_urlsafe(48),
        }
        kit = QuotaKit(url, config["token_secret"])
        Base.metadata.create_all(kit.engine)
        tenant = config["tenant_id"]
        with kit.sessions.begin() as db:
            db.add(
                Tenant(
                    tenant_id=tenant,
                    name="Synthetic SKZY integration",
                    subject_id_definition="Synthetic resident ID; never real users",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
        kit.put_quota(tenant, "visits", "use", "per_use")
        kit.put_service(tenant, "fixture_visit", "visits")
        for level, amount in (("fixture_vip", 20), ("fixture_bonus", 3), ("fixture_paid", 1)):
            kit.put_level(tenant, level, publication_status="formal")
            kit.put_limit(tenant, level, "visits", "finite", amount, "level_term", "UTC")
        kit.put_quota(tenant, "restricted", "use", "per_use")
        kit.put_service(tenant, "fixture_restricted", "restricted")
        kit.put_level(tenant, "fixture_star", publication_status="formal")
        kit.put_limit(tenant, "fixture_star", "visits", "finite", 4, "month", "UTC")
        kit.put_limit(tenant, "fixture_star", "restricted", "finite", 0, "level_term", "UTC")
        kit.put_limit(tenant, "fixture_vip", "restricted", "unlimited", None, "level_term", "UTC")
        for role, key_field in (
            ("default_catalog", "catalog_key"),
            ("default_init", "initialization_key"),
            ("consumer", "consumer_key"),
        ):
            kit.put_client(f"fixture-{role}", config[key_field], tenant, "fixture_visit", role)
        vip = dict(code="vip-week", name="VIP 七天", level_code="fixture_vip", validity_days=7)
        config["vip_week_plan_id"] = kit.create_default_plan(
            tenant, "Synthetic VIP seven days", True, [vip]
        )["id"]
        config["multi_plan_id"] = kit.create_default_plan(
            tenant,
            "Synthetic two packages",
            True,
            [
                vip,
                dict(
                    code="bonus",
                    name="Bonus",
                    level_code="fixture_bonus",
                    validity_days=7,
                ),
            ],
        )["id"]
        config["disabled_plan_id"] = kit.create_default_plan(
            tenant, "Synthetic disabled", False, [vip]
        )["id"]
        config["vip_package_code"] = vip["code"]
        config["star_package_code"] = "star-base"
        config["star_vip_plan_id"] = kit.create_default_plan(
            tenant,
            "Synthetic VIP seven days plus permanent base",
            True,
            [
                vip,
                dict(
                    code="star-base",
                    name="Permanent base",
                    level_code="fixture_star",
                    validity_days=None,
                ),
            ],
        )["id"]
        with open(
            manifest, "x", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)
        ) as output:
            json.dump(config, output, indent=2)
        print(f"Synthetic fixture ready. Private manifest: {manifest}")
        return
    config = json.loads(manifest.read_text())
    kit = QuotaKit(config["database_url"], config["token_secret"])
    uvicorn.run(create_app(kit, config["admin_key"]), host="127.0.0.1", port=config["port"])


if __name__ == "__main__":
    main()
