"""Remove spherical point neighborhoods from a LAS/LAZ file."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
from collections import defaultdict
from pathlib import Path

try:
	from segment_pointcloud import (
		SegmentationError,
		check,
		load_laszip,
		native_path,
		point_count_from_header,
	)
except ModuleNotFoundError:
	from tools.segment_pointcloud import (
		SegmentationError,
		check,
		load_laszip,
		native_path,
		point_count_from_header,
	)


class PointRemovalError(SegmentationError):
	pass


def parse_selections(value: str) -> list[tuple[float, float, float, float, float]]:
	try:
		payload = json.loads(value)
	except json.JSONDecodeError as error:
		raise PointRemovalError("Lista zaznaczeń ma nieprawidłowy format JSON.") from error

	if not isinstance(payload, list) or not payload:
		raise PointRemovalError("Dodaj co najmniej jedno zaznaczenie punktów.")
	if len(payload) > 500:
		raise PointRemovalError("Jedna operacja może zawierać najwyżej 500 zaznaczeń.")

	selections = []
	for index, item in enumerate(payload, start=1):
		if not isinstance(item, dict):
			raise PointRemovalError(f"Zaznaczenie {index} ma nieprawidłowy format.")
		position = item.get("position")
		if not isinstance(position, list) or len(position) != 3:
			raise PointRemovalError(f"Pozycja zaznaczenia {index} musi zawierać trzy wartości.")
		coordinates = [float(coordinate) for coordinate in position]
		radius = float(item.get("radius", 0))
		if not all(math.isfinite(coordinate) for coordinate in coordinates) or not math.isfinite(radius):
			raise PointRemovalError(f"Zaznaczenie {index} zawiera nieprawidłowe wartości.")
		if radius <= 0:
			raise PointRemovalError(f"Promień zaznaczenia {index} musi być większy od zera.")
		selections.append((coordinates[0], coordinates[1], coordinates[2], radius, radius * radius))

	return selections


def build_selection_grid(
	selections: list[tuple[float, float, float, float, float]],
) -> tuple[float, dict[tuple[int, int, int], list[tuple[float, float, float, float, float]]]]:
	cell_size = max(selection[3] for selection in selections)
	grid = defaultdict(list)
	for selection in selections:
		key = tuple(math.floor(selection[axis] / cell_size) for axis in range(3))
		grid[key].append(selection)
	return cell_size, grid


def selected_by_brush(
	x: float,
	y: float,
	z: float,
	cell_size: float,
	grid: dict[tuple[int, int, int], list[tuple[float, float, float, float, float]]],
) -> bool:
	base_x = math.floor(x / cell_size)
	base_y = math.floor(y / cell_size)
	base_z = math.floor(z / cell_size)
	for offset_x in (-1, 0, 1):
		for offset_y in (-1, 0, 1):
			for offset_z in (-1, 0, 1):
				for center_x, center_y, center_z, radius, radius_squared in grid.get(
					(base_x + offset_x, base_y + offset_y, base_z + offset_z), ()
				):
					if abs(x - center_x) > radius or abs(y - center_y) > radius or abs(z - center_z) > radius:
						continue
					delta_x = x - center_x
					delta_y = y - center_y
					delta_z = z - center_z
					if delta_x * delta_x + delta_y * delta_y + delta_z * delta_z <= radius_squared + 1e-12:
						return True
	return False


def remove_points(
	input_path: Path,
	output_path: Path,
	dll_path: Path,
	selections: list[tuple[float, float, float, float, float]],
) -> dict:
	total_points = point_count_from_header(input_path)
	if total_points <= 0:
		raise PointRemovalError("Plik źródłowy nie zawiera punktów.")

	cell_size, selection_grid = build_selection_grid(selections)
	dll = load_laszip(dll_path)
	reader = ctypes.c_void_p()
	writer = ctypes.c_void_p()
	reader_open = False
	writer_open = False
	removed_points = 0
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
			remove = selected_by_brush(
				coordinates[0], coordinates[1], coordinates[2], cell_size, selection_grid
			)
			if remove:
				removed_points += 1
			else:
				check(dll, writer, dll.laszip_set_point(writer, point_pointer), f"kopiowanie punktu {index}")
				check(dll, writer, dll.laszip_write_point(writer), f"zapis punktu {index}")
				check(dll, writer, dll.laszip_update_inventory(writer), f"aktualizacja nagłówka punktu {index}")

			progress = int(((index + 1) * 100) / total_points)
			if progress != last_progress:
				print(f"Usuwanie punktów: {progress}%", flush=True)
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

	remaining_points = total_points - removed_points
	if removed_points == 0:
		output_path.unlink(missing_ok=True)
		raise PointRemovalError("W zaznaczonych obszarach nie znaleziono punktów. Zwiększ promień.")
	if remaining_points == 0:
		output_path.unlink(missing_ok=True)
		raise PointRemovalError("Zaznaczenie objęło całą chmurę. Zmniejsz promień.")

	written_points = point_count_from_header(output_path)
	if written_points != remaining_points:
		output_path.unlink(missing_ok=True)
		raise PointRemovalError(
			f"Nieprawidłowy licznik punktów w pliku wynikowym: {written_points} zamiast {remaining_points}."
		)

	return {
		"inputPoints": total_points,
		"removedPoints": removed_points,
		"outputPoints": remaining_points,
		"selectionCount": len(selections),
		"outputFile": output_path.name,
	}


def parse_arguments() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Remove spherical neighborhoods from a LAS/LAZ point cloud.")
	parser.add_argument("--input", required=True, type=Path)
	parser.add_argument("--output", required=True, type=Path)
	parser.add_argument("--laszip", required=True, type=Path)
	selection_input = parser.add_mutually_exclusive_group(required=True)
	selection_input.add_argument("--selections", help="JSON array of positions and radii")
	selection_input.add_argument("--selections-file", type=Path, help="UTF-8 JSON file of positions and radii")
	return parser.parse_args()


def main() -> int:
	arguments = parse_arguments()
	try:
		selection_json = arguments.selections if arguments.selections is not None else arguments.selections_file.read_text(encoding="utf-8")
		selections = parse_selections(selection_json)
		input_path = arguments.input.resolve(strict=True)
		output_path = arguments.output.resolve()
		dll_path = arguments.laszip.resolve(strict=True)
		output_path.parent.mkdir(parents=True, exist_ok=True)
		result = remove_points(input_path, output_path, dll_path, selections)
		print(f"POINT_REMOVAL_RESULT {json.dumps(result, ensure_ascii=False)}", flush=True)
		return 0
	except (OSError, ValueError, SegmentationError) as error:
		print(f"BŁĄD USUWANIA PUNKTÓW: {error}", flush=True)
		return 1


if __name__ == "__main__":
	raise SystemExit(main())
