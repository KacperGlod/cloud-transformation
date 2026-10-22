import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app import create_app
from backend.models import Cloud, CloudCollection, CollectionCloud, Project, utcnow
from test_api import application, register, seed_cloud, use_cookies


def create(client, name="Most Kraków", clouds=None):
    return client.post("/api/collections", json={"name": name, "cloudIds": clouds or []})


def test_optional_many_to_many_membership_and_deleting_collection_preserves_data(application):
    app, client, settings = application
    owner, _ = register(client)
    source, directory = seed_cloud(app, settings, owner)
    result, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(source))
    unrelated, _ = seed_cloud(app, settings, owner)
    original = (directory / "original.las").read_bytes()
    assert client.get("/api/collections").json() == {"collections": []}
    assert len(client.get("/api/clouds").json()["clouds"]) == 3
    empty = create(client)
    assert empty.status_code == 201, empty.text
    first = empty.json()
    assert first["cloudIds"] == []
    assert client.get(f"/api/collections/{first['id']}/suggestions").json() == {"suggestions": []}
    second = create(client, "Druga kolekcja", [source]).json()
    added = client.post(f"/api/collections/{first['id']}/clouds", json={"cloudIds": [source, result, source]})
    assert added.status_code == 200, added.text
    assert set(added.json()["cloudIds"]) == {source, result}
    repeated = client.post(f"/api/collections/{first['id']}/clouds", json={"cloudIds": [source]})
    assert set(repeated.json()["cloudIds"]) == {source, result}
    with app.state.database.session() as session:
        assert session.scalar(select(func.count()).select_from(CollectionCloud)) == 3
        project = Project(owner_id=uuid.UUID(owner["id"]), cloud_id=uuid.UUID(source), name="Widok",
                          scene_state={"schemaVersion": 1})
        session.add(project)
        session.commit()
        project_id = project.id
    renamed = client.patch(f"/api/collections/{first['id']}", json={"name": "  Nowa nazwa  "})
    assert renamed.json()["name"] == "Nowa nazwa"
    assert renamed.json()["id"] == first["id"]
    assert client.delete(f"/api/collections/{first['id']}/clouds/{source}").json()["cloudIds"] == [result]
    assert client.delete(f"/api/collections/{first['id']}/clouds/{source}").status_code == 200
    assert client.get(f"/api/collections/{second['id']}").json()["cloudIds"] == [source]
    assert client.delete(f"/api/collections/{first['id']}").status_code == 204
    assert client.get(f"/api/collections/{first['id']}").status_code == 404
    assert len(client.get("/api/clouds").json()["clouds"]) == 3
    assert (directory / "original.las").read_bytes() == original
    with app.state.database.session() as session:
        assert session.get(Project, project_id) is not None
        assert session.scalar(select(func.count()).select_from(CollectionCloud)) == 1
        assert session.get(Cloud, uuid.UUID(unrelated)).status == "ready"


def test_collections_and_suggestions_are_private_and_batches_are_atomic(application):
    app, client, settings = application
    owner, owner_cookies = register(client)
    source, _ = seed_cloud(app, settings, owner, visibility="public")
    child, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(source))
    collection = create(client, clouds=[source]).json()
    other, other_cookies = register(client, "collection-other@example.test")
    other_cloud, _ = seed_cloud(app, settings, other, visibility="public", parent=uuid.UUID(source))
    own_collection = create(client, clouds=[other_cloud]).json()
    assert client.get("/api/collections").json()["collections"] == [own_collection]
    for method, suffix, body in [("GET", "", None), ("GET", "/suggestions", None),
                                 ("PATCH", "", {"name": "Obca"}), ("DELETE", "", None),
                                 ("POST", "/clouds", {"cloudIds": [other_cloud]}),
                                 ("DELETE", f"/clouds/{source}", None)]:
        assert client.request(method, f"/api/collections/{collection['id']}{suffix}", json=body).status_code == 404
    assert create(client, clouds=[source]).status_code == 404  # Public viewing does not grant ownership.
    use_cookies(client, owner_cookies)
    response = client.post(f"/api/collections/{collection['id']}/clouds", json={"cloudIds": [child, other_cloud]})
    assert response.status_code == 404
    assert client.get(f"/api/collections/{collection['id']}").json()["cloudIds"] == [source]
    suggestions = client.get(f"/api/collections/{collection['id']}/suggestions").json()["suggestions"]
    assert {item["cloudId"] for item in suggestions} == {child}
    assert client.post("/api/collections", json={"name": "Spoof", "owner_id": other["id"]}).status_code == 422
    client.cookies.clear()
    assert client.get("/api/collections").status_code == 401
    assert client.get(f"/api/collections/{collection['id']}/suggestions").status_code == 401
    assert create(client).status_code == 401
    use_cookies(client, other_cookies)
    assert client.get(f"/api/collections/{own_collection['id']}/suggestions").json() == {"suggestions": []}


def test_suggestions_follow_ancestors_descendants_and_siblings_without_auto_add(application):
    app, client, settings = application
    owner, _ = register(client)
    root, _ = seed_cloud(app, settings, owner)
    child, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root))
    grandchild, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(child))
    sibling, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root))
    unrelated, _ = seed_cloud(app, settings, owner)
    collection = create(client, clouds=[child]).json()
    route = f"/api/collections/{collection['id']}"
    suggestions = client.get(route + "/suggestions").json()["suggestions"]
    assert {item["cloudId"] for item in suggestions} == {root, grandchild, sibling}
    assert {item["relatedCloudId"] for item in suggestions} == {child}
    assert next(item for item in suggestions if item["cloudId"] == sibling)["distance"] == 2
    assert client.get(route).json()["cloudIds"] == [child]
    later, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(grandchild))
    assert later in {item["cloudId"] for item in client.get(route + "/suggestions").json()["suggestions"]}
    assert client.get(route).json()["cloudIds"] == [child]
    # Explicit membership in a second family extends suggestions to that family as well.
    other_result, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(unrelated))
    client.post(route + "/clouds", json={"cloudIds": [unrelated, root]})
    ids = {item["cloudId"] for item in client.get(route + "/suggestions").json()["suggestions"]}
    assert ids == {grandchild, sibling, later, other_result}
    client.delete(route + f"/clouds/{unrelated}")
    assert other_result not in {item["cloudId"] for item in client.get(route + "/suggestions").json()["suggestions"]}


def test_deleted_clouds_are_hidden_but_can_connect_surviving_relatives_and_cycles_terminate(application):
    app, client, settings = application
    owner, _ = register(client)
    root, _ = seed_cloud(app, settings, owner)
    child, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root))
    sibling, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root))
    collection = create(client, clouds=[child, root]).json()
    route = f"/api/collections/{collection['id']}"
    assert client.delete(f"/api/clouds/{root}").status_code == 204
    assert client.get(route).json()["cloudIds"] == [child]
    assert client.get("/api/collections").json()["collections"][0]["cloudIds"] == [child]
    assert {item["cloudId"] for item in client.get(route + "/suggestions").json()["suggestions"]} == {sibling}
    assert client.post(route + "/clouds", json={"cloudIds": [root]}).status_code == 404
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(root)).parent_id = uuid.UUID(sibling)
        session.commit()
    assert {item["cloudId"] for item in client.get(route + "/suggestions").json()["suggestions"]} == {sibling}
    client.delete(f"/api/clouds/{child}")
    assert client.get(route).json()["cloudIds"] == []
    assert client.get(route + "/suggestions").json() == {"suggestions": []}


def test_pending_clouds_names_validation_origin_and_restart(application):
    app, client, settings = application
    owner, cookies = register(client)
    cloud, _ = seed_cloud(app, settings, owner)
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(cloud)).status = "queued"
        session.commit()
    collection = create(client, clouds=[cloud]).json()
    assert collection["cloudIds"] == [cloud]
    for name in ["", "   ", "x" * 121]:
        assert create(client, name).status_code == 422
        assert client.patch(f"/api/collections/{collection['id']}", json={"name": name}).status_code == 422
    assert client.post(f"/api/collections/{collection['id']}/clouds", json={"cloudIds": []}).status_code == 422
    assert client.post(f"/api/collections/{collection['id']}/clouds", json={"cloudIds": ["invalid"]}).status_code == 422
    assert client.post("/api/collections", json={"name": "Origin"}, headers={"Origin": "https://foreign.test"}).status_code == 403
    assert create(client, clouds=[str(uuid.uuid4())]).status_code == 404
    with app.state.database.session() as session:
        assert session.scalar(select(func.count()).select_from(CloudCollection)) == 1
    with TestClient(create_app(settings)) as restarted:
        use_cookies(restarted, cookies)
        assert restarted.get(f"/api/collections/{collection['id']}").json() == collection
