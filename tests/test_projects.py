from copy import deepcopy
import uuid

from fastapi.testclient import TestClient
import pytest

from backend.app import create_app
from backend.models import Cloud, Project
from backend.project_schemas import MAX_PROJECT_BYTES
from test_api import application, register, seed_cloud, use_cookies


def scene():
    return {"schemaVersion": 1, "camera": {"position": [500000, 6000000, 100],
            "yaw": 0.5, "pitch": -0.4, "radius": 25},
            "measurements": [{"id": str(uuid.uuid4()), "name": "Odległość", "points": [[1, 2, 3], [4, 5, 6]]}]}


def create(client, cloud, *, name="Pomiary elewacji", state=None):
    return client.post("/api/projects", json={"name": name, "cloudId": cloud, "sceneState": state or scene()})


def test_project_crud_multiple_saves_revision_conflicts_and_source_preservation(application):
    app, client, settings = application
    owner, _ = register(client)
    cloud, directory = seed_cloud(app, settings, owner)
    original = (directory / "original.las").read_bytes()
    first = create(client, cloud)
    assert first.status_code == 201, first.text
    project = first.json()
    assert project["revision"] == 1 and project["cloudId"] == cloud
    assert project["sceneState"]["camera"]["position"] == [500000, 6000000, 100]
    assert create(client, cloud, name="Drugi projekt").status_code == 201
    listing = client.get("/api/projects", params={"cloudId": cloud}).json()["projects"]
    assert len(listing) == 2 and all("sceneState" not in item for item in listing)
    assert client.get(f"/viewer?project={project['id']}").status_code == 200
    state = deepcopy(project["sceneState"])
    state["camera"]["position"] = [1, 2, 3]
    body = {"name": "Po zmianie", "sceneState": state, "revision": 1}
    updated = client.put(f"/api/projects/{project['id']}", json=body)
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert updated.json()["sceneState"]["camera"]["position"] == [1, 2, 3]
    assert client.put(f"/api/projects/{project['id']}", json=body).status_code == 409
    assert client.get(f"/api/projects/{project['id']}").json()["revision"] == 2
    assert (directory / "original.las").read_bytes() == original
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}").status_code == 404
    assert client.get(f"/api/clouds/{cloud}").status_code == 200


def test_projects_stay_private_on_public_clouds_and_reject_spoofed_ownership(application):
    app, client, settings = application
    owner, owner_cookies = register(client)
    cloud, _ = seed_cloud(app, settings, owner, visibility="public")
    project = create(client, cloud).json()
    other, other_cookies = register(client, "projects-other@example.test")
    own_cloud, _ = seed_cloud(app, settings, other)
    for cookies, forbidden in [(other_cookies, 404), ({}, 401)]:
        use_cookies(client, cookies)
        assert client.get(f"/api/projects/{project['id']}").status_code == forbidden
        assert client.get(f"/viewer?project={project['id']}").status_code == forbidden
        assert create(client, cloud).status_code == forbidden
        assert client.put(f"/api/projects/{project['id']}", json={
            "name": "Obcy zapis", "sceneState": scene(), "revision": 1}).status_code == forbidden
        assert client.delete(f"/api/projects/{project['id']}").status_code == forbidden
    use_cookies(client, other_cookies)
    assert client.get("/api/projects").json()["projects"] == []
    assert client.get("/api/projects", params={"cloudId": cloud}).status_code == 404
    assert create(client, own_cloud).status_code == 201
    use_cookies(client, owner_cookies)
    assert create(client, cloud, state={**scene(), "owner_id": other["id"]}).status_code == 422
    response = client.post("/api/projects", json={"name": "Nazwa", "cloudId": cloud,
        "sceneState": scene(), "owner_id": other["id"]})
    assert response.status_code == 422


def test_project_survives_server_restart_and_uses_stable_cloud_id(application):
    app, client, settings = application
    owner, cookies = register(client)
    cloud, _ = seed_cloud(app, settings, owner)
    project = create(client, cloud).json()
    assert client.patch(f"/api/clouds/{cloud}", json={"name": "Nowa nazwa chmury"}).status_code == 200
    with TestClient(create_app(settings)) as restarted:
        use_cookies(restarted, cookies)
        saved = restarted.get(f"/api/projects/{project['id']}").json()
        assert saved["cloudId"] == cloud
        assert saved["cloudName"] == "Nowa nazwa chmury"
        assert saved["sceneState"] == project["sceneState"]


def test_deleting_cloud_retains_project_but_blocks_open_and_update(application):
    app, client, settings = application
    owner, _ = register(client)
    cloud, _ = seed_cloud(app, settings, owner)
    project = create(client, cloud).json()
    assert client.delete(f"/api/clouds/{cloud}").status_code == 204
    saved = client.get("/api/projects").json()["projects"]
    assert saved[0]["id"] == project["id"] and saved[0]["cloudAvailable"] is False
    assert client.get(f"/api/projects/{project['id']}").status_code == 404
    assert client.get(f"/viewer?project={project['id']}").status_code == 404
    assert client.put(f"/api/projects/{project['id']}", json={
        "name": "Zmiana", "sceneState": scene(), "revision": 1}).status_code == 404
    with app.state.database.session() as session:
        assert session.get(Project, uuid.UUID(project["id"])).scene_state == project["sceneState"]
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204


@pytest.mark.parametrize("invalid", [
    {"schemaVersion": 2}, {"url": "https://example.test/cloud.js"}, {"cameraAnimations": []},
    {"volumes": [{"id": str(uuid.uuid4()), "position": [0, 0, 0], "type": "Viewer"}]},
    {"camera": {"position": [0, 0], "yaw": 0, "pitch": 0, "radius": 1}},
    {"camera": {"position": [0, 0, 0], "yaw": 0, "pitch": 2, "radius": 0}},
    {"classification": {"__proto__": {"name": "X", "visible": True, "color": [1, 1, 1, 1]}}},
])
def test_invalid_project_scenes_are_rejected(application, invalid):
    app, client, settings = application
    owner, _ = register(client)
    cloud, _ = seed_cloud(app, settings, owner)
    assert create(client, cloud, state={**scene(), **invalid}).status_code == 422
    assert client.get("/api/projects").json()["projects"] == []


def test_project_rejects_cyclic_annotations_duplicate_ids_large_bodies_and_cross_origin(application):
    app, client, settings = application
    owner, _ = register(client)
    cloud, _ = seed_cloud(app, settings, owner)
    identifier = str(uuid.uuid4())
    state = scene()
    state["annotations"] = [{"id": identifier, "parentId": identifier, "position": [0, 0, 0]}]
    assert create(client, cloud, state=state).status_code == 422
    state = scene()
    state["measurements"] *= 2
    assert create(client, cloud, state=state).status_code == 422
    body = {"name": "Projekt", "cloudId": cloud, "sceneState": scene()}
    assert client.post("/api/projects", json=body, headers={"Origin": "https://other.test"}).status_code == 403
    assert client.post("/api/projects", json=body, headers={"Content-Length": str(MAX_PROJECT_BYTES + 5000)}).status_code == 413


def test_project_requires_ready_cloud_and_immutable_cloud_binding(application):
    app, client, settings = application
    owner, _ = register(client)
    cloud, _ = seed_cloud(app, settings, owner)
    other_cloud, _ = seed_cloud(app, settings, owner)
    project = create(client, cloud).json()
    assert client.put(f"/api/projects/{project['id']}", json={"name": "Zmiana", "sceneState": scene(),
        "revision": 1, "cloudId": other_cloud}).status_code == 422
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(other_cloud)).status = "converting"
        session.commit()
    assert create(client, other_cloud).status_code == 409


def test_project_rejects_nonfinite_json_coordinates(application):
    app, client, settings = application
    owner, _ = register(client)
    cloud, _ = seed_cloud(app, settings, owner)
    body = '{"name":"Projekt","cloudId":"' + cloud + '","sceneState":{"camera":' \
           '{"position":[NaN,0,0],"yaw":0,"pitch":0,"radius":1}}}'
    assert client.post("/api/projects", content=body, headers={"Content-Type": "application/json"}).status_code == 422
