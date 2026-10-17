from test_membership_sync import TENANT, provision, setup


def test_catalog_reads_live_rules_scoped_by_membership_client_without_mutation(tmp_path):
    kit, client = setup(tmp_path)
    _, contract = provision(client, "catalog-reader", "membership")
    headers = {"Authorization": "Bearer " + contract["client_key"]}
    kit.put_level(TENANT, "星会员", description="缴费业主免费")
    kit.put_limit(TENANT, "星会员", "door_open_count", "finite", 4, "week", "Asia/Shanghai")
    first = client.get("/v1/membership/catalog", headers=headers)
    assert first.status_code == 200
    assert first.headers["Cache-Control"] == "no-store"
    levels = {level["code"]: level for level in first.json()["levels"]}
    assert set(levels) == {"member", "星会员"}
    assert levels["星会员"]["limits"][0]["value"] == 4
    kit.put_limit(TENANT, "星会员", "door_open_count", "unlimited", None, "week", "Asia/Shanghai")
    kit.set_service_enabled(TENANT, "door_open", False)
    second = client.get("/v1/membership/catalog", headers=headers).json()
    star = next(level for level in second["levels"] if level["code"] == "星会员")
    assert star["limits"][0]["mode"] == "unlimited"
    assert second["services"] == []
    assert not any(key in second for key in ["members", "assignments", "clients", "usage"])
    assert kit.membership(TENANT, "42") is None


def test_catalog_rejects_anonymous_and_service_credentials(tmp_path):
    _, client = setup(tmp_path)
    assert client.get("/v1/membership/catalog").status_code == 401
    _, contract = provision(client, "service-reader", "consumer", "door_open")
    response = client.get(
        "/v1/membership/catalog", headers={"Authorization": "Bearer " + contract["client_key"]}
    )
    assert response.status_code == 401
