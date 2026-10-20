"""PLY import tests use temporary storage and real native tools when installed."""
import json
import os
import struct
import subprocess
import uuid
from urllib.parse import quote

import pytest

from backend.models import Cloud, ProcessingJob
from backend.ply import MAX_HEADER_BYTES
from backend.worker import Worker
from test_api import application, register, use_cookies


def ply_bytes(encoding="ascii", *, colors="uchar", mesh=False, count=1000):
    header = ["ply", f"format {encoding} 1.0", f"element vertex {count}",
              "property double x", "property double y", "property double z"]
    if colors:
        header += [f"property {colors} {channel}" for channel in ("red", "green", "blue")]
    header += ["property ushort intensity", "property float nx"]
    if mesh:
        header += ["element face 1", "property list uchar int vertex_indices"]
    header += ["end_header"]
    points = []
    for index in range(count):
        values = [500000.125 + index % 10, 6000000.25 + index // 10 % 10, 100.375 + index // 100]
        if colors:
            values += [255, 128, 0] if colors == "uchar" else [65535, 32768, 0]
        values += [1000, 0.25]
        if encoding == "ascii":
            points.append((" ".join(map(str, values)) + "\n").encode("ascii"))
        else:
            color_format = "BBB" if colors == "uchar" else "HHH" if colors else ""
            points.append(struct.pack(("<" if encoding == "binary_little_endian" else ">") + "ddd" + color_format + "Hf", *values))
    if mesh:
        points.append(b"3 0 1 2\n" if encoding == "ascii" else struct.pack(("<" if encoding == "binary_little_endian" else ">") + "Biii", 3, 0, 1, 2))
    return ("\n".join(header) + "\n").encode("ascii") + b"".join(points)


def require_tools(settings):
    if not all(path.is_file() for path in (settings.pdal, settings.converter, settings.laszip)):
        pytest.skip("PDAL, PotreeConverter and LASzip are required for the real PLY round trip")


def upload_ply(client, content, *, multipart=False):
    if multipart:
        return client.post("/api/clouds", data={"name": "Łąka PLY"}, files={"file": ("Źródło.PLY", content)})
    return client.post("/api/conversions", params={"filename": "Źródło.PLY", "name": "Łąka PLY"}, content=content)


@pytest.mark.parametrize("content", [
    b"not a PLY", b"ply\nformat ascii 1.0\n",
    b"ply\nformat ascii 1.0\nelement vertex 0\nproperty float x\nproperty float y\nproperty float z\nend_header\n",
    b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nend_header\n0 0\n",
    ply_bytes().replace(b"format ascii 1.0", b"format ascii 2.0"),
    ply_bytes().replace(b"property float nx", b"property list uchar float nx"),
    b"ply\n" + b"comment long\n" * MAX_HEADER_BYTES,
], ids=["signature", "unfinished", "empty", "missing-z", "version", "vertex-list", "header-limit"])
def test_invalid_ply_headers_are_rejected(application, content, monkeypatch):
    app, client, settings = application
    register(client)
    monkeypatch.setenv("PDAL_PATH", str(settings.converter))
    response = upload_ply(client, content)
    assert response.status_code == 400, response.text
    assert not list(settings.runtime_dir.iterdir())
    assert all(cloud["status"] == "failed" for cloud in client.get("/api/clouds").json()["clouds"])


def test_missing_pdal_rejects_only_ply_before_creating_a_job(application, monkeypatch):
    app, client, settings = application
    register(client)
    monkeypatch.setenv("PDAL_PATH", str(settings.runtime_dir / "missing-pdal.exe"))
    assert not client.get("/api/health").json()["plyImportAvailable"]
    response = upload_ply(client, ply_bytes())
    assert response.status_code == 503 and "PDAL" in response.json()["error"]
    assert client.get("/api/clouds").json() == {"clouds": []}
    from test_api import las_bytes
    assert client.post("/api/conversions?filename=test.las&name=LAS", content=las_bytes()).status_code == 202


@pytest.mark.parametrize("encoding,colors,mesh", [
    ("ascii", "uchar", False), ("binary_little_endian", "uchar", True),
    ("binary_big_endian", "ushort", False), ("ascii", None, False),
])
def test_real_ply_import_preserves_original_points_attributes_and_access(application, encoding, colors, mesh):
    app, client, settings = application
    require_tools(settings)
    _, cookies = register(client)
    content = ply_bytes(encoding, colors=colors, mesh=mesh)
    response = upload_ply(client, content, multipart=encoding == "ascii")
    assert response.status_code == 202, response.text
    payload = response.json()
    worker = Worker(settings, app.state.database)
    worker.process(worker.claim_next())
    job = client.get(f"/api/jobs/{payload['id']}").json()
    assert job["status"] == "completed", job["error"] or job["log"]
    identifier = payload["cloudId"]
    cloud = client.get(f"/api/clouds/{identifier}").json()
    assert cloud["sourceAvailable"] and cloud["processingAvailable"]
    assert cloud["sourceFile"] == "Źródło.PLY" and cloud["sizeBytes"] == len(content)
    assert cloud["downloadName"] == "Łąka PLY.ply"
    directory = settings.clouds_dir / cloud["slug"]
    working = directory / "working.laz"
    assert working.is_file()
    download = client.get(f"/api/clouds/{identifier}/download")
    assert download.content == content
    assert download.headers["content-disposition"] == f"attachment; filename*=utf-8''{quote('Łąka PLY.ply')}"
    assert client.head(f"/api/clouds/{identifier}/download").headers["content-length"] == str(len(content))
    assert client.get(f"/api/clouds/{identifier}/download", headers={"Range": "bytes=0-19"}).content == content[:20]
    assert client.get(cloud["url"]).json()["points"] == 1000
    environment = dict(os.environ, PATH=str(settings.pdal.parent) + os.pathsep + os.environ.get("PATH", ""))
    result = subprocess.run([str(settings.pdal), "info", "--stats", str(working)],
                            env=environment, capture_output=True, text=True, encoding="utf-8", check=True)
    stats = {stat["name"]: stat for stat in json.loads(result.stdout)["stats"]["statistic"]}
    assert stats["X"]["minimum"] == pytest.approx(500000.125, abs=0.0005)
    assert stats["Y"]["minimum"] == pytest.approx(6000000.25, abs=0.0005)
    assert stats["Z"]["minimum"] == pytest.approx(100.375, abs=0.0005)
    assert stats["Intensity"]["minimum"] == 1000
    assert "NormalX" not in stats
    if colors:
        assert stats["Red"]["minimum"] == 65535
        assert stats["Green"]["minimum"] == (32896 if colors == "uchar" else 32768)
    client.patch(f"/api/clouds/{identifier}", json={"visibility": "public"})
    register(client, "other@example.com")
    for filename in ("Źródło.PLY", "working.laz", "WORKING.LAZ", "ply-import.log", "cloud-info.json"):
        assert client.get(f"/api/clouds/{identifier}/files/{quote(filename)}").status_code == 404
    assert client.get(f"/api/clouds/{identifier}/download").status_code == 404
    assert client.get(cloud["url"]).status_code == 200
    assert not client.get(f"/api/clouds/{identifier}").json()["processingAvailable"]
    use_cookies(client, cookies)
    # Availability for processing must not depend on retaining the original PLY.
    (directory / "Źródło.PLY").unlink()
    cloud = client.get(f"/api/clouds/{identifier}").json()
    assert not cloud["sourceAvailable"] and cloud["processingAvailable"]
    assert client.delete(f"/api/clouds/{identifier}").status_code == 204
    assert not directory.exists()
    with app.state.database.session() as session:
        deleted = session.get(Cloud, uuid.UUID(identifier))
        assert deleted.status == "deleted" and deleted.processing_file == "working.laz"


def test_real_ply_cloud_can_be_transformed_segmented_and_cleaned(application):
    app, client, settings = application
    require_tools(settings)
    register(client)
    payload = upload_ply(client, ply_bytes()).json()
    worker = Worker(settings, app.state.database)
    worker.process(worker.claim_next())
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    operations = [
        ("transformations", {"translation": [1, 0, 0], "rotationDegrees": [0, 0, 0], "scale": [1, 1, 1]}),
        ("segmentations", {"mode": "inside", "sourceToBoxMatrix": [.01,0,0,0,0,.01,0,0,0,0,.01,0,-5000,-60000,-1,1],
            "clipBox": {"matrixWorld": identity, "position": [0,0,0], "rotation": [0,0,0], "scale": [100,100,100],
                        "pointCloudMatrixWorld": identity, "pointCloudOffset": [0,0,0]}}),
        ("point-removals", {"selections": [{"position": [500000.125,6000000.25,100.375], "radius": .5}]}),
    ]
    for operation, parameters in operations:
        response = client.post(f"/api/{operation}", json={"name": "Wynik PLY", "sourceCloud": payload["cloudId"], **parameters})
        assert response.status_code == 202, response.text
        worker.process(worker.claim_next())
        job = client.get(f"/api/jobs/{response.json()['id']}").json()
        assert job["status"] == "completed", job["error"] or job["log"]
        cloud = client.get(f"/api/clouds/{job['cloudId']}").json()
        assert cloud["sourceFile"] == "Wynik PLY.laz" and cloud["processingAvailable"]
        assert client.get(f"/api/clouds/{job['cloudId']}/download").content.startswith(b"LASF")
    assert client.get(f"/api/clouds/{payload['cloudId']}/download").content == ply_bytes()


def test_ply_recovery_restores_both_files_after_they_were_moved(application, monkeypatch):
    app, client, settings = application
    register(client)
    monkeypatch.setenv("PDAL_PATH", str(settings.converter))
    payload = upload_ply(client, ply_bytes()).json()
    worker = Worker(settings, app.state.database)
    identifier = worker.claim_next()
    work = settings.runtime_dir / payload["id"]
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(payload["cloudId"]))
        output = settings.clouds_dir / cloud.storage_key
        output.mkdir()
        (work / cloud.source_file).rename(output / cloud.source_file)
        (output / cloud.processing_file).write_bytes(b"partial normalized file")
        session.get(ProcessingJob, identifier).status = "converting"
        session.commit()
    worker.recover()
    assert (work / "Źródło.PLY").read_bytes() == ply_bytes()
    assert (work / "working.laz").exists() and not output.exists()
    assert client.get(f"/api/jobs/{identifier}").json()["status"] == "queued"


def test_ply_conversion_failure_is_reported_and_partial_files_are_removed(application, monkeypatch):
    app, client, settings = application
    register(client)
    monkeypatch.setenv("PDAL_PATH", str(settings.converter))
    payload = upload_ply(client, ply_bytes()).json()
    worker = Worker(settings, app.state.database)
    def fail(*args):
        raise ValueError("Nieprawidłowe punkty PLY")
    monkeypatch.setattr(worker, "run_command", fail)
    worker.process(worker.claim_next())
    job = client.get(f"/api/jobs/{payload['id']}").json()
    assert job["status"] == "failed" and "Nieprawidłowe punkty PLY" in job["error"]
    work = settings.runtime_dir / payload["id"]
    assert [entry.name for entry in work.iterdir()] == ["Źródło.PLY"]
    assert (work / "Źródło.PLY").read_bytes() == ply_bytes()
    assert job["canRetry"]
    assert not (settings.clouds_dir / payload["cloudId"]).exists()


@pytest.mark.parametrize("fault", ["truncated", "non-numeric", "non-finite"])
def test_real_pdal_rejects_invalid_ply_point_data(application, fault):
    app, client, settings = application
    require_tools(settings)
    register(client)
    content = ply_bytes()
    if fault == "truncated":
        content = content[:content.index(b"end_header\n") + len(b"end_header\n")] + b"0 0\n"
    elif fault == "non-numeric":
        content = content.replace(b"500000.125", b"not-a-number", 1)
    else:
        content = content.replace(b"500000.125", b"nan", 1)
    response = upload_ply(client, content)
    assert response.status_code == 202, response.text
    worker = Worker(settings, app.state.database)
    worker.process(worker.claim_next())
    job = client.get(f"/api/jobs/{response.json()['id']}").json()
    assert job["status"] == "failed", job
    assert not (settings.clouds_dir / response.json()["cloudId"]).exists()


def test_ply_import_checks_disk_space_before_running_native_tools(application, monkeypatch):
    app, client, settings = application
    register(client)
    monkeypatch.setenv("PDAL_PATH", str(settings.converter))
    from collections import namedtuple
    usage = namedtuple("Usage", "total used free")(100, 100, 0)
    monkeypatch.setattr("backend.ply.shutil.disk_usage", lambda _: usage)
    response = upload_ply(client, ply_bytes())
    worker = Worker(settings, app.state.database)
    worker.process(worker.claim_next())
    job = client.get(f"/api/jobs/{response.json()['id']}").json()
    assert job["status"] == "failed" and "Za mało miejsca" in job["error"]
