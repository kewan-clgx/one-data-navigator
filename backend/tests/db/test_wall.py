"""Insight Wall against the real Postgres: who sees which wall, votes, the public wall and organization switches."""

import psycopg
import pytest
from fastapi.testclient import TestClient

from odn.api.main import app
from odn.config import settings
from tests.db.test_rls import OWNER_URL, _reachable
from tests.db.test_shares import THREAD, add_table, env  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")


def share(client: TestClient, audience: str = "organization", wall: str = "organization") -> dict:
    resp = client.post("/v1/shares", json={"thread_id": THREAD, "audience": audience, "wall": wall})
    assert resp.status_code == 201, resp.text
    return resp.json()


def ids(cards: list[dict]) -> list[str]:
    return [c["id"] for c in cards]


def test_organization_wall_lists_posted_links_to_members_only_with_votes(env) -> None:  # noqa: F811
    tenant, user, as_, _copies = env
    slug, other = tenant(), tenant()
    ann, ben, outsider = user(slug, "ann"), user(slug, "ben"), user(other, "cat")
    boss = user(slug, "boss", role="admin")
    add_table(ann)

    posted = share(as_(ann))
    share(as_(ann), wall="none")  # a link that isn't posted
    assert as_(ann).post("/v1/shares", json={"thread_id": THREAD, "audience": "people", "wall": "organization",
                                             "recipient_ids": [str(ben.user_id)]}).status_code == 422

    seen = as_(ben).get("/v1/wall").json()
    assert seen["enabled"] is True and ids(seen["items"]) == [posted["id"]]
    card = seen["items"][0]
    assert card["title"] == "Biz by state" and card["question"] == "How many businesses per state?"
    assert card["answer"] == "CA has 5." and card["votes"] == 0 and card["is_owner"] is False
    assert as_(outsider).get("/v1/wall").json()["items"] == []  # other organization
    assert as_(outsider).post(f"/v1/wall/{posted['id']}/vote").status_code == 404

    assert as_(ann).post(f"/v1/wall/{posted['id']}/vote").status_code == 422  # not your own
    assert as_(ben).post(f"/v1/wall/{posted['id']}/vote").json() == {"votes": 1, "my_vote": True}
    assert as_(ben).post(f"/v1/wall/{posted['id']}/vote").json()["votes"] == 1  # once per person
    assert as_(ben).get("/v1/wall", params={"sort": "top"}).json()["items"][0]["my_vote"] is True
    assert as_(ben).delete(f"/v1/wall/{posted['id']}/vote").json() == {"votes": 0, "my_vote": False}
    assert ids(as_(ben).get("/v1/wall", params={"q": "biz"}).json()["items"]) == [posted["id"]]
    assert as_(ben).get("/v1/wall", params={"q": "100%_"}).json()["items"] == []  # wildcards are literal

    assert as_(ben).post(f"/v1/wall/{posted['id']}/feature", json={"featured": True}).status_code == 403
    assert as_(boss).post(f"/v1/wall/{posted['id']}/feature", json={"featured": True}).json() == {"featured": True}
    assert as_(ben).get("/v1/wall").json()["items"][0]["featured"] is True

    # Only the owner posts or moves; an admin may take it down.
    assert as_(ben).put(f"/v1/shares/{posted['id']}/wall", json={"wall": "none"}).status_code == 404
    assert as_(boss).put(f"/v1/shares/{posted['id']}/wall", json={"wall": "organization"}).status_code == 404
    assert as_(boss).put(f"/v1/shares/{posted['id']}/wall", json={"wall": "none"}).json() == {"wall": "none"}
    assert as_(ben).get("/v1/wall").json()["items"] == []
    assert as_(ann).put(f"/v1/shares/{posted['id']}/wall", json={"wall": "organization"}).status_code == 200
    item = as_(ben).get("/v1/wall").json()["items"][0]
    assert item["featured"] is False  # taking it down cleared the pin

    assert as_(ann).delete(f"/v1/shares/{posted['id']}").status_code == 204  # revoked: off the wall
    assert as_(ben).get("/v1/wall").json()["items"] == []
    assert as_(ann).put(f"/v1/shares/{posted['id']}/wall", json={"wall": "organization"}).status_code == 422

    again = share(as_(ann))
    as_(boss).put("/v1/admin/sharing", json={"enabled": True, "org_wall": False})
    off = as_(ben).get("/v1/wall").json()
    assert off["enabled"] is False and off["items"] == []
    assert as_(ann).post("/v1/shares", json={"thread_id": THREAD, "audience": "organization",
                                             "wall": "organization"}).status_code == 403
    assert as_(ann).put(f"/v1/shares/{again['id']}/wall", json={"wall": "organization"}).status_code == 403
    actions = {e["action"] for e in as_(boss).get("/v1/admin/events").json()}
    assert {"wall.posted", "wall.removed", "wall.featured"} <= actions


def test_public_wall_is_off_for_organizations_until_an_admin_allows_it(env) -> None:  # noqa: F811
    tenant, user, as_, _copies = env
    slug, other = tenant(), tenant()
    ann, boss, outsider = user(slug, "ann"), user(slug, "boss", role="admin"), user(other, "cat")
    add_table(ann)

    assert as_(ann).get("/v1/shares/preview", params={"thread_id": THREAD}).json()["public_wall"] is False
    assert as_(ann).post("/v1/shares", json={"thread_id": THREAD, "audience": "public", "wall": "public"}).status_code == 403
    as_(boss).put("/v1/admin/sharing", json={"enabled": True, "public_links": True, "public_wall": True})
    assert as_(ann).post("/v1/shares", json={"thread_id": THREAD, "audience": "organization",
                                             "wall": "public"}).status_code == 422  # needs a public link
    posted = share(as_(ann), audience="public", wall="public")

    anonymous = TestClient(app)
    resp = anonymous.get("/v1/public/wall", params={"sort": "new"})
    assert resp.status_code == 200 and resp.headers["x-robots-tag"] == "noindex"
    card = next(c for c in resp.json() if c["id"] == posted["id"])
    assert card["organization"] == slug and card["owner_name"] == "ann" and card["wall"] == "public"
    assert not {"owner_user_id", "owner_email", "tenant_id", "my_vote", "is_owner"} & card.keys()

    # Members of any organization may upvote public items; the count spans organizations.
    assert as_(outsider).post(f"/v1/wall/{posted['id']}/vote").json() == {"votes": 1, "my_vote": True}
    public = as_(outsider).get("/v1/wall", params={"scope": "public", "sort": "new"}).json()["items"]
    assert next(c for c in public if c["id"] == posted["id"])["my_vote"] is True
    assert posted["id"] in ids(as_(ann).get("/v1/wall").json()["items"])  # also on its organization's wall

    as_(boss).put("/v1/admin/sharing", json={"enabled": True, "public_links": False})  # hides it at once
    assert posted["id"] not in ids(anonymous.get("/v1/public/wall", params={"sort": "new"}).json())
    as_(boss).put("/v1/admin/sharing", json={"enabled": True, "public_links": True})
    assert posted["id"] in ids(anonymous.get("/v1/public/wall", params={"sort": "new"}).json())
    as_(boss).put("/v1/admin/sharing", json={"enabled": True, "public_wall": False})
    assert posted["id"] not in ids(anonymous.get("/v1/public/wall", params={"sort": "new"}).json())


def test_personal_accounts_post_to_the_public_wall_and_have_no_organization_wall(
    env, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    tenant, user, as_, _copies = env
    slug = tenant()
    monkeypatch.setattr(settings, "personal_tenant", slug)
    ann, ben = user(slug, "ann"), user(slug, "ben")
    add_table(ann)

    preview = as_(ann).get("/v1/shares/preview", params={"thread_id": THREAD}).json()
    assert preview["personal"] is True and preview["org_wall"] is False and preview["public_wall"] is True
    assert as_(ann).post("/v1/shares", json={"thread_id": THREAD, "audience": "organization",
                                             "wall": "organization"}).status_code == 403
    posted = share(as_(ann), audience="public", wall="public")
    assert as_(ben).get("/v1/wall").json()["enabled"] is False  # strangers share this tenant

    card = next(c for c in TestClient(app).get("/v1/public/wall", params={"sort": "new"}).json()
                if c["id"] == posted["id"])
    assert card["organization"] is None  # the personal tenant isn't an organization


def test_hot_ranks_upvoted_items_above_newer_ones(env) -> None:  # noqa: F811
    tenant, user, as_, _copies = env
    slug = tenant()
    ann, ben, cat = user(slug, "ann"), user(slug, "ben"), user(slug, "cat")
    add_table(ann)
    older, newer = share(as_(ann)), share(as_(ann))
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE shared_conversations SET listed_at = now() - interval '1 hour' WHERE id = %s",
                     (older["id"],))
    as_(ben).post(f"/v1/wall/{older['id']}/vote")
    as_(cat).post(f"/v1/wall/{older['id']}/vote")
    assert ids(as_(ben).get("/v1/wall", params={"sort": "new"}).json()["items"]) == [newer["id"], older["id"]]
    assert ids(as_(ben).get("/v1/wall", params={"sort": "hot"}).json()["items"]) == [older["id"], newer["id"]]
