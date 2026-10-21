"""Combine cloud provenance with bounded, on-demand geometry metadata."""
from pathlib import Path

from sqlalchemy.orm import Session

from .cloud_metadata import UNKNOWN_REFERENCE, read_las_metadata, read_potree_metadata
from .config import Settings
from .models import Cloud, User
from .services import cloud_json
from .storage import child_path


def cloud_info(session: Session, cloud: Cloud, user: User | None, settings: Settings) -> dict:
    descriptor = cloud_json(cloud, user, settings)
    info = {"cloud": descriptor, "sourceFormat": Path(cloud.source_file).suffix[1:].upper() if cloud.source_file else None,
            "pointCount": None, "bounds": None, "scale": None, "offset": None,
            "lasVersion": None, "pointFormat": None, "metadataSource": None,
            "attributes": None, "reference": dict(UNKNOWN_REFERENCE), "source": None, "notices": []}
    if descriptor["canEdit"] and cloud.parent_id:
        parent = session.get(Cloud, cloud.parent_id)
        if parent and parent.owner_id == user.id:
            deleted = parent.deleted_at is not None or parent.status in ("deleting", "deleted")
            info["source"] = {"id": str(parent.id), "name": parent.name,
                              "status": "deleted" if deleted else parent.status,
                              "viewerUrl": f"/viewer?cloud={parent.id}" if not deleted and parent.status == "ready" else None}
    if cloud.status != "ready":
        info["notices"].append("Geometria i atrybuty są dostępne po zakończeniu przetwarzania.")
        return info

    if cloud.entry_file:
        try:
            metadata = read_potree_metadata(child_path(settings.clouds_dir, cloud.storage_key, cloud.entry_file))
            info.update(metadata)
            info["metadataSource"] = "Potree"
        except (OSError, ValueError, RecursionError):
            info["notices"].append("Nie udało się odczytać metadanych Potree.")

    # Use the normalized LAS/LAZ for text/PLY imports, while retaining the original format above.
    filename = cloud.processing_filename
    if filename and Path(filename).suffix.lower() in (".las", ".laz"):
        try:
            metadata = read_las_metadata(child_path(settings.clouds_dir, cloud.storage_key, filename))
            for key in ("pointCount", "bounds", "scale", "offset", "lasVersion", "pointFormat"):
                if metadata[key] is not None:
                    info[key] = metadata[key]
            if metadata["reference"]["name"]:
                info["reference"] = metadata["reference"]
            else:
                # A units-only GeoTIFF record must not hide a recognized Potree CRS.
                for key in ("horizontalUnit", "verticalUnit"):
                    if metadata["reference"][key]:
                        info["reference"][key] = metadata["reference"][key]
                if not info["reference"]["name"]:
                    info["reference"]["source"] = metadata["reference"]["source"]
            info["metadataSource"] = "LAS/LAZ + Potree" if info["metadataSource"] else "LAS/LAZ"
            info["notices"].extend(metadata["notices"])
        except (OSError, ValueError):
            info["notices"].append("Nie udało się odczytać nagłówka LAS/LAZ.")
    return info
