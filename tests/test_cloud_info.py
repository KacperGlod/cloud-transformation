import io
import json
import struct
import uuid

from pyproj import CRS
import pytest

from backend.cloud_metadata import MAX_METADATA_BYTES, projection_records, read_las_metadata, read_potree_metadata, reference
from backend.models import Cloud, utcnow
from test_api import application, las_bytes, register, seed_cloud, use_cookies


def las_with_records(*records):
    data = bytearray(las_bytes())
    vlrs = b"".join(struct.pack("<H16sHH32s", 0, b"LASF_Projection", identifier, len(payload), b"CRS") + payload
                    for identifier, payload in records)
    struct.pack_into("<II", data, 96, 227 + len(vlrs), len(records))
    return data[:227] + vlrs + data[227:]


def test_header_geometry_wkt_and_actual_viewer_attributes(application):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    (directory / "original.las").write_bytes(las_with_records((2112, CRS.from_epsg(2180).to_wkt().encode() + b"\0")))
    (directory / "metadata.json").write_text(json.dumps({"points": 123,
        "boundingBox": {"min": [0, 0, 0], "max": [100, 100, 100]},
        "attributes": [{"name": name} for name in ("position", "rgb", "intensity", "classification")]}))
    response = client.get(f"/api/clouds/{identifier}/info")
    assert response.status_code == 200, response.text
    info = response.json()
    assert info["pointCount"] == 10
    assert info["bounds"] == {"min": [0, 0, 0], "max": [9, 0, 0]}
    assert info["scale"] == [.001] * 3
    assert info["reference"]["authority"] == "EPSG:2180"
    assert info["reference"]["horizontalUnit"] == "metre"
    assert info["reference"]["verticalUnit"] is None  # A 2D CRS does not define Z units.
    assert info["attributes"] == ["position", "rgb", "intensity", "classification"]
    assert info["sourceFormat"] == "LAS"
    assert info["notices"] == []


def test_geotiff_and_las14_extended_count_and_wkt(tmp_path):
    path = tmp_path / "input.laz"
    keys = struct.pack("<16H", 1, 1, 0, 3, 3072, 0, 1, 2180, 3076, 0, 1, 9001, 4099, 0, 1, 9001)
    path.write_bytes(las_with_records((34735, keys)))
    metadata = read_las_metadata(path)
    assert metadata["reference"]["authority"] == "EPSG:2180"
    assert metadata["reference"]["verticalUnit"] == "metre"
    assert metadata["reference"]["source"] == "LAS GeoTIFF"

    wkt = CRS.from_epsg(4979).to_wkt().encode() + b"\0"
    header = bytearray(las_bytes()[:227]) + bytearray(148)
    header[25] = 4
    struct.pack_into("<HI", header, 94, 375, 375)
    struct.pack_into("<I", header, 107, 0)
    struct.pack_into("<QI", header, 235, 375, 1)
    struct.pack_into("<Q", header, 247, 5_000_000_000)
    path.write_bytes(header + struct.pack("<H16sHQ32s", 0, b"LASF_Projection", 2112, len(wkt), b"CRS") + wkt)
    metadata = read_las_metadata(path)
    assert metadata["pointCount"] == 5_000_000_000
    assert metadata["reference"]["authority"] == "EPSG:4979"
    assert metadata["reference"]["horizontalUnit"] == "degree"
    assert metadata["reference"]["verticalUnit"] == "metre"


def test_normalized_import_uses_working_header_and_original_format(application):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    (directory / "working.laz").write_bytes(las_bytes(20))
    (directory / "scan.ply").write_text("preserved original")
    with app.state.database.session() as session:
        cloud = session.get(Cloud, uuid.UUID(identifier))
        cloud.source_file, cloud.processing_file = "scan.ply", "working.laz"
        session.commit()
    info = client.get(f"/api/clouds/{identifier}/info").json()
    assert info["sourceFormat"] == "PLY"
    assert info["cloud"]["sourceFile"] == "scan.ply"
    assert info["pointCount"] == 20
    assert info["reference"]["name"] is None


def test_partial_las_units_do_not_hide_a_recognized_potree_crs(application):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    (directory / "metadata.json").write_text(json.dumps({"projection": "EPSG:2180"}))
    keys = struct.pack("<8H", 1, 1, 0, 1, 4099, 0, 1, 9001)
    (directory / "original.las").write_bytes(las_with_records((34735, keys)))
    info = client.get(f"/api/clouds/{identifier}/info").json()
    assert info["reference"]["authority"] == "EPSG:2180"
    assert info["reference"]["horizontalUnit"] == "metre"
    assert info["reference"]["verticalUnit"] == "metre"


def test_information_access_and_private_provenance(application):
    app, client, settings = application
    owner, owner_cookies = register(client)
    source, _ = seed_cloud(app, settings, owner)
    identifier, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(source), visibility="public")
    operation = {"type": "transformation", "translation": [1, 2, 3], "result": {"outputPoints": 10}}
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(source)).name = "Private source name"
        session.get(Cloud, uuid.UUID(identifier)).operation = operation
        session.commit()
    info = client.get(f"/api/clouds/{identifier}/info").json()
    assert info["source"]["name"] == "Private source name"
    assert info["cloud"]["operation"] == operation
    client.cookies.clear()
    assert client.get(f"/api/clouds/{source}/info").status_code == 404
    public = client.get(f"/api/clouds/{identifier}/info")
    assert public.status_code == 200
    assert public.json()["source"] is None
    assert public.json()["cloud"]["operation"] is None
    assert public.json()["cloud"]["sourceFile"] is None
    assert "Private source name" not in public.text
    assert "translation" not in public.text
    register(client, "stranger@example.com")
    assert client.get(f"/api/clouds/{source}/info").status_code == 404
    assert client.get(f"/api/clouds/{identifier}/info").json()["source"] is None

    use_cookies(client, owner_cookies)
    with app.state.database.session() as session:
        parent = session.get(Cloud, uuid.UUID(source))
        parent.status, parent.deleted_at = "deleted", utcnow()
        session.commit()
    info = client.get(f"/api/clouds/{identifier}/info").json()
    assert info["source"]["status"] == "deleted"
    assert info["source"]["viewerUrl"] is None
    assert client.get(f"/api/clouds/{source}/info").status_code == 404


def test_legacy_fallback_and_pending_or_corrupt_metadata(application):
    app, client, settings = application
    owner, _ = register(client)
    identifier, directory = seed_cloud(app, settings, owner)
    legacy = {"points": 42, "pointAttributes": ["POSITION_CARTESIAN", "COLOR_PACKED"], "scale": .01,
              "tightBoundingBox": {"lx": 1, "ly": 2, "lz": 3, "ux": 4, "uy": 5, "uz": 6}, "projection": "EPSG:2180"}
    (directory / "cloud.js").write_text(json.dumps(legacy))
    (directory / "original.las").unlink()
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(identifier)).entry_file = "cloud.js"
        session.commit()
    info = client.get(f"/api/clouds/{identifier}/info").json()
    assert info["pointCount"] == 42
    assert info["attributes"] == ["position", "rgb"]
    assert info["bounds"] == {"min": [1, 2, 3], "max": [4, 5, 6]}
    assert info["reference"]["authority"] == "EPSG:2180"
    assert info["notices"]

    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(identifier)).status = "converting"
        session.commit()
    info = client.get(f"/api/clouds/{identifier}/info").json()
    assert info["pointCount"] is None  # Never read live/incomplete worker output.
    assert info["bounds"] is None
    assert info["notices"]
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(identifier)).status = "ready"
        session.commit()
    (directory / "cloud.js").write_text('{"attributes": {"bad": true}}')
    response = client.get(f"/api/clouds/{identifier}/info")
    assert response.status_code == 200
    assert response.json()["pointCount"] is None
    assert response.json()["notices"]


def test_bounded_parsing_and_untrusted_projection(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_bytes(b" " * (MAX_METADATA_BYTES + 1))
    with pytest.raises(ValueError):
        read_potree_metadata(path)
    huge_record = struct.pack("<H16sHQ32s", 0, b"LASF_Projection", 2112, 2**63, b"CRS")
    with pytest.raises(ValueError):
        projection_records(io.BytesIO(huge_record), 0, 1, len(huge_record), extended=True)
    with pytest.raises(ValueError):
        projection_records(io.BytesIO(b""), 0, 1_000_000, 0)
    for projection in ["https://example.test/crs", "C:/secret.txt", "+proj=utm +zone=34 +init=secret", "+proj=longlat +nadgrids=secret"]:
        assert reference(projection, "Potree")["name"] is None
    assert reference("+proj=utm +zone=34 +datum=WGS84 +units=m +no_defs", "Potree")["authority"] == "EPSG:32634"
    path.write_text(json.dumps({"attributes": [{"name": "position", "min": [5, 6, 7], "max": [8, 9, 10]}],
        "boundingBox": {"min": [0, 0, 0], "max": [100, 100, 100]}}))
    assert read_potree_metadata(path)["bounds"] == {"min": [5, 6, 7], "max": [8, 9, 10]}


def test_information_lists_only_actual_memberships_and_reflects_changes(application):
    app, client, settings = application
    owner, _ = register(client)
    cloud, _ = seed_cloud(app, settings, owner)
    other, _ = seed_cloud(app, settings, owner, parent=uuid.UUID(cloud))
    route = f"/api/clouds/{cloud}/info"
    assert client.get(route).json()["collections"] == []
    first = client.post("/api/collections", json={"name": "Most Kraków", "cloudIds": [cloud]}).json()
    second = client.post("/api/collections", json={"name": "Inwentaryzacja", "cloudIds": [cloud, other]}).json()
    unrelated = client.post("/api/collections", json={"name": "Tylko wynik", "cloudIds": [other]}).json()
    assert client.get(route).json()["collections"] == [
        {"id": first["id"], "name": first["name"]}, {"id": second["id"], "name": second["name"]}]
    assert unrelated["id"] not in {item["id"] for item in client.get(route).json()["collections"]}
    client.patch(f"/api/collections/{first['id']}", json={"name": "Nowa nazwa"})
    assert client.get(route).json()["collections"][0]["name"] == "Nowa nazwa"
    client.delete(f"/api/collections/{first['id']}/clouds/{cloud}")
    assert client.get(route).json()["collections"] == [{"id": second["id"], "name": second["name"]}]
    client.delete(f"/api/collections/{second['id']}")
    assert client.get(route).json()["collections"] == []


def test_information_hides_collection_names_and_ids_on_public_clouds(application):
    app, client, settings = application
    owner, owner_cookies = register(client)
    cloud, _ = seed_cloud(app, settings, owner, visibility="public")
    collection = client.post("/api/collections", json={"name": "Prywatna kolekcja", "cloudIds": [cloud]}).json()
    route = f"/api/clouds/{cloud}/info"
    assert client.get(route).json()["collections"] == [{"id": collection["id"], "name": collection["name"]}]
    client.cookies.clear()
    public = client.get(route)
    assert public.status_code == 200 and public.json()["collections"] is None
    assert collection["name"] not in public.text and collection["id"] not in public.text
    register(client, "collection-info-other@example.test")
    foreign = client.get(route)
    assert foreign.json()["collections"] is None
    assert collection["name"] not in foreign.text and collection["id"] not in foreign.text
    use_cookies(client, owner_cookies)
    with app.state.database.session() as session:
        session.get(Cloud, uuid.UUID(cloud)).status = "queued"
        session.commit()
    assert client.get(route).json()["collections"] == [{"id": collection["id"], "name": collection["name"]}]
