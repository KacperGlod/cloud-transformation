"""Source format capabilities and bounded validation shared by uploads and storage."""
from .ply import MAX_HEADER_BYTES, read_ply_header
from .scan_text import validate_scan_header


NORMALIZED_EXTENSIONS = (".ply", ".pts", ".ptx")
SOURCE_EXTENSIONS = (".las", ".laz", *NORMALIZED_EXTENSIONS)


def validate_source_header(data: bytes, extension: str) -> None:
    if extension == ".ply":
        read_ply_header(data)
    elif extension in (".pts", ".ptx"):
        validate_scan_header(data, extension)
    elif extension in (".las", ".laz"):
        if len(data) < 227 or data[:4] != b"LASF":
            raise ValueError("Plik nie ma prawidłowego nagłówka LAS/LAZ.")
    else:
        raise ValueError("Nieobsługiwany format chmury.")
