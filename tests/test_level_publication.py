from sqlalchemy import select
from test_membership_sync import ADMIN, TENANT, provision, setup

from tekes_quota_kit.models import Level


def test_new_levels_are_test_until_explicitly_promoted_and_notes_preserve_state(tmp_path):
    kit, client = setup(tmp_path)
    _, contract = provision(client, "catalog-reader", "membership")
    member = {"Authorization": "Bearer " + contract["client_key"]}
    admin = ADMIN
    url = f"/v1/admin/tenants/{TENANT}/levels/VIP"
    assert client.put(url, headers=admin, json={"description": "Internal test"}).status_code == 200
    assert client.get("/v1/membership/catalog", headers=member).json()["levels"] == []
    assert client.put(url, headers=admin, json={"publication_status": "formal"}).status_code == 200
    visible = client.get("/v1/membership/catalog", headers=member).json()["levels"]
    assert [x["code"] for x in visible] == ["VIP"]
    assert visible[0]["publicationStatus"] == "formal"
    assert client.put(url, headers=admin, json={"description": "Notes updated"}).status_code == 200
    assert len(client.get("/v1/membership/catalog", headers=member).json()["levels"]) == 1
    assert client.put(url, headers=admin, json={"publication_status": "test"}).status_code == 200
    assert client.get("/v1/membership/catalog", headers=member).json()["levels"] == []
    with kit.sessions() as db:
        row = db.scalar(select(Level).where(Level.tenant_id == TENANT, Level.level_code == "VIP"))
        assert row.publication_status == "test" and row.description == "Notes updated"
    assert client.put(url, headers=admin, json={"publication_status": "public"}).status_code == 422
    assert client.put(url, json={"publication_status": "formal"}).status_code == 401
