"""Bounded PLY header validation and a streaming PDAL import pipeline."""
from dataclasses import dataclass
from pathlib import Path
import shutil


MAX_HEADER_BYTES = 64 * 1024
SCALAR_TYPES = {
    "char", "uchar", "short", "ushort", "int", "uint", "float", "double",
    "int8", "uint8", "int16", "uint16", "int32", "uint32", "float32", "float64",
}


@dataclass(frozen=True)
class PlyHeader:
    vertex_count: int
    properties: dict[str, str]


def read_ply_header(data: bytes) -> PlyHeader:
    """Validate only a bounded header; PDAL validates point data in the worker."""
    lines = data[:MAX_HEADER_BYTES].splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != b"ply":
        raise ValueError("Plik nie ma prawidłowego nagłówka PLY.")
    encoding = None
    element = None
    count = None
    properties = {}
    for raw in lines[1:]:
        try:
            fields = raw.decode("ascii").split()
        except UnicodeDecodeError as error:
            raise ValueError("Nagłówek PLY musi być tekstem ASCII.") from error
        if not fields or fields[0] in ("comment", "obj_info"):
            continue
        if fields[0] == "end_header":
            if encoding is None or count is None or count <= 0 or not {"x", "y", "z"} <= properties.keys():
                raise ValueError("PLY musi zawierać punkty vertex ze współrzędnymi x, y, z.")
            colors = {name for name in ("red", "green", "blue") if name in properties}
            if colors and (len(colors) != 3 or any(
                    properties[name] not in ("uchar", "uint8", "ushort", "uint16") for name in colors)):
                raise ValueError("Kolory PLY wymagają pól red, green, blue typu uint8 lub uint16.")
            return PlyHeader(count, properties)
        if fields[0] == "format":
            if (encoding is not None or len(fields) != 3 or fields[2] != "1.0"
                    or fields[1] not in ("ascii", "binary_little_endian", "binary_big_endian")):
                raise ValueError("Obsługiwany jest PLY 1.0: ASCII oraz binary little/big endian.")
            encoding = fields[1]
        elif fields[0] == "element":
            if len(fields) != 3 or not fields[2].isdigit():
                raise ValueError("Nieprawidłowy element w nagłówku PLY.")
            element = fields[1]
            if element == "vertex":
                if count is not None:
                    raise ValueError("PLY zawiera powtórzony element vertex.")
                count = int(fields[2])
        elif fields[0] == "property" and element == "vertex":
            if len(fields) != 3 or fields[1] not in SCALAR_TYPES:
                raise ValueError("Punkty PLY muszą mieć skalarne właściwości liczbowe; listy nie są obsługiwane.")
            name = fields[2].lower()
            if name in properties:
                raise ValueError("PLY zawiera powtórzoną właściwość punktu.")
            properties[name] = fields[1]
    raise ValueError("Brak end_header w PLY lub nagłówek przekracza 64 KiB.")


def ply_pipeline(input_path: Path, output_path: Path, destination: Path) -> list[dict]:
    with input_path.open("rb") as stream:
        header = read_ply_header(stream.read(MAX_HEADER_BYTES))
    required = header.vertex_count * (40 + 8 * len(header.properties)) + 1024**3
    if shutil.disk_usage(destination).free < required:
        raise ValueError(f"Za mało miejsca na import PLY. Szacowane minimum: {required / 1024**3:.2f} GB.")
    pipeline = [{"type": "readers.ply", "filename": str(input_path)}]
    assignments = [f"{name.title()} = {name.title()} * 257" for name in ("red", "green", "blue")
                   if header.properties.get(name) in ("uchar", "uint8")]
    if assignments:
        # Promote 8-bit reader dimensions before multiplying, avoiding uint8 overflow.
        pipeline.append({"type": "filters.ferry", "dimensions": ",".join(
            f"=>{name.title()}" for name in ("red", "green", "blue")
            if header.properties.get(name) in ("uchar", "uint8"))})
        pipeline.append({"type": "filters.assign", "value": assignments})
    # The bundled PotreeConverter crashes on PDAL extra-byte dimensions (e.g. normals).
    # Keep standard LAS fields for processing; the unchanged PLY retains every property.
    pipeline.append({"type": "writers.las", "filename": str(output_path), "minor_version": 4,
        "dataformat_id": 7 if "red" in header.properties else 6,
        "scale_x": 0.001, "scale_y": 0.001, "scale_z": 0.001,
        "offset_x": "auto", "offset_y": "auto", "offset_z": "auto"})
    return pipeline
