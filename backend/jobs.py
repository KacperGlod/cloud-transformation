"""Owner-authorized job cancellation, retry, and action availability."""
import uuid

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings
from .formats import NORMALIZED_EXTENSIONS
from .job_files import clean_work, restore_input, work_input_path
from .job_states import ACTIVE_STATUSES, RETRYABLE_STATUSES, RUNNING_STATUSES
from .models import Cloud, ProcessingJob, User, iso_utc, utcnow
from .storage import child_path


def get_job(session: Session, identifier: uuid.UUID, user: User) -> ProcessingJob:
    job = session.get(ProcessingJob, identifier)
    if job is None or job.owner_id != user.id:
        raise HTTPException(404, "Nie znaleziono zadania.")
    return job


def retry_unavailable_reason(session: Session, settings: Settings, job: ProcessingJob, cloud: Cloud) -> str | None:
    if cloud.deleted_at is not None or cloud.status in ("deleting", "deleted"):
        return "Pliki tej chmury zostały usunięte."
    if cloud.status not in RETRYABLE_STATUSES:
        return "Chmura jest już przetwarzana lub gotowa."
    if not settings.converter.is_file():
        return "Nie znaleziono PotreeConvertera."
    if job.kind == "import":
        if job.upload_progress != 100 or not work_input_path(settings, job, cloud).is_file():
            return "Brak kompletnego pliku wejściowego. Prześlij plik ponownie."
        if work_input_path(settings, job, cloud).suffix.lower() in NORMALIZED_EXTENSIONS and not settings.pdal.is_file():
            return "Import tego formatu wymaga PDAL."
    else:
        source = session.get(Cloud, job.source_cloud_id) if job.source_cloud_id else None
        if (source is None or source.owner_id != job.owner_id or source.deleted_at is not None
                or source.status != "ready" or not source.processing_filename):
            return "Chmura źródłowa jest niedostępna lub nie jest gotowa."
        if not child_path(settings.clouds_dir, source.storage_key, source.processing_filename).is_file():
            return "Brak pliku chmury źródłowej do przetwarzania."
        if not settings.laszip.is_file():
            return "Nie znaleziono biblioteki LASzip."
    return None


def job_actions(session: Session, settings: Settings, job: ProcessingJob, cloud: Cloud) -> dict:
    present = cloud.deleted_at is None and cloud.status not in ("deleting", "deleted")
    reason = retry_unavailable_reason(session, settings, job, cloud) if job.status in RETRYABLE_STATUSES else None
    return {"canCancel": present and job.status in ("queued", *RUNNING_STATUSES),
            "canRetry": job.status in RETRYABLE_STATUSES and reason is None,
            "retryUnavailableReason": reason}


def cancel_job(session: Session, settings: Settings, job: ProcessingJob, cloud: Cloud) -> None:
    """The caller holds cloud-mutations.lock; claiming a queued job uses a DB CAS."""
    if cloud.deleted_at is not None or cloud.status in ("deleting", "deleted"):
        raise HTTPException(409, "Pliki tej chmury zostały usunięte.")
    if job.status in ("cancelled", "cancelling"):
        return
    changed = session.execute(update(ProcessingJob).where(
        ProcessingJob.id == job.id, ProcessingJob.status == "queued"
    ).values(status="cancelling", updated_at=utcnow()))
    if changed.rowcount == 1:
        cloud.status = "cancelling"
        session.commit()
        session.refresh(job)
        # Nothing can claim the job now. Cleanup also handles a previously interrupted attempt.
        finish_cancelled(session, settings, job, cloud)
        return
    session.rollback()
    session.refresh(job)
    if job.status in ("cancelled", "cancelling"):
        return
    changed = session.execute(update(ProcessingJob).where(
        ProcessingJob.id == job.id, ProcessingJob.status.in_(RUNNING_STATUSES)
    ).values(status="cancelling", updated_at=utcnow()))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "Tego zadania nie można już zatrzymać.")
    cloud.status = "cancelling"
    session.commit()
    session.refresh(job)


def finish_cancelled(session: Session, settings: Settings, job: ProcessingJob, cloud: Cloud) -> None:
    restore_input(settings, job, cloud)
    clean_work(settings, job, cloud, keep_import=True)
    job.status, job.error, job.result = "cancelled", None, None
    job.log = (job.log + "\nZadanie anulowane przez użytkownika.\n")[-60000:]
    cloud.status, cloud.error, cloud.entry_file = "cancelled", None, None
    session.commit()


def retry_job(session: Session, settings: Settings, job: ProcessingJob, cloud: Cloud) -> None:
    """Restart the same job and cloud, preserving parameters and previous attempt logs."""
    if job.status not in RETRYABLE_STATUSES:
        raise HTTPException(409, "Można ponowić wyłącznie zadanie zakończone błędem lub anulowane.")
    reason = retry_unavailable_reason(session, settings, job, cloud)
    if reason:
        raise HTTPException(409, reason)
    dependent = session.scalar(select(ProcessingJob.id).where(
        ProcessingJob.cloud_id == cloud.id, ProcessingJob.id != job.id,
        ProcessingJob.status.in_(ACTIVE_STATUSES)).limit(1))
    if dependent:
        raise HTTPException(409, "Ta chmura ma już aktywne zadanie.")
    restore_input(settings, job, cloud)
    clean_work(settings, job, cloud, keep_import=True)
    previous_error = f"\nPoprzedni błąd: {job.error}" if job.error else ""
    job.log = (job.log + previous_error + f"\n--- Ponowiono zadanie: {iso_utc(utcnow())} ---\n")[-60000:]
    job.status, job.progress, job.error, job.result = "queued", 0, None, None
    cloud.status, cloud.error, cloud.entry_file = "queued", None, None
    session.commit()
