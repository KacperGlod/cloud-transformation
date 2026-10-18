"""Read PTS/PTX incrementally and normalize them to a temporary binary PLY."""
import argparse
from dataclasses import dataclass
import io
import itertools
import json
import math
from pathlib import Path
import shutil
import struct


MAX_LINE_BYTES = 64 * 1024
CHUNK_POINTS = 8192


class ScanLines:
    def __init__(self, stream):
        self.stream = stream
        self.number = 0

    def fields(self, *, skip_blank=False, eof=False) -> list[str] | None:
        while True:
            raw = self.stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                if eof:
                    return None
                raise self.error("Plik zakończył się przed oczekiwaną liczbą punktów lub końcem nagłówka.")
            self.number += 1
            if len(raw) > MAX_LINE_BYTES:
                raise self.error("Wiersz przekracza 64 KiB.")
            if self.number == 1:
                raw = raw.removeprefix(b"\xef\xbb\xbf")
            try:
                fields = raw.decode("ascii").split()
            except UnicodeDecodeError as error:
                raise self.error("Dane PTS/PTX muszą być liczbami zapisanymi w ASCII.") from error
            if fields or not skip_blank:
                return fields

    def error(self, message: str) -> ValueError:
        return ValueError(f"PTS/PTX, wiersz {self.number}: {message}")


@dataclass
class ScanStats:
    scans: int = 0
    input_points: int = 0
    skipped_points: int = 0


def positive_count(fields: list[str], lines: ScanLines) -> int:
    if len(fields) != 1 or not fields[0].isascii() or not fields[0].isdigit() or len(fields[0]) > 18:
        raise lines.error("Oczekiwano dodatniej całkowitej liczby punktów, kolumn lub wierszy.")
    count = int(fields[0])
    if count <= 0:
        raise lines.error("Liczba punktów, kolumn lub wierszy musi być dodatnia.")
    return count


def numbers(fields: list[str], lines: ScanLines, count: int | None = None) -> list[float]:
    if count is not None and len(fields) != count:
        raise lines.error(f"Oczekiwano {count} wartości, otrzymano {len(fields)}.")
    try:
        values = [float(field) for field in fields]
    except ValueError as error:
        raise lines.error("Wiersz zawiera wartość, która nie jest liczbą.") from error
    if not all(math.isfinite(value) for value in values):
        raise lines.error("Wartości muszą być skończone; NaN i Infinity nie są obsługiwane.")
    return values


def ptx_header(lines: ScanLines, first: list[str]) -> tuple[int, list[float]]:
    columns = positive_count(first, lines)
    rows = positive_count(lines.fields(), lines)
    for _ in range(4):
        numbers(lines.fields(), lines, 3)  # Scanner position and axes; the 4x4 matrix is authoritative.
    matrix = list(itertools.chain.from_iterable(numbers(lines.fields(), lines, 4) for _ in range(4)))
    if any(abs(matrix[index]) > 1e-12 for index in (3, 7, 11)) or abs(matrix[15] - 1) > 1e-12:
        raise lines.error("Macierz PTX musi opisywać transformację afiniczną 4×4.")
    determinant = (matrix[0] * (matrix[5] * matrix[10] - matrix[6] * matrix[9])
                   - matrix[4] * (matrix[1] * matrix[10] - matrix[2] * matrix[9])
                   + matrix[8] * (matrix[1] * matrix[6] - matrix[2] * matrix[5]))
    if not math.isfinite(determinant) or determinant == 0:
        raise lines.error("Macierz PTX jest osobliwa lub ma zbyt duże wartości.")
    return columns * rows, matrix


def point_values(fields: list[str], extension: str, lines: ScanLines,
                 expected_fields: int | None = None) -> tuple[list[float], int, tuple[int, ...] | None]:
    allowed = (3, 4, 6, 7) if extension == ".pts" else (4, 7)
    if len(fields) not in allowed or (expected_fields is not None and len(fields) != expected_fields):
        raise lines.error(f"Nieprawidłowy układ punktu. Dozwolona liczba kolumn: {allowed}; układ musi być stały.")
    values = numbers(fields, lines)
    intensity = 0
    if len(fields) in (4, 7):
        minimum, maximum = (-2048, 2047) if extension == ".pts" else (0, 1)
        if not minimum <= values[3] <= maximum:
            raise lines.error(f"Intensywność musi mieścić się w zakresie {minimum}–{maximum}.")
        # Match PDAL's PTS/PTX intensity conventions in the working LAS/LAZ.
        intensity = round(values[3] + 2048 if extension == ".pts" else values[3] * 4096)
    rgb = None
    if len(fields) in (6, 7):
        colors = values[-3:]
        if any(value < 0 or value > 255 or not value.is_integer() for value in colors):
            raise lines.error("Składowe RGB muszą być liczbami całkowitymi od 0 do 255.")
        rgb = tuple(int(value) * 257 for value in colors)
    return values[:3], intensity, rgb


def validate_scan_header(data: bytes, extension: str) -> int:
    """Validate only the first header and point; the worker checks the entire file."""
    lines = ScanLines(io.BytesIO(data))
    first = lines.fields(skip_blank=True)
    count = positive_count(first, lines) if extension == ".pts" else ptx_header(lines, first)[0]
    point_values(lines.fields(skip_blank=extension == ".pts"), extension, lines)
    return count


def scan_points(lines: ScanLines, extension: str, stats: ScanStats):
    expected_fields = None
    while (first := lines.fields(skip_blank=True, eof=True)) is not None:
        if extension == ".pts":
            count, matrix = positive_count(first, lines), None
        else:
            count, matrix = ptx_header(lines, first)
        stats.scans += 1
        for _ in range(count):
            fields = lines.fields(skip_blank=extension == ".pts")
            xyz, intensity, rgb = point_values(fields, extension, lines, expected_fields)
            expected_fields = len(fields)
            stats.input_points += 1
            if matrix is not None:
                if xyz == [0, 0, 0]:
                    stats.skipped_points += 1
                    continue
                xyz = [sum(xyz[axis] * matrix[axis * 4 + coordinate] for axis in range(3))
                       + matrix[12 + coordinate] for coordinate in range(3)]
                if not all(math.isfinite(value) for value in xyz):
                    raise lines.error("Transformacja PTX utworzyła nieskończoną współrzędną.")
            yield xyz, intensity, rgb
        if extension == ".pts":
            if lines.fields(skip_blank=True, eof=True) is not None:
                raise lines.error("Plik PTS zawiera więcej danych niż deklarowana liczba punktów.")
            return


def normalize_scan(input_path: Path, output_path: Path, progress=print) -> dict:
    extension = input_path.suffix.lower()
    if extension not in (".pts", ".ptx"):
        raise ValueError("Oczekiwano pliku PTS lub PTX.")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Plik roboczy musi być inny niż oryginał.")
    size = input_path.stat().st_size
    with input_path.open("rb") as source:
        declared_count = validate_scan_header(source.read(MAX_LINE_BYTES), extension)
    required = max(size * 3, declared_count * 96) + 1024**3
    if shutil.disk_usage(output_path.parent).free < required:
        raise ValueError(f"Za mało miejsca na import {extension[1:].upper()}. Minimum: {required / 1024**3:.2f} GB.")
    stats = ScanStats()
    written = 0
    opened = False
    try:
        with input_path.open("rb") as source:
            lines = ScanLines(source)
            points = scan_points(lines, extension, stats)
            first = next(points, None)
            if first is None:
                raise ValueError("Plik nie zawiera poprawnych punktów; pomiary PTX 0 0 0 są pomijane.")
            colored = first[2] is not None
            header_prefix = b"ply\nformat binary_little_endian 1.0\nelement vertex "
            properties = b"\nproperty double x\nproperty double y\nproperty double z\nproperty ushort intensity\n"
            if colored:
                properties += b"property ushort red\nproperty ushort green\nproperty ushort blue\n"
            record = struct.Struct("<dddH" + ("HHH" if colored else ""))
            buffer = bytearray()
            last_progress = -1
            with output_path.open("wb") as output:
                opened = True
                output.write(header_prefix + b"0" * 20 + properties + b"end_header\n")
                for xyz, intensity, rgb in itertools.chain((first,), points):
                    buffer.extend(record.pack(*xyz, intensity, *(rgb or ())))
                    written += 1
                    if written % CHUNK_POINTS == 0:
                        if shutil.disk_usage(output_path.parent).free < len(buffer) + 1024**3:
                            raise ValueError("Za mało miejsca na przygotowanie punktów PTS/PTX.")
                        output.write(buffer)
                        buffer.clear()
                        percent = min(99, source.tell() * 100 // max(size, 1))
                        if percent > last_progress:
                            progress(f"Przygotowanie skanu: {percent}%")
                            last_progress = percent
                output.write(buffer)
                output.seek(len(header_prefix))
                output.write(f"{written:020d}".encode("ascii"))
        progress("Przygotowanie skanu: 100%")
        return {"pointCount": written, "inputPointCount": stats.input_points,
                "skippedPoints": stats.skipped_points, "scanCount": stats.scans}
    except BaseException:
        if opened:
            output_path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description="Normalize a PTS/PTX scan incrementally for PDAL.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = normalize_scan(arguments.input, arguments.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print("SCAN_IMPORT_RESULT " + json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
