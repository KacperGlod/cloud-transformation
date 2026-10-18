"""File operations stay outside database transactions and outside the static web root."""
import json
from pathlib import Path
import re
import shutil
import struct

from .formats import SOURCE_EXTENSIONS


def download_filename(name: str, source_filename: str) -> str:
    """Build a portable download name without changing the stored file's format."""
    extension = Path(source_filename).suffix.lower()
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name).strip(" .")
    if stem.lower().endswith(SOURCE_EXTENSIONS):
        stem = stem[:-4].rstrip(" .")
    stem = stem or "chmura"
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", stem.split(".")[0].rstrip(" "), re.IGNORECASE):
        stem = "_" + stem
    # Stay within common filesystem limits, including multibyte Unicode names.
    stem = stem.encode("utf-8")[:255 - len(extension.encode("utf-8"))].decode("utf-8", errors="ignore").rstrip(" .")
    return stem + extension


def child_path(base: Path, *parts: str) -> Path:
    base = base.resolve()
    target = base.joinpath(*parts).resolve()
    if target == base or base not in target.parents:
        raise ValueError("Ścieżka wychodzi poza katalog danych.")
    return target


def remove_directory(base: Path, name: str):
    target = child_path(base, name)
    if target.exists():
        shutil.rmtree(target)


def read_info(directory: Path) -> dict:
    try:
        data = json.loads((directory / "cloud-info.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def find_source(directory: Path, info: dict) -> str | None:
    declared = info.get("sourceFile")
    if isinstance(declared, str) and Path(declared).name == declared:
        candidate = child_path(directory, declared)
        if candidate.is_file() and candidate.suffix.lower() in SOURCE_EXTENSIONS:
            return candidate.name
    for candidate in directory.iterdir():
        if candidate.is_file() and not candidate.is_symlink() and candidate.suffix.lower() in (".las", ".laz"):
            return candidate.name
    return None


def validate_potree(directory: Path):
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("version") != "2.0":
        raise ValueError("Nieobsługiwana wersja danych Potree.")
    hierarchy = directory / "hierarchy.bin"
    octree = directory / "octree.bin"
    hierarchy_size = hierarchy.stat().st_size
    octree_size = octree.stat().st_size
    if not hierarchy_size or hierarchy_size % 22:
        raise ValueError("Plik hierarchy.bin ma nieprawidłowy rozmiar.")
    first_chunk = metadata.get("hierarchy", {}).get("firstChunkSize", 0)
    if not isinstance(first_chunk, int) or first_chunk <= 0 or first_chunk > hierarchy_size or first_chunk % 22:
        raise ValueError("Nieprawidłowy pierwszy fragment hierarchii Potree.")
    with hierarchy.open("rb") as stream:
        while block := stream.read(22 * 8192):
            for node_type, _mask, _points, offset, size in struct.iter_unpack("<BBIqq", block):
                if offset < 0 or size < 0 or node_type not in (0, 1, 2):
                    raise ValueError("Hierarchia Potree zawiera nieprawidłowy zakres danych.")
                limit = hierarchy_size if node_type == 2 else octree_size
                if offset + size > limit:
                    name = "hierarchy.bin" if node_type == 2 else "octree.bin"
                    raise ValueError(f"Plik {name} jest niekompletny. Sprawdź wolne miejsce na dysku.")


def ensure_conversion_space(input_path: Path, destination: Path):
    with input_path.open("rb") as stream:
        header = stream.read(375)
    if len(header) < 227 or header[:4] != b"LASF":
        raise ValueError("Plik nie ma prawidłowego nagłówka LAS/LAZ.")
    record_length = struct.unpack_from("<H", header, 105)[0]
    count = struct.unpack_from("<I", header, 107)[0]
    header_size = struct.unpack_from("<H", header, 94)[0]
    if header[24] == 1 and header[25] >= 4 and header_size >= 375 and len(header) >= 255:
        count = struct.unpack_from("<Q", header, 247)[0] or count
    estimated_size = count * record_length
    required = estimated_size + max(1024**3, estimated_size // 4)
    if shutil.disk_usage(destination).free < required:
        raise ValueError(f"Za mało wolnego miejsca na konwersję. Szacowane minimum: {required / 1024**3:.2f} GB.")
