"""Structured scan imports: coordinate transforms, strict input checks, and API round trips."""
import json
import os
import struct
import subprocess
import uuid
from urllib.parse import quote

import pytest

from backend.models import Cloud, ProcessingJob
from backend.ply import read_ply_header
from backend.scan_text import normalize_scan
from backend.worker import Worker
from test_api import application, register, use_cookies
from test_ply import require_tools


IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def ptx_block(points, matrix=IDENTITY):
    header = ["1", str(len(points)), "0 0 0", "1 0 0", "0 1 0", "0 0 1"]
    header += [" ".join(map(str, matrix[index:index + 4])) for index in range(0, 16, 4)]
    return "\n".join(header + points) + "\n"


def scan_bytes(extension, *, fields=7, count=1000):
    points = []
    for index in range(count):
        values = [1 + index % 10, 2 + index // 10 % 10, 3 + index // 100]
        if extension == ".ptx" and index == 0:
            values = [0, 0, 0]
        if fields in (4, 7):
            values += [-1000 if extension == ".pts" else 0.5]
        if fields in (6, 7):
            values += [255, 128, 0]
        points.append(" ".join(map(str, values)))
    if extension == ".pts":
        return (str(count) + "\n" + "\n".join(points) + "\n").encode("ascii")
    matrix = [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 500000, 6000000, 100, 1]
    return ptx_block(points, matrix).encode("ascii")


def normalized_records(path):
    content = path.read_bytes()
    header_end = content.index(b"end_header\n") + len(b"end_header\n")
    header = read_ply_header(content[:header_end])
    records = list(struct.iter_unpack("<dddH" + ("HHH" if "red" in header.properties else ""), content[header_end:]))
    assert len(records) == header.vertex_count
    return records


@pytest.mark.parametrize("point,expected", [
    ("0 0 0", (0, 0, 0, 0)),
    ("1 2 3", (1, 2, 3, 0)),
    ("1 2 3 -2048", (1, 2, 3, 0)),
    ("1 2 3 2047", (1, 2, 3, 4095)),
    ("1 2 3 255 128 0", (1, 2, 3, 0, 65535, 32896, 0)),
    ("1 2 3 0 255 128 0", (1, 2, 3, 2048, 65535, 32896, 0)),
])
def test_pts_column_layouts_and_attribute_mapping(tmp_path, point, expected):
    source, output = tmp_path / "Skan.pts", tmp_path / "working.ply"
    source.write_bytes(("\ufeff1\r\n" + point + "\r\n\r\n").encode("utf-8"))
    original = source.read_bytes()
    result = normalize_scan(source, output, lambda _: None)
    assert result == {"pointCount": 1, "inputPointCount": 1, "skippedPoints": 0, "scanCount": 1}
    assert normalized_records(output) == [expected]
    assert source.read_bytes() == original


def test_ptx_applies_each_scan_matrix_and_skips_missing_points_before_transform(tmp_path):
    source, output = tmp_path / "Stanowiska.ptx", tmp_path / "working.ply"
    rotation = [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 10, 20, 30, 1]
    translation = [*IDENTITY[:12], 100, -200, 5, 1]
    source.write_text(ptx_block(["1 2 3 0.5 255 0 0", "0 0 0 0 0 0 0"], rotation)
                      + "\n" + ptx_block(["-1 2 3 1 0 128 255"], translation), encoding="ascii")
    result = normalize_scan(source, output, lambda _: None)
    assert result == {"pointCount": 2, "inputPointCount": 3, "skippedPoints": 1, "scanCount": 2}
    assert normalized_records(output) == [(8, 21, 33, 2048, 65535, 0, 0), (99, -198, 8, 4096, 0, 32896, 65535)]


@pytest.mark.parametrize("extension,content", [
    (".pts", b"2\n1 2 3\n"),
    (".pts", b"1\n1 2 3\n4 5 6\n"),
    (".pts", b"2\n1 2 3\n1 2 invalid\n"),
    (".pts", b"2\n1 2 3\nnan 2 3\n"),
    (".pts", b"2\n1 2 3\n1 2 3 4\n"),
    (".pts", b"1\n1 2 3 3000\n"),
    (".pts", b"1\n1 2 3 0 256 0 0\n"),
    (".pts", b"1\n1 2 3 0 1.5 0 0\n"),
    (".ptx", ptx_block(["1 2 3 0.5", "1 2 3 inf"]).encode()),
    (".ptx", ptx_block(["1 2 3 1.5"]).encode()),
    (".ptx", ptx_block(["0 0 0 0"]).encode()),
    (".ptx", ptx_block(["1 2 3 0.5"]).replace("1\n1\n", "1\n2\n", 1).encode()),
    (".ptx", (ptx_block(["1 2 3 0.5"]) + ptx_block(["1 2 3 0.5 0 0 0"])).encode()),
], ids=["truncated", "extra", "invalid-number", "nan", "mixed-columns", "intensity", "rgb-range",
        "rgb-fraction", "ptx-inf", "ptx-intensity", "ptx-empty", "ptx-truncated", "ptx-mixed"])
def test_invalid_scan_data_fails_without_preserving_partial_normalization(tmp_path, extension, content):
    source, output = tmp_path / ("source" + extension), tmp_path / "working.ply"
    source.write_bytes(content)
    with pytest.raises(ValueError):
        normalize_scan(source, output, lambda _: None)
    assert source.read_bytes() == content and not output.exists()


def test_scan_normalization_processes_multiple_chunks_and_reports_progress(tmp_path):
    source, output = tmp_path / "large.pts", tmp_path / "working.ply"
    source.write_bytes(b"17000\n" + b"1 2 3 0 255 128 0\n" * 17000)
    progress = []
    result = normalize_scan(source, output, progress.append)
    assert result["pointCount"] == 17000 and len(normalized_records(output)) == 17000
    assert len(progress) >= 3 and progress[-1] == "Przygotowanie skanu: 100%"


@pytest.mark.parametrize("extension,content", [
    (".pts", b"-1\n1 2 3\n"), (".pts", b"0\n"), (".pts", b"hello\n1 2 3\n"),
    (".ptx", b"1\n1\n"),
    (".ptx", ptx_block(["1 2 3 0.5"], [0] * 16).encode()),
    (".ptx", ptx_block(["1 2 3 0.5"], [*IDENTITY[:15], float("nan")]).encode()),
], ids=["negative", "zero", "not-count", "ptx-header", "ptx-matrix", "ptx-nan-matrix"])
def test_api_rejects_invalid_scan_headers(application, monkeypatch, extension, content):
    app, client, settings = application
    register(client)
    monkeypatch.setenv("PDAL_PATH", str(settings.converter))
    response = client.post("/api/conversions", params={"filename": "bad" + extension, "name": "Bad"}, content=content)
    assert response.status_code == 400, response.text
    assert not list(settings.runtime_dir.iterdir())


@pytest.mark.parametrize("extension", [".pts", ".ptx"])
def test_missing_pdal_rejects_scan_upload_before_creating_job(application, monkeypatch, extension):
    app, client, settings = application
    register(client)
    monkeypatch.setenv("PDAL_PATH", str(settings.runtime_dir / "missing-pdal.exe"))
    assert not client.get("/api/health").json()[extension[1:] + "ImportAvailable"]
    response = client.post("/api/conversions", params={"filename": "scan" + extension, "name": "Scan"},
                           content=scan_bytes(extension))
    assert response.status_code == 503
    assert client.get("/api/clouds").json() == {"clouds": []}


@pytest.mark.parametrize("extension,fields", [(".pts", 3), (".pts", 7), (".ptx", 4), (".ptx", 7)])
def test_real_scan_import_download_processing_and_deletion(application, extension, fields):
    app, client, settings = application
    require_tools(settings)
    _, cookies = register(client)
    filename, name = "Źródło" + extension.upper(), "Skan " + extension[1:].upper()
    content = scan_bytes(extension, fields=fields)
    response = client.post("/api/clouds", data={"name": name}, files={"file": (filename, content)})
    assert response.status_code == 202, response.text
    payload = response.json()
    worker = Worker(settings, app.state.database)
    worker.process(worker.claim_next())
    job = client.get(f"/api/jobs/{payload['id']}").json()
    assert job["status"] == "completed", job["error"] or job["log"]
    identifier = payload["cloudId"]
    cloud = client.get(f"/api/clouds/{identifier}").json()
    directory = settings.clouds_dir / cloud["slug"]
    assert cloud["sourceFile"] == filename and cloud["processingAvailable"] and cloud["sourceAvailable"]
    assert cloud["sizeBytes"] == len(content) and cloud["downloadName"] == name + extension
    assert client.get(cloud["url"]).json()["points"] == (1000 if extension == ".pts" else 999)
    with app.state.database.session() as session:
        result = session.get(ProcessingJob, uuid.UUID(payload["id"])).result
        assert result["skippedPoints"] == (0 if extension == ".pts" else 1)
    assert not (directory / "normalized.ply").exists()
    environment = dict(os.environ, PATH=str(settings.pdal.parent) + os.pathsep + os.environ.get("PATH", ""))
    native = subprocess.run([str(settings.pdal), "info", "--stats", str(directory / "working.laz")],
                            env=environment, capture_output=True, text=True, encoding="utf-8", check=True)
    stats = {item["name"]: item for item in json.loads(native.stdout)["stats"]["statistic"]}
    assert stats["X"]["minimum"] == (1 if extension == ".pts" else 499989)
    assert stats["Y"]["minimum"] == (2 if extension == ".pts" else 6000001)
    if fields == 7:
        assert stats["Red"]["minimum"] == 65535 and stats["Green"]["minimum"] == 32896
    assert stats["Intensity"]["minimum"] == (1048 if extension == ".pts" and fields == 7 else 2048 if extension == ".ptx" else 0)
    url = f"/api/clouds/{identifier}/download"
    assert client.get(url).content == content
    assert client.head(url).headers["content-length"] == str(len(content))
    assert client.get(url, headers={"Range": "bytes=0-19"}).content == content[:20]
    renamed = client.patch(f"/api/clouds/{identifier}", json={"name": "Nowy skan" + extension.upper(), "visibility": "public"})
    assert renamed.json()["downloadName"] == "Nowy skan" + extension
    assert client.get(url).headers["content-disposition"] == f"attachment; filename*=utf-8''{quote('Nowy skan' + extension)}"
    register(client, "other@example.com")
    for asset in (filename, "WORKING.LAZ", "scan-import.log"):
        assert client.get(f"/api/clouds/{identifier}/files/{quote(asset)}").status_code == 404
    assert client.get(url).status_code == 404 and client.get(cloud["url"]).status_code == 200
    use_cookies(client, cookies)
    operations = [
        ("transformations", {"translation": [1, 0, 0], "rotationDegrees": [0, 0, 0], "scale": [1, 1, 1]}),
        ("point-removals", {"selections": [{"position": [1, 2, 3] if extension == ".pts"
                                           else [499998, 6000002, 103], "radius": .1}]}),
        ("segmentations", {"mode": "outside", "sourceToBoxMatrix": IDENTITY,
            "clipBox": {"matrixWorld": IDENTITY, "position": [0,0,0], "rotation": [0,0,0], "scale": [1,1,1],
                        "pointCloudMatrixWorld": IDENTITY, "pointCloudOffset": [0,0,0]}}),
    ]
    # Exercise the complete processing path for each source format, including colorless LAZ.
    for operation, parameters in operations:
        processed = client.post(f"/api/{operation}", json={"sourceCloud": identifier, "name": "Wynik skanu", **parameters})
        assert processed.status_code == 202, processed.text
        worker.process(worker.claim_next())
        result = client.get(f"/api/jobs/{processed.json()['id']}").json()
        assert result["status"] == "completed", result["error"] or result["log"]
        assert client.get(f"/api/clouds/{result['cloudId']}/download").content.startswith(b"LASF")
    assert client.get(url).content == content
    assert client.delete(f"/api/clouds/{identifier}").status_code == 204 and not directory.exists()
    with app.state.database.session() as session:
        assert session.get(Cloud, uuid.UUID(identifier)).status == "deleted"


@pytest.mark.parametrize("extension", [".pts", ".ptx"])
def test_worker_recovers_scan_preparation_and_keeps_original(application, monkeypatch, extension):
    app, client, settings = application
    register(client)
    monkeypatch.setenv("PDAL_PATH", str(settings.converter))
    content = scan_bytes(extension)
    payload = client.post("/api/conversions", params={"filename": "scan" + extension, "name": "Recover"}, content=content).json()
    worker = Worker(settings, app.state.database)
    identifier = worker.claim_next()
    work = settings.runtime_dir / payload["id"]
    (work / "normalized.ply").write_bytes(b"partial")
    with app.state.database.session() as session:
        session.get(ProcessingJob, identifier).status = "normalizing"
        session.commit()
    worker.recover()
    assert client.get(f"/api/jobs/{identifier}").json()["status"] == "queued"
    assert (work / ("scan" + extension)).read_bytes() == content


def test_scan_preparation_checks_disk_space_before_creating_output(tmp_path, monkeypatch):
    from collections import namedtuple
    source, output = tmp_path / "scan.pts", tmp_path / "working.ply"
    source.write_bytes(scan_bytes(".pts"))
    usage = namedtuple("Usage", "total used free")(100, 100, 0)
    monkeypatch.setattr("backend.scan_text.shutil.disk_usage", lambda _: usage)
    with pytest.raises(ValueError, match="Za mało miejsca"):
        normalize_scan(source, output, lambda _: None)
    assert not output.exists()


@pytest.mark.parametrize("extension", [".pts", ".ptx"])
def test_worker_reports_late_scan_errors_and_cleans_partial_output(application, extension):
    app, client, settings = application
    require_tools(settings)
    register(client)
    content = scan_bytes(extension).rsplit(b"\n", 2)[0] + b"\ninvalid point\n"
    response = client.post("/api/conversions", params={"filename": "bad" + extension, "name": "Bad scan"}, content=content)
    assert response.status_code == 202, response.text
    worker = Worker(settings, app.state.database)
    worker.process(worker.claim_next())
    payload = response.json()
    job = client.get(f"/api/jobs/{payload['id']}").json()
    assert job["status"] == "failed" and "wiersz" in job["error"]
    assert not (settings.clouds_dir / payload["cloudId"]).exists()
    work = settings.runtime_dir / payload["id"]
    assert [entry.name for entry in work.iterdir()] == ["bad" + extension]
    assert (work / ("bad" + extension)).read_bytes() == content
    assert job["canRetry"]
