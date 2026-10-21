"""Read bounded metadata and LAS headers without decoding point records."""
import json
import math
from pathlib import Path
import re
import struct
from typing import BinaryIO

from pyproj import CRS
from pyproj.exceptions import CRSError

MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_CRS_BYTES = 64 * 1024
MAX_RECORDS = 512
MAX_PROJECTION_BYTES = 1024 * 1024
UNKNOWN_REFERENCE = {"name": None, "authority": None, "horizontalUnit": None,
                     "verticalUnit": None, "source": None}
ATTRIBUTE_ALIASES = {"POSITION_CARTESIAN": "position", "COLOR_PACKED": "rgb", "RGB": "rgb",
                     "RGBA": "rgba", "INTENSITY": "intensity", "CLASSIFICATION": "classification"}
UNIT_NAMES = {9001: "metre", 9002: "foot", 9003: "US survey foot", 9101: "radian", 9102: "degree", 9105: "grad"}
PROJ_KEYS = {"proj", "lat_0", "lat_1", "lat_2", "lat_ts", "lon_0", "lonc", "alpha", "gamma",
             "k", "k_0", "x_0", "y_0", "zone", "south", "ellps", "datum", "towgs84", "units",
             "vunits", "to_meter", "vto_meter", "a", "b", "rf", "f", "R", "pm", "axis", "no_defs", "type"}


def vector(value: object) -> list[float] | None:
    try:
        if (isinstance(value, list) and len(value) == 3
                and all(isinstance(item, (int, float)) and not isinstance(item, bool)
                        and math.isfinite(item) for item in value)):
            return value
    except OverflowError:
        return None
    return None


def bounds(low: object, high: object) -> dict | None:
    minimum, maximum = vector(low), vector(high)
    if minimum is None or maximum is None or any(a > b for a, b in zip(minimum, maximum)):
        return None
    return {"min": minimum, "max": maximum}


def reference(value: str | int | None, source: str) -> dict:
    """Accept CRS definitions, never filenames/URLs accepted by generic PROJ input."""
    result = dict(UNKNOWN_REFERENCE)
    if value is None or len(str(value)) > MAX_CRS_BYTES:
        return result
    text = str(value).strip().strip("\0")
    try:
        if re.fullmatch(r"EPSG:\d{1,8}(?:\+\d{1,8})?", text, re.IGNORECASE):
            crs = CRS.from_user_input(text)
        elif re.match(r"^(?:PROJCS|GEOGCS|GEOCCS|VERT_CS|COMPD_CS|LOCAL_CS|PROJCRS|GEOGCRS|GEODCRS|VERTCRS|COMPOUNDCRS|BOUNDCRS|ENGCRS)\s*[\[(]", text, re.IGNORECASE):
            crs = CRS.from_wkt(text)
        elif text.startswith("+proj=") and all(
            re.fullmatch(r"\+[A-Za-z0-9_]+(?:=[A-Za-z0-9_.,+\-]+)?", token)
            and token[1:].split("=", 1)[0] in PROJ_KEYS for token in text.split()
        ):
            # Legacy Potree uses PROJ strings; exclude init/grid/file/pipeline inputs.
            crs = CRS.from_proj4(text)
        else:
            return result
        authority = crs.to_authority()
        axes = crs.axis_info
        result.update(name=crs.name[:512], authority=":".join(authority) if authority else None, source=source)
        if len(axes) >= 2 and axes[0].unit_name == axes[1].unit_name:
            result["horizontalUnit"] = axes[0].unit_name or None
        if len(axes) >= 3:
            result["verticalUnit"] = axes[2].unit_name or None
        elif crs.is_vertical and axes:
            result["verticalUnit"] = axes[0].unit_name or None
    except (CRSError, ValueError):
        pass
    return result


def projection_records(stream: BinaryIO, start: int, count: int, limit: int, *, extended: bool = False) -> dict[int, bytes]:
    records = {}
    if count > MAX_RECORDS or start < 0 or start > limit:
        raise ValueError("Nieprawidłowa lista rekordów układu współrzędnych.")
    stream.seek(start)
    header_size = 60 if extended else 54
    consumed = 0
    for _ in range(count):
        if stream.tell() + header_size > limit:
            raise ValueError("Niepełny rekord układu współrzędnych.")
        header = stream.read(header_size)
        if len(header) != header_size:
            raise ValueError("Niepełny rekord układu współrzędnych.")
        user = header[2:18].rstrip(b"\0 ")
        identifier = struct.unpack_from("<H", header, 18)[0]
        length = struct.unpack_from("<Q" if extended else "<H", header, 20)[0]
        end = stream.tell() + length
        if end > limit:
            raise ValueError("Rekord układu współrzędnych wychodzi poza plik.")
        if user == b"LASF_Projection" and identifier in (2112, 34735):
            consumed += length
            if length > MAX_CRS_BYTES or consumed > MAX_PROJECTION_BYTES:
                raise ValueError("Opis układu współrzędnych przekracza limit odczytu.")
            records[identifier] = stream.read(length)
        stream.seek(end)
    return records


def las_reference(records: dict[int, bytes]) -> dict:
    if 2112 in records:
        parsed = reference(records[2112].decode("utf-8", errors="replace"), "LAS WKT")
        if parsed["name"]:
            return parsed
    data = records.get(34735, b"")
    if len(data) < 8 or len(data) % 2:
        return dict(UNKNOWN_REFERENCE)
    directory = struct.unpack(f"<{len(data) // 2}H", data)
    count = directory[3]
    if len(directory) < 4 + count * 4:
        return dict(UNKNOWN_REFERENCE)
    keys = {}
    for index in range(count):
        key, location, size, value = directory[4 + index * 4:8 + index * 4]
        if location == 0 and size == 1:
            keys[key] = value
    horizontal = keys.get(3072) or keys.get(2048)
    vertical = keys.get(4096)
    valid = lambda code: code is not None and 0 < code < 32767
    authority = f"EPSG:{horizontal}" if valid(horizontal) else None
    if authority and valid(vertical):
        authority += f"+{vertical}"
    result = reference(authority, "LAS GeoTIFF")
    horizontal_unit = keys.get(3076) if 3072 in keys else keys.get(2054)
    if horizontal_unit in UNIT_NAMES:
        result["horizontalUnit"] = UNIT_NAMES[horizontal_unit]
    if keys.get(4099) in UNIT_NAMES:
        result["verticalUnit"] = UNIT_NAMES[keys[4099]]
    if any(result.values()):
        result["source"] = "LAS GeoTIFF"
    return result


def read_las_metadata(path: Path) -> dict:
    with path.open("rb") as stream:
        size = path.stat().st_size
        header = stream.read(375)
        if len(header) < 227 or header[:4] != b"LASF" or header[24] != 1:
            raise ValueError("Nieprawidłowy nagłówek LAS/LAZ.")
        header_size, point_offset, vlrs = struct.unpack_from("<HII", header, 94)
        if header_size < 227 or not header_size <= point_offset <= size:
            raise ValueError("Nieprawidłowy zakres nagłówka LAS/LAZ.")
        count = struct.unpack_from("<I", header, 107)[0]
        if header[25] >= 4 and header_size >= 375 and len(header) >= 375:
            count = struct.unpack_from("<Q", header, 247)[0] or count
        max_x, min_x, max_y, min_y, max_z, min_z = struct.unpack_from("<6d", header, 179)
        result = {"pointCount": count, "bounds": bounds([min_x, min_y, min_z], [max_x, max_y, max_z]),
                  "scale": vector(list(struct.unpack_from("<3d", header, 131))),
                  "offset": vector(list(struct.unpack_from("<3d", header, 155))),
                  "lasVersion": f"{header[24]}.{header[25]}",
                  "pointFormat": (header[104] & 63) if (header[104] & 63) <= 10 else None,
                  "reference": dict(UNKNOWN_REFERENCE), "notices": []}
        try:
            records = projection_records(stream, header_size, vlrs, point_offset)
            result["reference"] = las_reference(records)
            if header[25] >= 4 and header_size >= 375 and len(header) >= 375:
                evlr_start, evlr_count = struct.unpack_from("<QI", header, 235)
                if evlr_count:
                    if evlr_start < point_offset:
                        raise ValueError("Nieprawidłowy zakres EVLR.")
                    records.update(projection_records(stream, evlr_start, evlr_count, size, extended=True))
                    result["reference"] = las_reference(records)
        except (OSError, ValueError, struct.error):
            result["notices"].append("Nie udało się odczytać pełnego opisu układu współrzędnych z LAS/LAZ.")
        return result


def read_potree_metadata(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("Metadane Potree przekraczają limit odczytu.")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Nieprawidłowe metadane Potree.")
    attributes = []
    actual_bounds = None
    items = data.get("attributes") or data.get("pointAttributes") or []
    if not isinstance(items, list):
        raise ValueError("Nieprawidłowa lista atrybutów Potree.")
    for item in items[:256]:
        name = item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str):
            name = ATTRIBUTE_ALIASES.get(name, name)[:128]
            if name not in attributes:
                attributes.append(name)
            if name == "position" and isinstance(item, dict):
                actual_bounds = bounds(item.get("min"), item.get("max"))
    box = data.get("tightBoundingBox") or data.get("boundingBox") or {}
    if isinstance(box, dict):
        if "min" in box:
            box_bounds = bounds(box.get("min"), box.get("max"))
        else:
            box_bounds = bounds([box.get("lx"), box.get("ly"), box.get("lz")],
                                [box.get("ux"), box.get("uy"), box.get("uz")])
    else:
        box_bounds = None
    count = data.get("points")
    scale = data.get("scale")
    return {"pointCount": count if isinstance(count, int) and not isinstance(count, bool) and 0 <= count < 2**64 else None,
            "bounds": actual_bounds or box_bounds, "attributes": attributes or None,
            "scale": vector([scale] * 3 if isinstance(scale, (int, float)) else scale),
            "offset": vector(data.get("offset")), "reference": reference(data.get("projection"), "Potree")}
