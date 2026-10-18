from pathlib import Path
from datetime import datetime
import json
import struct
import uuid
from urllib.parse import quote

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from backend.app import create_app
from backend.config import Settings
from backend.models import Cloud, ProcessingJob, User, iso_utc, utcnow
from backend.worker import Worker


@pytest.fixture
def application(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", clouds_dir=tmp_path / "pointclouds",
                        runtime_dir=tmp_path / "runtime", database_url="", start_worker=False)
    app = create_app(settings)
    with TestClient(app) as client:
        yield app, client, settings


def register(client, email="owner@example.com"):
    client.cookies.clear()
    response = client.post("/api/auth/register", json={"email": email, "password": "test-password-123"})
    assert response.status_code == 201, response.text
    return response.json()["user"], dict(client.cookies)


def use_cookies(client, cookies):
    client.cookies.clear()
    client.cookies.update(cookies)


def seed_cloud(app, settings, owner, *, visibility="private", parent=None, key=None):
    identifier = uuid.uuid4()
    storage_key = key or str(identifier)
    directory = settings.clouds_dir / storage_key
    directory.mkdir()
    (directory / "metadata.json").write_text(json.dumps({"version": "2.0", "hierarchy": {"firstChunkSize": 22}}))
    (directory / "hierarchy.bin").write_bytes(struct.pack("<BBIqq", 1, 0, 1, 0, 10))
    (directory / "octree.bin").write_bytes(b"0123456789")
    (directory / "original.las").write_bytes(las_bytes())
    (directory / "cloud-info.json").write_text('{"name":"Original"}')
    with app.state.database.session() as session:
        cloud = Cloud(id=identifier, owner_id=uuid.UUID(owner["id"]), name="Testowa chmura",
            visibility=visibility, status="ready", storage_key=storage_key,
            source_file="original.las", entry_file="metadata.json", parent_id=parent,
            size_bytes=100)
        session.add(cloud)
        session.commit()
    return str(identifier), directory


def seed_completed_job(app, settings, owner, cloud_id):
    identifier = uuid.uuid4()
    with app.state.database.session() as session:
        session.add(ProcessingJob(id=identifier, cloud_id=uuid.UUID(cloud_id), owner_id=uuid.UUID(owner["id"]),
            kind="import", status="completed", progress=100, parameters={"test": True},
            result={"pointCount": 10}, log="Konwersja zakończona"))
        session.commit()
    directory = settings.runtime_dir / str(identifier)
    directory.mkdir()
    (directory / "temporary.las").write_bytes(las_bytes())
    return str(identifier), directory


def las_bytes(count=10):
    header = bytearray(227)
    header[:4] = b"LASF"
    header[24:26] = bytes((1, 2))
    header[26:58] = b"CloudPoint test".ljust(32, b"\0")
    struct.pack_into("<H", header, 94, 227)
    struct.pack_into("<I", header, 96, 227)
    header[104] = 2
    struct.pack_into("<H", header, 105, 26)
    struct.pack_into("<I", header, 107, count)
    struct.pack_into("<I", header, 111, count)
    struct.pack_into("<ddd", header, 131, 0.001, 0.001, 0.001)
    points = [(index % 20 * 1000, index // 20 % 20 * 1000, index // 400 * 1000) for index in range(count)]
    coordinates = list(zip(*points))
    bounds = [value / 1000 for axis in coordinates for value in (max(axis), min(axis))]
    struct.pack_into("<dddddd", header, 179, *bounds)
    records = b"".join(struct.pack("<iiiHBBbBHHHH", *point, 1000, 9, 1, 0, 0, 1, 40000, 20000, 10000)
                       for point in points)
    return bytes(header) + records


def test_registration_login_logout_and_private_web_root(application):
    app, client, settings = application
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/api/clouds").status_code == 401
    assert client.get("/api/auth/status").json() == {"needsSetup": True}
    user, cookies = register(client)
    assert "httponly" in client.post("/api/auth/login", json={"email": user["email"], "password": "test-password-123"}).headers["set-cookie"].lower()
    assert client.get("/").status_code == 200
    assert client.get("/api/auth/me").json()["user"]["id"] == user["id"]
    for path in ("/data/app.sqlite3", "/backend/app.py", "/server.js", "/.git/config", "/tools/segment_pointcloud.py"):
        assert client.get(path).status_code == 404
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"email": user["email"], "password": "wrong-password-123"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": user["email"], "password": "test-password-123"}).status_code == 200


def test_owner_isolation_visibility_and_range_requests(application):
    app, client, settings = application
    owner, owner_cookies = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    file_url = f"/api/clouds/{identifier}/files/octree.bin"
    response = client.get(file_url, headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert client.head(file_url).headers["content-length"] == "10"
    assert client.get(file_url, headers={"Range": "bytes=100-200"}).status_code == 416
    other, other_cookies = register(client, "other@example.com")
    assert client.get("/api/clouds").json() == {"clouds": []}
    assert client.get(file_url).status_code == 404
    assert client.get(f"/viewer?cloud={identifier}").status_code == 404
    assert client.patch(f"/api/clouds/{identifier}", json={"name": "Stolen"}).status_code == 404
    assert client.delete(f"/api/clouds/{identifier}").status_code == 404
    use_cookies(client, owner_cookies)
    assert client.patch(f"/api/clouds/{identifier}", json={"visibility": "public"}).status_code == 200
    client.cookies.clear()
    assert client.get(file_url).status_code == 200
    assert client.get(f"/viewer?cloud={identifier}").status_code == 200
    assert client.get(f"/api/clouds/{identifier}/files/original.las").status_code == 404
    assert client.get(f"/pointclouds/{identifier}/octree.bin").status_code == 200
    assert client.get(f"/api/clouds/{identifier}/files/%2e%2e%2fapp.sqlite3").status_code == 404
    use_cookies(client, owner_cookies)
    client.patch(f"/api/clouds/{identifier}", json={"visibility": "private"})
    client.cookies.clear()
    assert client.get(file_url).status_code == 404


@pytest.mark.parametrize("extension", ["las", "laz"])
def test_source_download_uses_cloud_name_and_preserves_bytes_head_and_range(application, extension):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    filename = f"Skan łąki.{extension}"
    (directory / "original.las").rename(directory / filename)
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(identifier))
        cloud.source_file = filename
        cloud.name = "Zmieniona nazwa łąki"
        session.commit()
    expected_name = f"Zmieniona nazwa łąki.{extension}"
    metadata = client.get(f"/api/clouds/{identifier}").json()
    assert metadata["downloadName"] == expected_name and metadata["sourceFile"] == filename
    url = f"/api/clouds/{identifier}/download"
    response = client.get(url)
    content = (directory / filename).read_bytes()
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == f"attachment; filename*=utf-8''{quote(expected_name)}"
    assert response.headers["cache-control"] == "private, no-store"
    head = client.head(url)
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == str(len(content))
    assert head.headers["content-disposition"] == response.headers["content-disposition"]
    partial = client.get(url, headers={"Range": "bytes=2-25"})
    assert partial.status_code == 206 and partial.content == content[2:26]
    assert partial.headers["content-range"] == f"bytes 2-25/{len(content)}"
    assert client.get(url, headers={"Range": f"bytes={len(content)}-"}).status_code == 416
    assert (directory / filename).read_bytes() == content
    assert not (directory / expected_name).exists()


@pytest.mark.parametrize(("name", "expected_name"), [
    ("Wynik 01", "Wynik 01.las"),
    ("Łąka po obróbce", "Łąka po obróbce.las"),
    ("Wynik.LAS", "Wynik.las"),
    ("Wynik.laz", "Wynik.las"),
    ('Dach / sekcja: A\\B <test>|?*"', "Dach _ sekcja_ A_B _test_____.las"),
    ("Skan\r\nWynik", "Skan__Wynik.las"),
    ("...", "chmura.las"),
    ("CON", "_CON.las"),
    ("aux.sekcja", "_aux.sekcja.las"),
    ("LPT1.las", "_LPT1.las"),
    ("COM¹.las", "_COM¹.las"),
    ("CON .wynik", "_CON .wynik.las"),
    ("Wynik. .", "Wynik.las"),
    ("界" * 120, "界" * 83 + ".las"),
])
def test_download_names_are_safe_and_match_metadata(application, name, expected_name):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    response = client.patch(f"/api/clouds/{identifier}", json={"name": name})
    assert response.status_code == 200
    assert response.json()["downloadName"] == expected_name
    response = client.get(f"/api/clouds/{identifier}/download")
    assert response.status_code == 200
    expected_disposition = (f"attachment; filename*=utf-8''{quote(expected_name)}" if quote(expected_name) != expected_name
                            else f'attachment; filename="{expected_name}"')
    assert response.headers["content-disposition"] == expected_disposition
    assert len(expected_name.encode("utf-8")) <= 255
    assert response.content == (directory / "original.las").read_bytes()


@pytest.mark.parametrize("visibility", ["private", "public"])
def test_source_download_requires_owner_even_for_public_clouds(application, visibility):
    app, client, settings = application
    owner, owner_cookies = register(client)
    identifier, _ = seed_cloud(app, settings, owner, visibility=visibility)
    url = f"/api/clouds/{identifier}/download"
    register(client, "other@example.com")
    if visibility == "public":
        assert client.get(f"/api/clouds/{identifier}").json()["downloadName"] is None
    assert client.get(url).status_code == 404
    assert client.head(url).status_code == 404
    client.cookies.clear()
    assert client.get(url).status_code == 401
    assert client.head(url).status_code == 401
    use_cookies(client, owner_cookies)
    assert client.get(url).status_code == 200


def test_source_download_of_processing_result_returns_its_own_file(application):
    app, client, settings = application
    owner, _ = register(client)
    parent_id, parent_directory = seed_cloud(app, settings, owner)
    result_id, result_directory = seed_cloud(app, settings, owner, parent=uuid.UUID(parent_id))
    result_content = las_bytes(3)
    internal_filename = f"{uuid.uuid4()}.laz"
    (result_directory / "original.las").rename(result_directory / internal_filename)
    (result_directory / internal_filename).write_bytes(result_content)
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(result_id))
        cloud.source_file = internal_filename
        cloud.name = "Wycięty dach"
        session.commit()
    response = client.get(f"/api/clouds/{result_id}/download")
    assert response.status_code == 200 and response.content == result_content
    assert response.headers["content-disposition"] == f"attachment; filename*=utf-8''{quote('Wycięty dach.laz')}"
    assert response.content != (parent_directory / "original.las").read_bytes()
    assert client.get(f"/api/clouds/{parent_id}/download").content == (parent_directory / "original.las").read_bytes()
    renamed = client.patch(f"/api/clouds/{result_id}", json={"name": "Nowy dach"})
    assert renamed.json()["downloadName"] == "Nowy dach.laz"
    response = client.get(f"/api/clouds/{result_id}/download")
    assert response.headers["content-disposition"] == f"attachment; filename*=utf-8''{quote('Nowy dach.laz')}"
    assert response.content == result_content
    assert (result_directory / internal_filename).read_bytes() == result_content


def test_source_download_rejects_missing_files_unready_clouds_and_path_traversal(application):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    url = f"/api/clouds/{identifier}/download"
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(identifier))
        cloud.status = "converting"
        session.commit()
    assert client.get(url).status_code == 409
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(identifier))
        cloud.status = "ready"
        cloud.source_file = None
        session.commit()
    assert not client.get(f"/api/clouds/{identifier}").json()["sourceAvailable"]
    assert client.get(url).status_code == 404
    (settings.clouds_dir / "outside.las").write_bytes(b"File outside the cloud directory")
    for source in ("missing.las", "../outside.las"):
        with app.state.database.session() as session:
            session.get(Cloud, uuid.UUID(identifier)).source_file = source
            session.commit()
        assert client.get(url).status_code == 404
    assert (directory / "original.las").is_file()


def test_rename_has_stable_id_paths_and_validation(application):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    before = client.get(f"/api/clouds/{identifier}").json()
    response = client.patch(f"/api/clouds/{identifier}", json={"name": "Nowa nazwa / z polskimi znakami ąę"})
    assert response.status_code == 200
    after = response.json()
    assert after["id"] == before["id"] and after["url"] == before["url"]
    assert directory.is_dir()
    assert client.patch(f"/api/clouds/{identifier}", json={"visibility": "hidden"}).status_code == 422
    assert client.patch(f"/api/clouds/{identifier}", json={"name": "   "}).status_code == 422
    assert client.patch(f"/api/clouds/{identifier}", json={"owner_id": str(uuid.uuid4())}).status_code == 422


def test_uploads_are_durable_private_and_names_need_not_be_unique(application):
    app, client, settings = application
    owner, _ = register(client)
    for _ in range(2):
        response = client.post("/api/clouds", data={"name": "Ta sama nazwa"},
                               files={"file": ("sample.las", las_bytes(), "application/octet-stream")})
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["status"] == "queued" and job["visibility"] == "private"
        assert client.get(f"/api/jobs/{job['id']}").json()["cloudId"] == job["cloudId"]
        with app.state.database.session() as session:
            assert session.get(ProcessingJob, uuid.UUID(job["id"])).status == "queued"
        assert (settings.runtime_dir / job["id"] / "sample.las").is_file()
    records = client.get("/api/clouds").json()["clouds"]
    assert len(records) == 2 and records[0]["id"] != records[1]["id"]
    assert client.delete(f"/api/clouds/{records[0]['id']}").status_code == 409


def test_raw_upload_compatibility_and_upload_errors(application):
    app, client, settings = application
    register(client)
    response = client.post("/api/conversions?filename=test.las&name=Raw", content=las_bytes())
    assert response.status_code == 202
    assert client.get(f"/api/conversions/{response.json()['id']}").status_code == 200
    assert client.post("/api/conversions?filename=test.exe&name=Bad", content=b"anything").status_code == 400
    assert client.post("/api/conversions?filename=bad.las&name=Bad", content=b"invalid").status_code == 400
    settings.max_upload_bytes = 2
    assert client.post("/api/conversions?filename=big.las&name=Big", content=las_bytes()).status_code == 413


def test_delete_preserves_derived_cloud_and_other_files(application):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    child_id, child_directory = seed_cloud(app, settings, owner, parent=uuid.UUID(identifier))
    assert client.delete(f"/api/clouds/{identifier}").status_code == 204
    assert not directory.exists() and child_directory.exists()
    child = client.get(f"/api/clouds/{child_id}").json()
    assert child["parentCloud"] == identifier
    assert client.get(f"/api/clouds/{identifier}").status_code == 404
    assert client.get(f"/api/clouds/{child_id}/download").status_code == 200
    with app.state.database.session() as session:
        assert session.get(Cloud, uuid.UUID(identifier)).status == "deleted"


@pytest.mark.parametrize("visibility", ["private", "public"])
def test_delete_retains_metadata_and_job_history_but_blocks_access(application, visibility):
    app, client, settings = application
    owner, owner_cookies = register(client)
    identifier, directory = seed_cloud(app, settings, owner, visibility=visibility, key="old-cloud-link")
    job_id, job_directory = seed_completed_job(app, settings, owner, identifier)
    metadata = client.get(f"/api/clouds/{identifier}").json()
    before_deletion = utcnow()
    assert client.delete(f"/api/clouds/{identifier}").status_code == 204
    assert not directory.exists() and not job_directory.exists()
    assert client.get("/api/clouds").json() == {"clouds": []}
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(identifier))
        assert cloud.status == "deleted" and cloud.deleted_at is not None
        assert before_deletion <= datetime.fromisoformat(iso_utc(cloud.deleted_at)) <= utcnow()
        assert cloud.name == metadata["name"] and str(cloud.owner_id) == owner["id"]
        assert cloud.source_file == metadata["sourceFile"] and cloud.size_bytes == metadata["sizeBytes"]
        assert iso_utc(cloud.created_at) == datetime.fromisoformat(metadata["createdAt"].replace("Z", "+00:00")).isoformat()
        job = session.get(ProcessingJob, uuid.UUID(job_id))
        assert job.status == "completed" and job.result == {"pointCount": 10}
        assert job.parameters == {"test": True} and job.log == "Konwersja zakończona"
    history = client.get(f"/api/jobs/{job_id}").json()
    assert history["pointCloudUrl"] is None
    assert client.get("/api/jobs").json()["jobs"] == [history]
    assert client.patch(f"/api/clouds/{identifier}", json={"name": "Przywrócona"}).status_code == 404
    assert client.delete(f"/api/clouds/{identifier}").status_code == 404
    identity = [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]
    for endpoint, parameters in [
        ("transformations", {"translation": [0, 0, 0], "rotationDegrees": [0, 0, 0], "scale": [1, 1, 1]}),
        ("segmentations", {"mode": "inside", "sourceToBoxMatrix": identity,
            "clipBox": {"matrixWorld": identity, "position": [0, 0, 0], "rotation": [0, 0, 0],
                        "scale": [1, 1, 1], "pointCloudMatrixWorld": identity, "pointCloudOffset": [0, 0, 0]}}),
        ("point-removals", {"selections": [{"position": [0, 0, 0], "radius": 1}]}),
    ]:
        assert client.post(f"/api/{endpoint}", json={"sourceCloud": identifier, "name": "Wynik", **parameters}).status_code == 404
    for cookies in (owner_cookies, register(client, "other@example.com")[1], {}):
        use_cookies(client, cookies)
        for path in (f"/api/clouds/{identifier}", "/api/clouds/old-cloud-link", f"/viewer?cloud={identifier}",
                     f"/api/clouds/{identifier}/files/metadata.json", "/pointclouds/old-cloud-link/octree.bin"):
            assert client.get(path).status_code == 404
        assert client.head(f"/api/clouds/{identifier}/files/octree.bin").status_code == 404
        assert client.get(f"/api/clouds/{identifier}/download").status_code == (404 if cookies else 401)
        assert client.get(f"/api/jobs/{job_id}").status_code == (200 if cookies == owner_cookies else 404 if cookies else 401)


def test_failed_file_deletion_keeps_record_and_can_be_retried(application, monkeypatch):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    def fail_removal(*_args):
        raise PermissionError("Plik jest zajęty")
    with monkeypatch.context() as patch:
        patch.setattr("backend.services.remove_directory", fail_removal)
        assert client.delete(f"/api/clouds/{identifier}").status_code == 409
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(identifier))
        assert cloud.status == "failed" and cloud.deleted_at is None
        assert "Plik jest zajęty" in cloud.error
    assert directory.exists() and len(client.get("/api/clouds").json()["clouds"]) == 1
    assert client.delete(f"/api/clouds/{identifier}").status_code == 204
    assert not directory.exists()
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(identifier))
        assert cloud.status == "deleted" and cloud.deleted_at is not None and cloud.error is None


def test_deleted_timestamp_blocks_access_even_if_status_is_changed(application):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner, visibility="public")
    job_id, _ = seed_completed_job(app, settings, owner, identifier)
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(identifier))
        cloud.deleted_at = utcnow()
        session.get(ProcessingJob, uuid.UUID(job_id)).status = "queued"
        session.commit()
    assert client.get("/api/clouds").json() == {"clouds": []}
    assert client.get(f"/api/clouds/{identifier}").status_code == 404
    assert client.get(f"/api/clouds/{identifier}/download").status_code == 404
    client.cookies.clear()
    assert client.get(f"/api/clouds/{identifier}/files/metadata.json").status_code == 404
    assert Worker(settings, app.state.database).claim_next() is None
    assert directory.exists()


def test_source_cannot_be_deleted_while_a_job_uses_it(application):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    response = client.post("/api/transformations", json={"sourceCloud": identifier, "name": "Wynik",
        "translation": [1, 2, 3], "rotationDegrees": [0, 0, 0], "scale": [1, 1, 1]})
    assert response.status_code == 202, response.text
    assert client.delete(f"/api/clouds/{identifier}").status_code == 409
    assert directory.exists()


def test_csrf_and_job_owner_checks(application):
    app, client, settings = application
    owner, _ = register(client)
    job = client.post("/api/conversions?filename=test.las&name=Raw", content=las_bytes()).json()
    assert client.post("/api/auth/logout", headers={"Origin": "https://other.example"}).status_code == 403
    register(client, "other@example.com")
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_legacy_import_is_idempotent_and_claimed_by_first_account(application):
    app, client, settings = application
    directory = settings.clouds_dir / "legacy-cloud"
    directory.mkdir()
    (directory / "cloud.js").write_text('{"version":"1.7"}')
    (directory / "cloud-info.json").write_text('{"name":"Stara chmura"}')
    owner, cookies = register(client)
    first = client.get("/api/clouds").json()["clouds"]
    assert len(first) == 1 and first[0]["name"] == "Stara chmura" and first[0]["visibility"] == "private"
    register(client, "second@example.com")
    assert client.get("/api/clouds").json() == {"clouds": []}
    from backend.services import import_legacy
    with app.state.database.session() as session:
        user = session.get(User, uuid.UUID(owner["id"]))
        assert import_legacy(session, settings, user) == 0
    assert (directory / "cloud.js").is_file()


def test_worker_recovers_interrupted_jobs_without_deleting_uploaded_source(application):
    app, client, settings = application
    register(client)
    payload = client.post("/api/conversions?filename=test.las&name=Recover", content=las_bytes()).json()
    worker = Worker(settings, app.state.database)
    identifier = worker.claim_next()
    with app.state.database.session() as session:
        job = session.get(ProcessingJob, identifier)
        cloud = session.get(Cloud, job.cloud_id)
        output = settings.clouds_dir / cloud.storage_key
        output.mkdir()
        (output / "partial.bin").write_bytes(b"partial")
    worker.recover()
    assert client.get(f"/api/jobs/{identifier}").json()["status"] == "queued"
    assert not output.exists()
    assert (settings.runtime_dir / payload["id"] / "test.las").is_file()


def test_database_and_sessions_survive_restart_and_incomplete_uploads_fail(application):
    app, client, settings = application
    _, cookies = register(client)
    queued = client.post("/api/conversions?filename=test.las&name=Queued", content=las_bytes()).json()
    interrupted = client.post("/api/conversions?filename=test.las&name=Interrupted", content=las_bytes()).json()
    with app.state.database.session() as session:
        session.get(ProcessingJob, uuid.UUID(interrupted["id"])).status = "uploading"
        session.get(Cloud, uuid.UUID(interrupted["cloudId"])).status = "uploading"
        session.commit()
    with TestClient(create_app(settings)) as restarted:
        use_cookies(restarted, cookies)
        assert restarted.get("/api/auth/me").status_code == 200
        assert restarted.get(f"/api/jobs/{queued['id']}").json()["status"] == "queued"
        assert restarted.get(f"/api/jobs/{interrupted['id']}").json()["status"] == "failed"
        assert len(restarted.get("/api/clouds").json()["clouds"]) == 2
    assert (settings.runtime_dir / queued["id"] / "test.las").exists()
    assert not (settings.runtime_dir / interrupted["id"]).exists()


@pytest.mark.parametrize("files_remaining", [True, False])
def test_restart_finishes_interrupted_deletion(application, files_remaining):
    app, client, settings = application
    owner, cookies = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    job_id, job_directory = seed_completed_job(app, settings, owner, identifier)
    if not files_remaining:
        from backend.storage import remove_directory
        remove_directory(settings.clouds_dir, directory.name)
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(identifier)).status = "deleting"
        session.commit()
    with TestClient(create_app(settings)) as restarted:
        use_cookies(restarted, cookies)
        assert restarted.get(f"/api/clouds/{identifier}").status_code == 404
        assert restarted.get(f"/api/jobs/{job_id}").status_code == 200
        assert restarted.get("/api/clouds").json() == {"clouds": []}
    assert not directory.exists() and not job_directory.exists()
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(identifier))
        deleted_at = cloud.deleted_at
        assert cloud.status == "deleted" and deleted_at is not None
    with TestClient(create_app(settings)):
        pass
    with app.state.database.session() as session:
        assert session.get(Cloud, uuid.UUID(identifier)).deleted_at == deleted_at


@pytest.mark.parametrize("status", ["deleting", "deleted"])
def test_worker_does_not_restart_jobs_of_removed_clouds(application, status):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    job_id, job_directory = seed_completed_job(app, settings, owner, identifier)
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(identifier))
        cloud.status = status
        cloud.deleted_at = utcnow() if status == "deleted" else None
        job = session.get(ProcessingJob, uuid.UUID(job_id))
        job.status = "converting"
        session.commit()
    worker = Worker(settings, app.state.database)
    worker.recover()
    with app.state.database.session() as session:
        assert session.get(Cloud, uuid.UUID(identifier)).status == status
        job = session.get(ProcessingJob, uuid.UUID(job_id))
        assert job.status == "converting"
        job.status = "queued"
        session.commit()
    assert worker.claim_next() is None
    worker.process(uuid.UUID(job_id))
    assert directory.exists() and (job_directory / "temporary.las").is_file()
    with app.state.database.session() as session:
        assert session.get(Cloud, uuid.UUID(identifier)).status == status


def test_worker_failure_is_persisted_and_partial_output_is_removed(application, monkeypatch):
    app, client, settings = application
    register(client)
    payload = client.post("/api/conversions?filename=test.las&name=Failed", content=las_bytes()).json()
    worker = Worker(settings, app.state.database)
    def fail(*_args, **_kwargs):
        raise ValueError("Testowana awaria konwertera")
    monkeypatch.setattr(worker, "run_command", fail)
    worker.process(worker.claim_next())
    job = client.get(f"/api/jobs/{payload['id']}").json()
    cloud = client.get(f"/api/clouds/{payload['cloudId']}").json()
    assert job["status"] == "failed" and cloud["status"] == "failed"
    assert "Testowana awaria" in job["error"]
    assert not (settings.clouds_dir / payload["cloudId"]).exists()


def test_stopping_worker_preserves_input_and_requeues_job(application, monkeypatch):
    app, client, settings = application
    register(client)
    payload = client.post("/api/conversions?filename=test.las&name=Interrupted", content=las_bytes()).json()
    worker = Worker(settings, app.state.database)
    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(worker, "run_command", interrupt)
    worker.process(worker.claim_next())
    assert worker.stopped()
    assert client.get(f"/api/jobs/{payload['id']}").json()["status"] == "queued"
    assert (settings.runtime_dir / payload["id"] / "test.las").is_file()


def test_owner_is_required_to_process_public_cloud_and_timestamps_include_timezone(application):
    app, client, settings = application
    owner, _ = register(client)
    identifier, _ = seed_cloud(app, settings, owner, visibility="public")
    metadata = client.get(f"/api/clouds/{identifier}").json()
    assert metadata["createdAt"].endswith(("Z", "+00:00"))
    register(client, "other@example.com")
    response = client.post("/api/point-removals", json={"sourceCloud": identifier, "name": "Not mine",
        "selections": [{"position": [0, 0, 0], "radius": 1}]})
    assert response.status_code == 404


def test_real_converter_and_processing_round_trip(application):
    app, client, settings = application
    if not settings.converter.is_file() or not settings.laszip.is_file():
        pytest.skip("Bundled converter and LASzip are needed for this integration test")
    register(client)
    upload = client.post("/api/clouds", data={"name": "Rzeczywista konwersja"},
                         files={"file": ("test.las", las_bytes(5000), "application/octet-stream")})
    assert upload.status_code == 202, upload.text
    worker = Worker(settings, app.state.database)
    worker.process(worker.claim_next())
    first_job = client.get(f"/api/jobs/{upload.json()['id']}").json()
    assert first_job["status"] == "completed", first_job
    assert client.get(first_job["pointCloudUrl"]).status_code == 200
    identifier = upload.json()["cloudId"]
    operations = [
        ("transformations", {"translation": [1, 0, 0], "rotationDegrees": [0, 0, 0], "scale": [1, 1, 1]}),
        ("segmentations", {"mode": "inside", "sourceToBoxMatrix": [.02,0,0,0,0,.02,0,0,0,0,.02,0,-.2,-.2,-.1,1],
            "clipBox": {"matrixWorld": [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1], "position": [0,0,0],
                "rotation": [0,0,0], "scale": [50,50,50], "pointCloudMatrixWorld": [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],
                "pointCloudOffset": [0,0,0]}}),
        ("point-removals", {"selections": [{"position": [0,0,0], "radius": .5}]}),
    ]
    for operation, parameters in operations:
        name = f"Łąka — {operation}"
        response = client.post(f"/api/{operation}", json={"name": name, "sourceCloud": identifier, **parameters})
        assert response.status_code == 202, response.text
        assert response.json()["filename"] == name + ".laz"
        worker.process(worker.claim_next())
        result = client.get(f"/api/jobs/{response.json()['id']}").json()
        assert result["status"] == "completed", result
        derived = client.get(f"/api/clouds/{result['cloudId']}").json()
        assert derived["parentCloud"] == identifier and derived["sourceAvailable"]
        assert derived["sourceFile"] == name + ".laz" and derived["downloadName"] == name + ".laz"
        downloaded = client.get(f"/api/clouds/{result['cloudId']}/download")
        assert downloaded.status_code == 200
        assert downloaded.headers["content-disposition"] == f"attachment; filename*=utf-8''{quote(name + '.laz')}"
        assert downloaded.content == (settings.clouds_dir / derived["slug"] / derived["sourceFile"]).read_bytes()
    response = client.post("/api/transformations", json={"name": "Kolejna obróbka łąki", "sourceCloud": derived["id"],
        "translation": [1, 0, 0], "rotationDegrees": [0, 0, 0], "scale": [1, 1, 1]})
    assert response.status_code == 202, response.text
    worker.process(worker.claim_next())
    chained = client.get(f"/api/jobs/{response.json()['id']}").json()
    assert chained["status"] == "completed", chained
