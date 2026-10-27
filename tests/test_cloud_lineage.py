import uuid

import pytest

from backend.cloud_lineage import MAX_LINEAGE_NODES
from backend.models import Cloud, ProcessingJob
from test_api import application, register, seed_cloud, use_cookies


def lineage(client, identifier):
    response = client.get(f"/api/clouds/{identifier}/lineage")
    assert response.status_code == 200, response.text
    return response.json()


def test_lineage_includes_ancestors_siblings_and_descendants_with_actions(application):
    app, client, settings = application
    owner, _ = register(client)
    root, _ = seed_cloud(app, settings, owner)
    child, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root))
    result, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(child))
    sibling, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root))
    unrelated, _ = seed_cloud(app, settings, owner)
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(child)).operation = {"type": "segmentation", "clipBox": {"secret": "payload"}}
        session.get(Cloud, uuid.UUID(result)).operation = {"type": "transformation", "translation": [1, 2, 3]}
        session.get(Cloud, uuid.UUID(sibling)).operation = {"type": "point-removal"}
        session.commit()
    history = lineage(client, result)
    nodes = {node["id"]: node for node in history["nodes"]}
    assert history["cloudId"] == result and history["notices"] == [] and not history["truncated"]
    assert set(nodes) == {root, child, result, sibling} and unrelated not in nodes
    assert nodes[child]["parentId"] == root and nodes[result]["parentId"] == child
    assert nodes[root]["operationType"] == "import" and nodes[root]["comparisonUrl"] is None
    assert nodes[child]["operationType"] == "segmentation"
    assert nodes[result]["operationType"] == "transformation"
    assert nodes[sibling]["operationType"] == "point-removal"
    for identifier, node in nodes.items():
        assert node["viewerUrl"] == f"/viewer?cloud={identifier}" and node["canInspect"]
        assert node["createdAt"] and not node["sourceUnavailable"]
        if identifier != root:
            assert node["comparisonUrl"] == f"/compare?cloud={identifier}"
            assert client.get(f"/api/clouds/{identifier}/comparison").status_code == 200
    assert "clipBox" not in str(history) and "translation" not in str(history)
    client.patch(f"/api/clouds/{root}", json={"name": "Nowa nazwa źródła"})
    assert next(node for node in lineage(client, child)["nodes"] if node["id"] == root)["name"] == "Nowa nazwa źródła"
    assert lineage(client, unrelated)["nodes"][0]["id"] == unrelated


def test_lineage_is_private_even_when_all_family_members_are_public(application):
    app, client, settings = application
    owner, cookies = register(client)
    root, _ = seed_cloud(app, settings, owner, visibility="public")
    child, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root), visibility="public")
    route = f"/api/clouds/{child}/lineage"
    assert len(lineage(client, child)["nodes"]) == 2
    client.cookies.clear()
    assert client.get(route).status_code == 401
    other, _ = register(client, "lineage-other@example.test")
    other_child, _ = seed_cloud(app, settings, other, parent=uuid.UUID(root))
    assert client.get(route).status_code == 404
    own = lineage(client, other_child)
    assert len(own["nodes"]) == 1 and own["nodes"][0]["parentId"] is None
    assert own["nodes"][0]["sourceUnavailable"] and own["nodes"][0]["comparisonUrl"] is None
    assert root not in str(own) and child not in str(own)
    use_cookies(client, cookies)
    assert other_child not in str(lineage(client, root))
    assert client.get("/api/clouds/not-a-cloud/lineage").status_code == 404


def test_deleted_sources_remain_in_history_without_actions(application):
    app, client, settings = application
    owner, _ = register(client)
    root, directory = seed_cloud(app, settings, owner)
    child, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root))
    grandchild, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(child))
    sibling, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root))
    assert client.delete(f"/api/clouds/{root}").status_code == 204
    assert not directory.exists()
    nodes = {node["id"]: node for node in lineage(client, grandchild)["nodes"]}
    assert set(nodes) == {root, child, grandchild, sibling}
    assert nodes[root]["status"] == "deleted" and not nodes[root]["canInspect"]
    assert nodes[root]["viewerUrl"] is nodes[root]["comparisonUrl"] is None
    assert nodes[child]["comparisonUrl"] is None and nodes[grandchild]["comparisonUrl"]
    assert client.get(f"/api/clouds/{root}/lineage").status_code == 404


@pytest.mark.parametrize("state", ["queued", "converting", "failed", "cancelled", "deleting", "deleted"])
def test_unavailable_nodes_have_no_open_or_compare_links_and_keep_job_kind(application, state):
    app, client, settings = application
    owner, _ = register(client)
    root, _ = seed_cloud(app, settings, owner)
    child, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root))
    with app.state.database.session() as session:
        record = session.get(Cloud, uuid.UUID(child))
        record.status = state
        session.add(ProcessingJob(cloud_id=record.id, owner_id=record.owner_id, source_cloud_id=uuid.UUID(root),
                                  kind="transformation", status=state, parameters={"translation": [1, 2, 3]}))
        session.commit()
    node = next(node for node in lineage(client, root)["nodes"] if node["id"] == child)
    assert node["operationType"] == "transformation"
    assert node["viewerUrl"] is node["comparisonUrl"] is None
    assert node["canInspect"] == (state not in ("deleting", "deleted"))


def test_invalid_operation_labels_and_cycles_are_bounded(application):
    app, client, settings = application
    owner, _ = register(client)
    root, _ = seed_cloud(app, settings, owner)
    child, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root))
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(root)).parent_id = uuid.UUID(child)
        session.get(Cloud, uuid.UUID(child)).operation = {"type": 17}
        session.commit()
    history = lineage(client, child)
    assert len(history["nodes"]) == 2 and any("cykl" in note for note in history["notices"])
    assert next(node for node in history["nodes"] if node["id"] == child)["operationType"] == "processing"
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(root)).parent_id = uuid.UUID(root)
        session.commit()
    assert next(node for node in lineage(client, child)["nodes"] if node["id"] == root)["comparisonUrl"] is None


def test_missing_viewer_entry_disables_actions_without_losing_history(application):
    app, client, settings = application
    owner, _ = register(client)
    root, _ = seed_cloud(app, settings, owner)
    child, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(root))
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(root)).entry_file = None
        session.commit()
    nodes = {node["id"]: node for node in lineage(client, child)["nodes"]}
    assert nodes[root]["canInspect"] and nodes[root]["viewerUrl"] is None
    assert nodes[child]["viewerUrl"] and nodes[child]["comparisonUrl"] is None


def test_large_family_returns_a_limited_connected_fragment_without_reading_geometry(application):
    app, client, settings = application
    owner, _ = register(client)
    identifier = uuid.uuid4()
    with app.state.database.session() as session:
        session.add(Cloud(id=identifier, owner_id=uuid.UUID(owner["id"]), name="Korzeń", storage_key=str(identifier), status="queued"))
        session.flush()
        for index in range(MAX_LINEAGE_NODES + 5):
            child_id = uuid.uuid4()
            session.add(Cloud(id=child_id, owner_id=uuid.UUID(owner["id"]), name=f"Wynik {index}",
                              storage_key=str(child_id), status="queued", parent_id=identifier))
        session.commit()
    history = lineage(client, str(identifier))
    assert len(history["nodes"]) == MAX_LINEAGE_NODES and history["truncated"]
    assert str(identifier) in {node["id"] for node in history["nodes"]}
    assert history["notices"] and all(node["viewerUrl"] is None for node in history["nodes"])
