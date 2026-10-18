"""Apply an affine transformation to every point in a LAS/LAZ file."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import struct
import sys
from pathlib import Path


class TransformationError(RuntimeError):
	pass


class LaszipHeader(ctypes.Structure):
	"""LASzip 3.x public header structure used by the bundled DLL."""

	_fields_ = [
		("file_source_ID", ctypes.c_uint16),
		("global_encoding", ctypes.c_uint16),
		("project_ID_GUID_data_1", ctypes.c_uint32),
		("project_ID_GUID_data_2", ctypes.c_uint16),
		("project_ID_GUID_data_3", ctypes.c_uint16),
		("project_ID_GUID_data_4", ctypes.c_char * 8),
		("version_major", ctypes.c_uint8),
		("version_minor", ctypes.c_uint8),
		("system_identifier", ctypes.c_char * 32),
		("generating_software", ctypes.c_char * 32),
		("file_creation_day", ctypes.c_uint16),
		("file_creation_year", ctypes.c_uint16),
		("header_size", ctypes.c_uint16),
		("offset_to_point_data", ctypes.c_uint32),
		("number_of_variable_length_records", ctypes.c_uint32),
		("point_data_format", ctypes.c_uint8),
		("point_data_record_length", ctypes.c_uint16),
		("number_of_point_records", ctypes.c_uint32),
		("number_of_points_by_return", ctypes.c_uint32 * 5),
		("x_scale_factor", ctypes.c_double),
		("y_scale_factor", ctypes.c_double),
		("z_scale_factor", ctypes.c_double),
		("x_offset", ctypes.c_double),
		("y_offset", ctypes.c_double),
		("z_offset", ctypes.c_double),
		("max_x", ctypes.c_double),
		("min_x", ctypes.c_double),
		("max_y", ctypes.c_double),
		("min_y", ctypes.c_double),
		("max_z", ctypes.c_double),
		("min_z", ctypes.c_double),
		("start_of_waveform_data_packet_record", ctypes.c_uint64),
		("start_of_first_extended_variable_length_record", ctypes.c_uint64),
		("number_of_extended_variable_length_records", ctypes.c_uint32),
		("extended_number_of_point_records", ctypes.c_uint64),
		("extended_number_of_points_by_return", ctypes.c_uint64 * 15),
		("user_data_in_header_size", ctypes.c_uint32),
		("user_data_in_header", ctypes.c_void_p),
		("vlrs", ctypes.c_void_p),
		("user_data_after_header_size", ctypes.c_uint32),
		("user_data_after_header", ctypes.c_void_p),
	]


def native_path(path: Path) -> bytes:
	# The bundled LASzip DLL accepts UTF-8 paths on Windows.
	encoding = "utf-8" if os.name == "nt" else sys.getfilesystemencoding()
	return str(path).encode(encoding)


def point_count_from_header(path: Path) -> int:
	with path.open("rb") as source:
		header = source.read(375)

	if len(header) < 227 or header[:4] != b"LASF":
		raise TransformationError("Plik wejściowy nie ma prawidłowego nagłówka LAS/LAZ.")

	header_size = struct.unpack_from("<H", header, 94)[0]
	legacy_count = struct.unpack_from("<I", header, 107)[0]
	if header_size >= 375 and len(header) >= 255:
		extended_count = struct.unpack_from("<Q", header, 247)[0]
		if extended_count:
			return extended_count

	return legacy_count


def coordinate_metadata_from_header(path: Path) -> dict:
	with path.open("rb") as source:
		header = source.read(227)
	if len(header) < 227 or header[:4] != b"LASF":
		raise TransformationError("Plik wynikowy nie ma prawidłowego nagłówka LAS/LAZ.")
	scale = list(struct.unpack_from("<3d", header, 131))
	offset = list(struct.unpack_from("<3d", header, 155))
	max_x, min_x, max_y, min_y, max_z, min_z = struct.unpack_from("<6d", header, 179)
	return {
		"scale": scale,
		"offset": offset,
		"bounds": [min_x, max_x, min_y, max_y, min_z, max_z],
	}


def load_laszip(dll_path: Path) -> ctypes.CDLL:
	try:
		dll = ctypes.CDLL(str(dll_path))
	except OSError as error:
		raise TransformationError(f"Nie udało się załadować LASzip: {error}") from error

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
	dll.laszip_seek_point.argtypes = [void_pointer, ctypes.c_int64]
	dll.laszip_seek_point.restype = ctypes.c_int
	dll.laszip_get_coordinates.argtypes = [void_pointer, double_pointer]
	dll.laszip_get_coordinates.restype = ctypes.c_int
	dll.laszip_request_native_extension.argtypes = [void_pointer, ctypes.c_int]
	dll.laszip_request_native_extension.restype = ctypes.c_int
	dll.laszip_set_header.argtypes = [void_pointer, ctypes.POINTER(LaszipHeader)]
	dll.laszip_set_header.restype = ctypes.c_int
	dll.laszip_open_writer.argtypes = [void_pointer, ctypes.c_char_p, ctypes.c_int]
	dll.laszip_open_writer.restype = ctypes.c_int
	dll.laszip_set_point.argtypes = [void_pointer, void_pointer]
	dll.laszip_set_point.restype = ctypes.c_int
	dll.laszip_set_coordinates.argtypes = [void_pointer, double_pointer]
	dll.laszip_set_coordinates.restype = ctypes.c_int
	dll.laszip_write_point.argtypes = [void_pointer]
	dll.laszip_write_point.restype = ctypes.c_int
	dll.laszip_update_inventory.argtypes = [void_pointer]
	dll.laszip_update_inventory.restype = ctypes.c_int
	dll.laszip_close_writer.argtypes = [void_pointer]
	dll.laszip_close_writer.restype = ctypes.c_int
	dll.laszip_get_error.argtypes = [void_pointer, ctypes.POINTER(ctypes.c_char_p)]
	dll.laszip_get_error.restype = ctypes.c_int
	return dll


def laszip_error(dll: ctypes.CDLL, handle: ctypes.c_void_p, operation: str) -> TransformationError:
	message_pointer = ctypes.c_char_p()
	message = "nieznany błąd"
	if handle and dll.laszip_get_error(handle, ctypes.byref(message_pointer)) == 0 and message_pointer.value:
		message = message_pointer.value.decode("utf-8", errors="replace")
	return TransformationError(f"LASzip: {operation}: {message}")


def check(dll: ctypes.CDLL, handle: ctypes.c_void_p, result: int, operation: str) -> None:
	if result:
		raise laszip_error(dll, handle, operation)


def rotation_matrix(rotation_degrees: list[float]) -> tuple[tuple[float, float, float], ...]:
	rx, ry, rz = (math.radians(value) for value in rotation_degrees)
	cx, sx = math.cos(rx), math.sin(rx)
	cy, sy = math.cos(ry), math.sin(ry)
	cz, sz = math.cos(rz), math.sin(rz)
	# Apply rotations around X, then Y, then Z: R = Rz * Ry * Rx.
	return (
		(cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),
		(sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
		(-sy, cy * sx, cy * cx),
	)


def apply_transform(
	coordinates: tuple[float, float, float],
	translation: list[float],
	rotation: tuple[tuple[float, float, float], ...],
	scale: list[float],
	pivot: list[float],
) -> tuple[float, float, float]:
	x = (coordinates[0] - pivot[0]) * scale[0]
	y = (coordinates[1] - pivot[1]) * scale[1]
	z = (coordinates[2] - pivot[2]) * scale[2]
	return (
		rotation[0][0] * x + rotation[0][1] * y + rotation[0][2] * z + pivot[0] + translation[0],
		rotation[1][0] * x + rotation[1][1] * y + rotation[1][2] * z + pivot[1] + translation[1],
		rotation[2][0] * x + rotation[2][1] * y + rotation[2][2] * z + pivot[2] + translation[2],
	)


def choose_output_axis(minimum: float, maximum: float, source_scale: float) -> tuple[float, float]:
	integer_limit = 2_147_483_646
	span = maximum - minimum
	scale = abs(source_scale)
	if not math.isfinite(scale) or scale <= 0:
		raise TransformationError("Nagłówek źródłowy zawiera nieprawidłową skalę współrzędnych.")
	minimum_scale = span / (2 * integer_limit) if span > 0 else 0
	if minimum_scale > scale:
		scale = minimum_scale * (1 + 1e-12)
	offset = minimum + span / 2
	return scale, offset


def bounds_as_json(bounds: list[float]) -> dict:
	return {
		"min": [bounds[0], bounds[2], bounds[4]],
		"max": [bounds[1], bounds[3], bounds[5]],
	}


def transform(
	input_path: Path,
	output_path: Path,
	dll_path: Path,
	translation: list[float],
	rotation_degrees: list[float],
	scale: list[float],
	pivot_mode: str,
	custom_pivot: list[float],
) -> dict:
	total_points = point_count_from_header(input_path)
	if total_points <= 0:
		raise TransformationError("Plik źródłowy nie zawiera punktów.")

	dll = load_laszip(dll_path)
	reader = ctypes.c_void_p()
	writer = ctypes.c_void_p()
	reader_open = False
	writer_open = False
	coordinates = (ctypes.c_double * 3)()
	output_coordinates = (ctypes.c_double * 3)()
	rotation = rotation_matrix(rotation_degrees)

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
		source_header = ctypes.cast(header_pointer, ctypes.POINTER(LaszipHeader)).contents
		input_bounds = [
			source_header.min_x,
			source_header.max_x,
			source_header.min_y,
			source_header.max_y,
			source_header.min_z,
			source_header.max_z,
		]
		if not all(math.isfinite(value) for value in input_bounds):
			raise TransformationError("Nagłówek źródłowy zawiera nieprawidłowe granice chmury.")

		if pivot_mode == "center":
			pivot = [
				(source_header.min_x + source_header.max_x) / 2,
				(source_header.min_y + source_header.max_y) / 2,
				(source_header.min_z + source_header.max_z) / 2,
			]
		elif pivot_mode == "origin":
			pivot = [0.0, 0.0, 0.0]
		else:
			pivot = custom_pivot

		output_bounds = [math.inf, -math.inf, math.inf, -math.inf, math.inf, -math.inf]
		last_progress = -1
		for index in range(total_points):
			check(dll, reader, dll.laszip_read_point(reader), f"analiza punktu {index}")
			check(dll, reader, dll.laszip_get_coordinates(reader, coordinates), f"odczyt współrzędnych punktu {index}")
			transformed = apply_transform(
				(coordinates[0], coordinates[1], coordinates[2]), translation, rotation, scale, pivot
			)
			output_bounds[0] = min(output_bounds[0], transformed[0])
			output_bounds[1] = max(output_bounds[1], transformed[0])
			output_bounds[2] = min(output_bounds[2], transformed[1])
			output_bounds[3] = max(output_bounds[3], transformed[1])
			output_bounds[4] = min(output_bounds[4], transformed[2])
			output_bounds[5] = max(output_bounds[5], transformed[2])
			progress = int(((index + 1) * 50) / total_points)
			if progress != last_progress:
				print(f"Analiza transformacji: {progress}%", flush=True)
				last_progress = progress

		if not all(math.isfinite(value) for value in output_bounds):
			raise TransformationError("Transformacja utworzyła nieprawidłowe współrzędne.")

		output_header = LaszipHeader.from_buffer_copy(
			ctypes.string_at(header_pointer, ctypes.sizeof(LaszipHeader))
		)
		output_header.x_scale_factor, output_header.x_offset = choose_output_axis(
			output_bounds[0], output_bounds[1], source_header.x_scale_factor
		)
		output_header.y_scale_factor, output_header.y_offset = choose_output_axis(
			output_bounds[2], output_bounds[3], source_header.y_scale_factor
		)
		output_header.z_scale_factor, output_header.z_offset = choose_output_axis(
			output_bounds[4], output_bounds[5], source_header.z_scale_factor
		)
		output_header.min_x, output_header.max_x = output_bounds[0], output_bounds[1]
		output_header.min_y, output_header.max_y = output_bounds[2], output_bounds[3]
		output_header.min_z, output_header.max_z = output_bounds[4], output_bounds[5]

		check(dll, writer, dll.laszip_create(ctypes.byref(writer)), "tworzenie pliku wynikowego")
		check(dll, writer, dll.laszip_request_native_extension(writer, 1), "włączenie obsługi LAS 1.4")
		check(dll, writer, dll.laszip_set_header(writer, ctypes.byref(output_header)), "kopiowanie nagłówka")
		check(dll, writer, dll.laszip_open_writer(writer, native_path(output_path), 1), "otwieranie pliku wynikowego")
		writer_open = True
		check(dll, reader, dll.laszip_seek_point(reader, 0), "powrót do początku chmury")

		last_progress = 49
		for index in range(total_points):
			check(dll, reader, dll.laszip_read_point(reader), f"odczyt punktu {index}")
			check(dll, reader, dll.laszip_get_coordinates(reader, coordinates), f"odczyt współrzędnych punktu {index}")
			transformed = apply_transform(
				(coordinates[0], coordinates[1], coordinates[2]), translation, rotation, scale, pivot
			)
			output_coordinates[0], output_coordinates[1], output_coordinates[2] = transformed
			check(dll, writer, dll.laszip_set_point(writer, point_pointer), f"kopiowanie punktu {index}")
			check(dll, writer, dll.laszip_set_coordinates(writer, output_coordinates), f"ustawianie współrzędnych punktu {index}")
			check(dll, writer, dll.laszip_write_point(writer), f"zapis punktu {index}")
			check(dll, writer, dll.laszip_update_inventory(writer), f"aktualizacja nagłówka punktu {index}")
			progress = 50 + int(((index + 1) * 50) / total_points)
			if progress != last_progress:
				print(f"Zapisywanie transformacji: {progress}%", flush=True)
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

	written_points = point_count_from_header(output_path)
	if written_points != total_points:
		output_path.unlink(missing_ok=True)
		raise TransformationError(
			f"Nieprawidłowy licznik punktów w pliku wynikowym: {written_points} zamiast {total_points}."
		)
	output_metadata = coordinate_metadata_from_header(output_path)

	return {
		"inputPoints": total_points,
		"outputPoints": written_points,
		"translation": translation,
		"rotationDegrees": rotation_degrees,
		"scale": scale,
		"pivotMode": pivot_mode,
		"pivot": pivot,
		"inputBounds": bounds_as_json(input_bounds),
		"outputBounds": bounds_as_json(output_metadata["bounds"]),
		"outputScale": output_metadata["scale"],
		"outputOffset": output_metadata["offset"],
		"outputFile": output_path.name,
	}


def parse_vector(value: str, label: str) -> list[float]:
	try:
		vector = json.loads(value)
	except json.JSONDecodeError as error:
		raise TransformationError(f"{label} ma nieprawidłowy format JSON.") from error
	if not isinstance(vector, list) or len(vector) != 3:
		raise TransformationError(f"{label} musi zawierać trzy wartości.")
	result = [float(item) for item in vector]
	if not all(math.isfinite(item) for item in result):
		raise TransformationError(f"{label} zawiera nieprawidłowe wartości.")
	return result


def parse_arguments() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Transform a LAS/LAZ point cloud with the bundled LASzip DLL.")
	parser.add_argument("--input", required=True, type=Path)
	parser.add_argument("--output", required=True, type=Path)
	parser.add_argument("--laszip", required=True, type=Path)
	parser.add_argument("--translation", required=True, help="JSON array [x, y, z]")
	parser.add_argument("--rotation", required=True, help="XYZ rotation in degrees as a JSON array")
	parser.add_argument("--scale", required=True, help="XYZ scale as a JSON array")
	parser.add_argument("--pivot-mode", required=True, choices=("center", "origin", "custom"))
	parser.add_argument("--pivot", required=True, help="Custom pivot [x, y, z]")
	return parser.parse_args()


def main() -> int:
	arguments = parse_arguments()
	try:
		translation = parse_vector(arguments.translation, "Przesunięcie")
		rotation = parse_vector(arguments.rotation, "Obrót")
		scale = parse_vector(arguments.scale, "Skala")
		pivot = parse_vector(arguments.pivot, "Punkt obrotu")
		if any(value <= 0 for value in scale):
			raise TransformationError("Wartości skali muszą być większe od zera.")

		input_path = arguments.input.resolve(strict=True)
		output_path = arguments.output.resolve()
		dll_path = arguments.laszip.resolve(strict=True)
		output_path.parent.mkdir(parents=True, exist_ok=True)
		result = transform(
			input_path,
			output_path,
			dll_path,
			translation,
			rotation,
			scale,
			arguments.pivot_mode,
			pivot,
		)
		print(f"TRANSFORM_RESULT {json.dumps(result, ensure_ascii=False)}", flush=True)
		return 0
	except (OSError, ValueError, TransformationError) as error:
		print(f"BŁĄD TRANSFORMACJI: {error}", file=sys.stderr, flush=True)
		return 1


if __name__ == "__main__":
	raise SystemExit(main())
