from copy import deepcopy
import uuid

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.models import Cloud, Project
from test_api import application, register, seed_cloud, use_cookies
from test_projects import scene


def create_shared(client, cloud, visibility="private"):
    response = client.post("/api/projects", json={"name": "Udostępniony widok", "cloudId": cloud,
        "sceneState": scene(), "visibility": visibility})
    assert response.status_code == 201, response.text
    return response.json()


def test_public_project_shares_private_cloud_view_without_other_private_data(application):
    app, client, settings = application
    owner, cookies = register(client)
    source, _ = seed_cloud(app, settings, owner)
    cloud, directory = seed_cloud(app, settings, owner, parent=uuid.UUID(source))
    (directory / "working.laz").write_bytes(b"private processing source")
    (directory / "log.txt").write_text("private conversion log")
    (directory / "private.log").write_text("private log")
    with app.state.database.session() as session:
        record = session.get(Cloud, uuid.UUID(cloud))
        record.processing_file = "working.laz"
        record.operation = {"type": "transformation", "translation": [1, 2, 3]}
        session.commit()
    private = create_shared(client, cloud)
    project = create_shared(client, cloud)
    assert project["visibility"] == "private" and project["canEdit"]
    route = f"/api/projects/{project['id']}"
    published = client.patch(route, json={"visibility": "public", "revision": 1})
    assert published.status_code == 200, published.text
    assert published.json()["revision"] == 2 and published.json()["canEdit"]
    assert client.get(route).json()["sceneState"] == project["sceneState"]
    client.cookies.clear()
    public = client.get(route)
    assert public.status_code == 200 and public.json()["sceneState"] == project["sceneState"]
    assert public.json()["visibility"] == "public" and not public.json()["canEdit"]
    assert client.get(public.json()["viewerUrl"]).status_code == 200
    descriptor = client.get(public.json()["cloudUrl"])
    assert descriptor.status_code == 200, descriptor.text
    data = descriptor.json()
    assert not data["canEdit"] and data["visibility"] == "private"
    assert not data["sourceAvailable"] and not data["processingAvailable"]
    assert data["sourceFile"] is data["operation"] is data["parentCloud"] is None
    assert data["url"] == route + "/files/metadata.json"
    assert client.get(data["url"]).status_code == 200
    info = client.get(data["infoUrl"])
    assert info.status_code == 200 and info.json()["source"] is info.json()["collections"] is None
    assert info.json()["cloud"]["parentCloud"] is info.json()["cloud"]["operation"] is None
    for path in ["original.las", "working.laz", "cloud-info.json", "log.txt", "private.log", ".hidden.bin", "../original.las"]:
        assert client.get(route + "/files/" + path).status_code == 404
    for path in [f"/api/clouds/{cloud}", f"/api/clouds/{cloud}/files/metadata.json", f"/api/clouds/{cloud}/info",
                 f"/api/clouds/{source}", f"/pointclouds/{cloud}/metadata.json"]:
        assert client.get(path).status_code == 404
    assert client.get(f"/api/projects/{private['id']}").status_code == 401
    assert client.get("/api/projects").status_code == 401
    use_cookies(client, cookies)
    assert client.get(f"/api/clouds/{cloud}").json()["visibility"] == "private"
    assert client.get(f"/api/clouds/{cloud}/download").status_code == 200
    with TestClient(create_app(settings)) as restarted:
        saved = restarted.get(route)
        assert saved.status_code == 200 and saved.json()["visibility"] == "public"
        assert saved.json()["sceneState"] == project["sceneState"] and not saved.json()["canEdit"]
        assert restarted.get(route + "/files/metadata.json").status_code == 200


def test_public_project_tiles_support_range_head_and_legacy_tiles(application):
    app, client, settings = application
    owner, _ = register(client)
    cloud, directory = seed_cloud(app, settings, owner)
    (directory / "data").mkdir()
    (directory / "data" / "r.hrc").write_bytes(b"hierarchy")
    (directory / "data" / "r.laz").write_bytes(b"converted tile")
    project = create_shared(client, cloud, "public")
    route = f"/api/projects/{project['id']}/files"
    client.cookies.clear()
    response = client.get(route + "/octree.bin", headers={"Range": "bytes=0-3"})
    assert response.status_code == 206 and response.content == b"0123"
    assert response.headers["content-range"] == "bytes 0-3/10"
    assert response.headers["cache-control"] == "private, no-store"
    head = client.head(route + "/octree.bin")
    assert head.status_code == 200 and head.content == b"" and head.headers["content-length"] == "10"
    assert client.get(route + "/octree.bin", headers={"Range": "bytes=100-200"}).status_code == 416
    assert client.get(route + "/data/r.hrc").content == b"hierarchy"
    assert client.get(route + "/data/r.laz").content == b"converted tile"
    assert client.head(route + "/original.las").status_code == 404


def test_external_viewers_cannot_modify_state_visibility_or_binding(application):
    app, client, settings = application
    owner, owner_cookies = register(client)
    cloud, _ = seed_cloud(app, settings, owner)
    project = create_shared(client, cloud, "public")
    route = f"/api/projects/{project['id']}"
    other, other_cookies = register(client, "public-project-other@example.test")
    for cookies, status in [(other_cookies, 404), ({}, 401)]:
        use_cookies(client, cookies)
        assert client.get(route).status_code == 200 and not client.get(route).json()["canEdit"]
        assert client.put(route, json={"name": "Nadpisz", "sceneState": scene(), "revision": 1}).status_code == status
        assert client.patch(route, json={"visibility": "private", "revision": 1}).status_code == status
        assert client.delete(route).status_code == status
        assert client.post("/api/projects", json={"name": "Kopia", "cloudId": cloud, "sceneState": scene()}).status_code == status
    use_cookies(client, other_cookies)
    assert client.get("/api/projects").json() == {"projects": []}
    use_cookies(client, owner_cookies)
    saved = client.get(route).json()
    assert saved["sceneState"] == project["sceneState"] and saved["revision"] == 1
    assert saved["visibility"] == "public" and saved["canEdit"]
    assert client.put(route, json={"name": "Zmiana właściciela", "sceneState": scene(), "revision": 1}).status_code == 200


def test_visibility_revision_conflicts_and_revocation(application):
    app, client, settings = application
    owner, owner_cookies = register(client)
    cloud, _ = seed_cloud(app, settings, owner)
    project = create_shared(client, cloud)
    route = f"/api/projects/{project['id']}"
    assert client.patch(route, json={"visibility": "public", "revision": 1}).json()["revision"] == 2
    assert client.patch(route, json={"visibility": "private", "revision": 1}).status_code == 409
    assert client.put(route, json={"name": "Stary zapis", "sceneState": scene(), "revision": 1}).status_code == 409
    state = deepcopy(project["sceneState"])
    state["camera"]["position"] = [1, 2, 3]
    assert client.put(route, json={"name": "Nowy zapis", "sceneState": state, "revision": 2}).json()["visibility"] == "public"
    client.cookies.clear()
    assert client.get(route).json()["sceneState"] == state
    use_cookies(client, owner_cookies)
    assert client.patch(route, json={"visibility": "private", "revision": 3}).json()["revision"] == 4
    client.cookies.clear()
    for suffix in ["", "/cloud", "/info", "/files/metadata.json", "/files/octree.bin"]:
        assert client.get(route + suffix).status_code == 401
    assert client.get(project["viewerUrl"]).status_code == 401


def test_visibility_validation_origin_and_unavailable_cloud(application):
    app, client, settings = application
    owner, _ = register(client)
    cloud, _ = seed_cloud(app, settings, owner)
    project = create_shared(client, cloud, "public")
    route = f"/api/projects/{project['id']}"
    for body in [{"visibility": "invalid", "revision": 1}, {"visibility": None, "revision": 1},
                 {"visibility": "private", "revision": 0}, {"visibility": "private", "revision": 1, "sceneState": scene()}]:
        assert client.patch(route, json=body).status_code == 422
    assert client.patch(route, json={"visibility": "private", "revision": 1}, headers={"Origin": "https://foreign.test"}).status_code == 403
    assert client.delete(f"/api/clouds/{cloud}").status_code == 204
    for suffix in ["", "/cloud", "/info", "/files/metadata.json"]:
        assert client.get(route + suffix).status_code == 404
    assert client.patch(route, json={"visibility": "private", "revision": 1}).status_code == 200
    assert client.patch(route, json={"visibility": "public", "revision": 2}).status_code == 404
    with app.state.database.session() as session:
        saved = session.get(Project, uuid.UUID(project["id"]))
        assert saved.scene_state == project["sceneState"] and saved.visibility == "private"
