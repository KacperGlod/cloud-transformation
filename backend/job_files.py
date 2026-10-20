"""Preserve complete imports while discarding an attempt's generated files."""
from pathlib import Path

from .config import Settings
from .models import Cloud, ProcessingJob
from .storage import child_path, remove_directory


def work_input_path(settings: Settings, job: ProcessingJob, cloud: Cloud) -> Path:
    # PotreeConverter on Windows needs an ASCII basename for generated inputs.
    filename = cloud.source_file if job.kind == "import" else "result.laz"
    return child_path(settings.runtime_dir, str(job.id), filename)


def restore_input(settings: Settings, job: ProcessingJob, cloud: Cloud) -> None:
    work = child_path(settings.runtime_dir, str(job.id))
    output = child_path(settings.clouds_dir, cloud.storage_key)
    files = {cloud.source_file: work_input_path(settings, job, cloud)}
    if cloud.processing_file:
        files[cloud.processing_file] = child_path(work, cloud.processing_file)
    for filename, target in files.items():
        preserved = child_path(output, filename)
        if not target.exists() and preserved.is_file():
            work.mkdir(parents=True, exist_ok=True)
            preserved.rename(target)
    remove_directory(settings.clouds_dir, cloud.storage_key)


def clean_work(settings: Settings, job: ProcessingJob, cloud: Cloud, *, keep_import: bool) -> None:
    work = child_path(settings.runtime_dir, str(job.id))
    original = work_input_path(settings, job, cloud)
    if not keep_import or job.kind != "import" or job.upload_progress != 100 or not original.is_file():
        remove_directory(settings.runtime_dir, str(job.id))
        return
    for entry in work.iterdir():
        target = child_path(work, entry.name)
        if target == original:
            continue
        if target.is_dir():
            remove_directory(work, entry.name)
        else:
            target.unlink()
