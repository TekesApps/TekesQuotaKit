from test_quota_flow import ADMIN_KEY, configured, redeem
from test_service_enabled import MEMBER_KEY, TENANT


def test_incoming_visible_but_not_callable_and_legacy_save_preserves_it(tmp_path):
    kit, client, service_id = configured(tmp_path, mode="per_use", limit=4)
    kit.put_client("members", MEMBER_KEY, TENANT, "*", "membership")
    url = f"/v1/admin/tenants/{TENANT}/services/example.run/status"
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    members = {"Authorization": f"Bearer {MEMBER_KEY}"}
    assert client.patch(url, headers=admin, json={"status": "incoming"}).json()["enabled"] is False
    assert redeem(client, service_id, "future").json()["code"] == "service_incoming"
    kit.put_service(TENANT, "example.run", "example", description="Coming soon")
    catalog = client.get("/v1/membership/catalog", headers=members).json()
    assert catalog["services"][0]["status"] == "incoming"
    usage = client.get("/v1/members/42/usage", headers=members).json()["quotas"][0]
    assert usage["services"] == []
    assert usage["service_balances"] == []
    assert usage["service_details"][0]["status"] == "incoming"
    assert client.patch(url, headers=admin, json={"status": "disabled"}).status_code == 200
    assert client.get("/v1/membership/catalog", headers=members).json()["services"] == []
    assert (
        client.get("/v1/members/42/usage", headers=members).json()["quotas"][0]["service_details"]
        == []
    )
    assert client.patch(url, headers=admin, json={"status": "enabled"}).json()["enabled"] is True
    assert redeem(client, service_id, "future").status_code == 200


def test_state_validation_and_legacy_boolean_compatibility(tmp_path):
    _, client, _ = configured(tmp_path, mode="per_use", limit=4)
    url = f"/v1/admin/tenants/{TENANT}/services/example.run/status"
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    for body in [
        {},
        {"status": "unknown"},
        {"status": "incoming", "enabled": True},
        {"enabled": "false"},
    ]:
        assert client.patch(url, headers=admin, json=body).status_code == 422
    assert client.patch(url, json={"status": "enabled"}).status_code == 401
    assert client.patch(url, headers=admin, json={"enabled": False}).json()["status"] == "disabled"


def test_incoming_still_allows_existing_usage_settlement(tmp_path):
    from test_quota_flow import issue, provider_headers

    kit, client, service_id = configured(tmp_path, mode="reported_usage", limit=100)
    token = issue(client, service_id, "started-before-incoming")
    kit.set_service_status(TENANT, "example.run", "incoming")
    response = client.post(
        "/v1/token/settle", headers=provider_headers(), json={"token": token, "consumed_units": 10}
    )
    assert response.status_code == 200
    kit.set_service_status(TENANT, "example.run", "enabled")
    from test_quota_flow import ISSUER_KEY

    assert kit.balance(kit.client(ISSUER_KEY, "issuer"), 42)["used"] == 10


def test_migration_preserves_existing_switches_without_incoming_promises():
    import sqlite3
    from pathlib import Path

    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE tq_services(id INTEGER, enabled BOOL); INSERT INTO tq_services VALUES (1,1),(2,0);"
    )
    db.executescript(
        (Path(__file__).parents[1] / "migrations/add_service_status_mysql.sql").read_text()
    )
    assert db.execute("SELECT enabled,status FROM tq_services ORDER BY id").fetchall() == [
        (1, "enabled"),
        (0, "disabled"),
    ]
