import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from .config import Settings
from .formats import NORMALIZED_EXTENSIONS
from .models import Cloud, ProcessingJob, User, iso_utc, utcnow
from .storage import child_path, download_filename, find_source, read_info, remove_directory, validate_potree


ACTIVE_STATUSES = ("uploading", "queued", "processing", "normalizing", "converting", "segmenting", "transforming", "removing")


def get_cloud(session, identifier: str, user: User | None, *, write=False) -> Cloud:
    try:
        cloud = session.get(Cloud, uuid.UUID(identifier))
    except ValueError:
        cloud = session.scalar(select(Cloud).where(Cloud.storage_key == identifier))
    if cloud is None or cloud.deleted_at is not None or cloud.status in ("deleting", "deleted"):
        raise HTTPException(404, "Nie znaleziono chmury.")
    owns = user is not None and cloud.owner_id == user.id
    if (write and not owns) or (not owns and cloud.visibility != "public"):
        raise HTTPException(404, "Nie znaleziono chmury.")
    return cloud


def finish_cloud_deletion(session, cloud: Cloud, settings: Settings) -> None:
    """Remove files before marking the retained record deleted; caller commits."""
    remove_directory(settings.clouds_dir, cloud.storage_key)
    for job_id in session.scalars(select(ProcessingJob.id).where(ProcessingJob.cloud_id == cloud.id)):
        remove_directory(settings.runtime_dir, str(job_id))
    cloud.status = "deleted"
    cloud.deleted_at = utcnow()
    cloud.error = None


def cloud_json(cloud: Cloud, user: User | None, settings: Settings) -> dict:
    own = user is not None and cloud.owner_id == user.id
    source_available = bool(cloud.source_file and child_path(settings.clouds_dir, cloud.storage_key, cloud.source_file).is_file())
    processing_available = bool(cloud.processing_filename and
        child_path(settings.clouds_dir, cloud.storage_key, cloud.processing_filename).is_file())
    url = f"/api/clouds/{cloud.id}/files/{cloud.entry_file}" if cloud.entry_file and cloud.status == "ready" else None
    return {
        "id": str(cloud.id), "name": cloud.name, "slug": cloud.storage_key,
        "visibility": cloud.visibility, "status": cloud.status, "url": url,
        "viewerUrl": f"/viewer?cloud={cloud.id}",
        "format": "Potree 1.x" if cloud.entry_file == "cloud.js" else "Potree 2.x",
        "sourceAvailable": source_available and own, "sourceFile": cloud.source_file if own else None,
        "processingAvailable": processing_available and own,
        "downloadName": download_filename(cloud.name, cloud.source_file) if own and cloud.source_file else None,
        "parentCloud": str(cloud.parent_id) if cloud.parent_id else None,
        "operation": cloud.operation if own else None,
        "sizeBytes": cloud.size_bytes, "createdAt": iso_utc(cloud.created_at),
        "updatedAt": iso_utc(cloud.updated_at), "canEdit": own,
        "valid": cloud.status == "ready", "validationError": cloud.error, "error": cloud.error,
    }


def job_json(job: ProcessingJob, cloud: Cloud) -> dict:
    params = job.parameters or {}
    return {
        "id": str(job.id), "cloudId": str(cloud.id), "kind": job.kind,
        "name": cloud.name, "slug": cloud.storage_key, "filename": cloud.source_file,
        "status": job.status, "visibility": cloud.visibility,
        "uploadProgress": job.upload_progress, "progress": job.progress,
        "pointCloudUrl": f"/api/clouds/{cloud.id}/files/{cloud.entry_file}" if cloud.status == "ready" else None,
        "sourceCloud": str(job.source_cloud_id) if job.source_cloud_id else None,
        "mode": params.get("mode"), "selection": job.result if job.kind == "segmentation" else None,
        "transformation": params if job.kind == "transformation" else None,
        "transformationResult": job.result if job.kind == "transformation" else None,
        "removalSelections": params.get("selections"),
        "removalResult": job.result if job.kind == "point-removal" else None,
        "error": job.error, "log": job.log, "createdAt": iso_utc(job.created_at),
        "updatedAt": iso_utc(job.updated_at),
    }


def create_job(session, user: User, name: str, visibility: str, filename: str, *, kind="import", source=None, parameters=None):
    identifier = uuid.uuid4()
    cloud = Cloud(id=identifier, owner_id=user.id, name=name, visibility=visibility,
                  storage_key=str(identifier), source_file=filename,
                  processing_file="working.laz" if Path(filename).suffix.lower() in NORMALIZED_EXTENSIONS else None,
                  status="uploading" if kind == "import" else "queued",
                  parent_id=source.id if source else None)
    session.add(cloud)
    session.flush()
    job = ProcessingJob(cloud_id=cloud.id, owner_id=user.id, kind=kind, status=cloud.status,
                        source_cloud_id=source.id if source else None, parameters=parameters or {})
    session.add(job)
    session.commit()
    return cloud, job


def import_legacy(session, settings: Settings, owner: User) -> int:
    """Register existing directories once; never move or rewrite source data."""
    known = set(session.scalars(select(Cloud.storage_key)))
    imported = {}
    parents = {}
    for directory in sorted(settings.clouds_dir.iterdir()):
        if not directory.is_dir() or directory.is_symlink() or directory.name in known:
            continue
        entry = next((name for name in ("metadata.json", "cloud.js") if (directory / name).is_file()), None)
        if not entry:
            continue
        info = read_info(directory)
        error = None
        if entry == "metadata.json":
            try:
                validate_potree(directory)
            except (OSError, ValueError, KeyError, TypeError) as problem:
                error = str(problem)
        source = find_source(directory, info)
        cloud = Cloud(id=uuid.uuid5(uuid.NAMESPACE_URL, f"cloud-transformation:{directory.name}"),
            owner_id=owner.id, name=str(info.get("name") or directory.name)[:120],
            visibility="public" if info.get("visibility") == "public" else "private",
            storage_key=directory.name, source_file=source, entry_file=entry,
            size_bytes=(directory / source).stat().st_size if source else 0,
            status="failed" if error else "ready", error=error,
            operation=info.get("operation") if isinstance(info.get("operation"), dict) else None)
        processing = info.get("processingFile")
        if (isinstance(processing, str) and Path(processing).name == processing
                and Path(processing).suffix.lower() in (".las", ".laz")
                and child_path(directory, processing).is_file()):
            cloud.processing_file = processing
        session.add(cloud)
        imported[directory.name] = cloud
        parents[directory.name] = info.get("parentCloud")
    session.flush()
    by_key = {cloud.storage_key: cloud for cloud in session.scalars(select(Cloud))}
    for key, parent_key in parents.items():
        if isinstance(parent_key, str) and parent_key in by_key and parent_key != key:
            imported[key].parent_id = by_key[parent_key].id
    session.commit()
    return len(imported)
