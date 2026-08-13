"""Create a spatial subset of a LAS/LAZ file with the bundled LASzip DLL."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import struct
import sys
from pathlib import Path


class SegmentationError(RuntimeError):
	pass


def native_path(path: Path) -> bytes:
	encoding = "mbcs" if os.name == "nt" else sys.getfilesystemencoding()
	return str(path).encode(encoding)


def point_count_from_header(path: Path) -> int:
	with path.open("rb") as source:
		header = source.read(375)

	if len(header) < 227 or header[:4] != b"LASF":
		raise SegmentationError("Plik wejściowy nie ma prawidłowego nagłówka LAS/LAZ.")

	header_size = struct.unpack_from("<H", header, 94)[0]
	legacy_count = struct.unpack_from("<I", header, 107)[0]
	if header_size >= 375 and len(header) >= 255:
		extended_count = struct.unpack_from("<Q", header, 247)[0]
		if extended_count:
			return extended_count

	return legacy_count


def load_laszip(dll_path: Path) -> ctypes.CDLL:
	try:
		dll = ctypes.CDLL(str(dll_path))
	except OSError as error:
		raise SegmentationError(f"Nie udało się załadować LASzip: {error}") from error

	void_pointer = ctypes.c_void_p
	bool_pointer = ctypes.POINTER(ctypes.c_int)
	double_pointer = ctypes.POINTER(ctypes.c_double)

	dll.laszip_create.argtypes = [ctypes.POINTER(void_pointer)]
	dll.laszip_create.restype = ctypes.c_int
	dll.laszip_destroy.argtypes = [void_pointer]
	dll.laszip_destroy.restype = ctypes.c_int
	dll.laszip_open_reader.argtypes = [void_pointer, ctypes.c_char_p, bool_pointer]
	dll.laszip_open_reader.restype = ctypes.c_int
	dll.laszip_close_reader.argtypes = [void_pointer]
	dll.laszip_close_reader.restype = ctypes.c_int
	dll.laszip_get_header_pointer.argtypes = [void_pointer, ctypes.POINTER(void_pointer)]
	dll.laszip_get_header_pointer.restype = ctypes.c_int
	dll.laszip_get_point_pointer.argtypes = [void_pointer, ctypes.POINTER(void_pointer)]
	dll.laszip_get_point_pointer.restype = ctypes.c_int
	dll.laszip_read_point.argtypes = [void_pointer]
	dll.laszip_read_point.restype = ctypes.c_int
	dll.laszip_get_coordinates.argtypes = [void_pointer, double_pointer]
	dll.laszip_get_coordinates.restype = ctypes.c_int
	dll.laszip_request_native_extension.argtypes = [void_pointer, ctypes.c_int]
	dll.laszip_request_native_extension.restype = ctypes.c_int
	dll.laszip_set_header.argtypes = [void_pointer, void_pointer]
	dll.laszip_set_header.restype = ctypes.c_int
	dll.laszip_open_writer.argtypes = [void_pointer, ctypes.c_char_p, ctypes.c_int]
	dll.laszip_open_writer.restype = ctypes.c_int
	dll.laszip_set_point.argtypes = [void_pointer, void_pointer]
	dll.laszip_set_point.restype = ctypes.c_int
	dll.laszip_write_point.argtypes = [void_pointer]
	dll.laszip_write_point.restype = ctypes.c_int
	dll.laszip_update_inventory.argtypes = [void_pointer]
	dll.laszip_update_inventory.restype = ctypes.c_int
	dll.laszip_close_writer.argtypes = [void_pointer]
	dll.laszip_close_writer.restype = ctypes.c_int
	dll.laszip_get_error.argtypes = [void_pointer, ctypes.POINTER(ctypes.c_char_p)]
	dll.laszip_get_error.restype = ctypes.c_int
	return dll


def laszip_error(dll: ctypes.CDLL, handle: ctypes.c_void_p, operation: str) -> SegmentationError:
	message_pointer = ctypes.c_char_p()
	message = "nieznany błąd"
	if handle and dll.laszip_get_error(handle, ctypes.byref(message_pointer)) == 0 and message_pointer.value:
		message = message_pointer.value.decode("utf-8", errors="replace")
	return SegmentationError(f"LASzip: {operation}: {message}")


def check(dll: ctypes.CDLL, handle: ctypes.c_void_p, result: int, operation: str) -> None:
	if result:
		raise laszip_error(dll, handle, operation)


def transform_point(matrix: list[float], x: float, y: float, z: float) -> tuple[float, float, float]:
	# Three.js serializes Matrix4.elements in column-major order.
	w = matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15]
	if abs(w) < 1e-15:
		raise SegmentationError("Macierz clipping boxa utworzyła nieprawidłową współrzędną.")
	return (
		(matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12]) / w,
		(matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13]) / w,
		(matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]) / w,
	)


def segment(input_path: Path, output_path: Path, dll_path: Path, matrix: list[float], mode: str) -> dict:
	total_points = point_count_from_header(input_path)
	if total_points <= 0:
		raise SegmentationError("Plik źródłowy nie zawiera punktów.")

	dll = load_laszip(dll_path)
	reader = ctypes.c_void_p()
	writer = ctypes.c_void_p()
	reader_open = False
	writer_open = False
	selected_points = 0
	coordinates = (ctypes.c_double * 3)()
	last_progress = -1

	try:
		check(dll, reader, dll.laszip_create(ctypes.byref(reader)), "tworzenie czytnika")
		compressed = ctypes.c_int()
		check(
			dll,
			reader,
			dll.laszip_open_reader(reader, native_path(input_path), ctypes.byref(compressed)),
			"otwieranie pliku źródłowego",
		)
		reader_open = True

		header_pointer = ctypes.c_void_p()
		point_pointer = ctypes.c_void_p()
		check(dll, reader, dll.laszip_get_header_pointer(reader, ctypes.byref(header_pointer)), "odczyt nagłówka")
		check(dll, reader, dll.laszip_get_point_pointer(reader, ctypes.byref(point_pointer)), "odczyt punktu")

		check(dll, writer, dll.laszip_create(ctypes.byref(writer)), "tworzenie pliku wynikowego")
		check(dll, writer, dll.laszip_request_native_extension(writer, 1), "włączenie obsługi LAS 1.4")
		check(dll, writer, dll.laszip_set_header(writer, header_pointer), "kopiowanie nagłówka")
		check(dll, writer, dll.laszip_open_writer(writer, native_path(output_path), 1), "otwieranie pliku wynikowego")
		writer_open = True

		for index in range(total_points):
			check(dll, reader, dll.laszip_read_point(reader), f"odczyt punktu {index}")
			check(dll, reader, dll.laszip_get_coordinates(reader, coordinates), f"odczyt współrzędnych punktu {index}")
			local_x, local_y, local_z = transform_point(matrix, coordinates[0], coordinates[1], coordinates[2])
			inside = (
				-0.5000000001 <= local_x <= 0.5000000001
				and -0.5000000001 <= local_y <= 0.5000000001
				and -0.5000000001 <= local_z <= 0.5000000001
			)
			keep = inside if mode == "inside" else not inside

			if keep:
				check(dll, writer, dll.laszip_set_point(writer, point_pointer), f"kopiowanie punktu {index}")
				check(dll, writer, dll.laszip_write_point(writer), f"zapis punktu {index}")
				check(dll, writer, dll.laszip_update_inventory(writer), f"aktualizacja nagłówka punktu {index}")
				selected_points += 1

			progress = int(((index + 1) * 100) / total_points)
			if progress != last_progress:
				print(f"Segmentacja: {progress}%", flush=True)
				last_progress = progress

		check(dll, writer, dll.laszip_close_writer(writer), "zamykanie pliku wynikowego")
		writer_open = False
		check(dll, reader, dll.laszip_close_reader(reader), "zamykanie pliku źródłowego")
		reader_open = False
	finally:
		if writer_open:
			dll.laszip_close_writer(writer)
		if reader_open:
			dll.laszip_close_reader(reader)
		if writer:
			dll.laszip_destroy(writer)
		if reader:
			dll.laszip_destroy(reader)

	if selected_points == 0:
		output_path.unlink(missing_ok=True)
		raise SegmentationError("Clipping box nie wybrał żadnych punktów.")

	written_points = point_count_from_header(output_path)
	if written_points != selected_points:
		output_path.unlink(missing_ok=True)
		raise SegmentationError(
			f"Nieprawidłowy licznik punktów w pliku wynikowym: {written_points} zamiast {selected_points}."
		)

	return {
		"inputPoints": total_points,
		"selectedPoints": selected_points,
		"rejectedPoints": total_points - selected_points,
		"mode": mode,
		"outputFile": output_path.name,
	}


def parse_arguments() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Segment a LAS/LAZ file using an oriented Potree clipping box.")
	parser.add_argument("--input", required=True, type=Path)
	parser.add_argument("--output", required=True, type=Path)
	parser.add_argument("--laszip", required=True, type=Path)
	parser.add_argument("--matrix", required=True, help="Column-major source-to-box Matrix4 JSON array")
	parser.add_argument("--mode", required=True, choices=("inside", "outside"))
	return parser.parse_args()


def main() -> int:
	arguments = parse_arguments()
	try:
		matrix = json.loads(arguments.matrix)
		if not isinstance(matrix, list) or len(matrix) != 16:
			raise SegmentationError("Macierz clipping boxa musi zawierać 16 elementów.")
		matrix = [float(value) for value in matrix]
		if not all(math.isfinite(value) for value in matrix):
			raise SegmentationError("Macierz clipping boxa zawiera nieprawidłowe wartości.")

		input_path = arguments.input.resolve(strict=True)
		output_path = arguments.output.resolve()
		dll_path = arguments.laszip.resolve(strict=True)
		output_path.parent.mkdir(parents=True, exist_ok=True)
		result = segment(input_path, output_path, dll_path, matrix, arguments.mode)
		print(f"SEGMENT_RESULT {json.dumps(result, ensure_ascii=False)}", flush=True)
		return 0
	except (OSError, ValueError, SegmentationError) as error:
		print(f"BŁĄD SEGMENTACJI: {error}", file=sys.stderr, flush=True)
		return 1


if __name__ == "__main__":
	raise SystemExit(main())
