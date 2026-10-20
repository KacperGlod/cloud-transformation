"""Single durable worker; can also be run with python -m backend.worker."""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

from filelock import FileLock, Timeout
from sqlalchemy import select, update

from .config import Settings
from .database import Database
from .formats import NORMALIZED_EXTENSIONS
from .job_files import clean_work, restore_input, work_input_path
from .job_states import RUNNING_STATUSES
from .jobs import finish_cancelled
from .models import Cloud, ProcessingJob, iso_utc, utcnow
from .ply import ply_pipeline
from .storage import child_path, ensure_conversion_space, validate_potree


class WorkerStopped(Exception):
    pass


class JobCancelled(Exception):
    pass


class Worker:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.stop_file = Path(os.environ["WORKER_STOP_FILE"]) if os.getenv("WORKER_STOP_FILE") else None
        self.parent_lock = FileLock(os.environ["WORKER_PARENT_LOCK"], timeout=0) if os.getenv("WORKER_PARENT_LOCK") else None
        self.shutdown_requested = False

    def stopped(self):
        if self.shutdown_requested:
            return True
        if self.stop_file and self.stop_file.exists():
            return True
        if self.parent_lock:
            try:
                with self.parent_lock:
                    # The API releases its OS lock even if its process is forcefully terminated.
                    return True
            except Timeout:
                pass
        return False

    def update_job(self, identifier, **values):
        with self.database.session() as session:
            states = RUNNING_STATUSES if "status" in values else (*RUNNING_STATUSES, "cancelling")
            changed = session.execute(update(ProcessingJob).where(
                ProcessingJob.id == identifier, ProcessingJob.status.in_(states)
            ).values(**values, updated_at=utcnow()))
            session.commit()
            if "status" in values and changed.rowcount != 1:
                raise JobCancelled()

    def check_interruption(self, job_id):
        with self.database.session() as session:
            status = session.scalar(select(ProcessingJob.status).where(ProcessingJob.id == job_id))
        if status == "cancelling":
            raise JobCancelled()
        if self.stopped():
            raise WorkerStopped()

    def recover(self):
        with self.database.session() as session:
            identifiers = list(session.scalars(select(ProcessingJob.id).where(
                ProcessingJob.status.in_((*RUNNING_STATUSES, "cancelling")))))
        for identifier in identifiers:
            self.finish_attempt(identifier, "queued", recovery=True)

    def work_input_path(self, job: ProcessingJob, cloud: Cloud) -> Path:
        return work_input_path(self.settings, job, cloud)

    def restore_input(self, job, cloud):
        restore_input(self.settings, job, cloud)

    def finish_attempt(self, job_id, status, *, error=None, result=None, recovery=False):
        # Terminal states are published only after cleanup, serialized with retry/deletion.
        with FileLock(str(self.settings.data_dir / "cloud-mutations.lock"), timeout=30):
            with self.database.session() as session:
                job = session.get(ProcessingJob, job_id)
                cloud = session.get(Cloud, job.cloud_id) if job else None
                if (not cloud or cloud.deleted_at is not None or cloud.status in ("deleting", "deleted")
                        or job.status not in (*RUNNING_STATUSES, "cancelling")):
                    return
                try:
                    if job.status == "cancelling":
                        finish_cancelled(session, self.settings, job, cloud)
                        return
                    self.restore_input(job, cloud)
                    if status != "queued":
                        clean_work(self.settings, job, cloud, keep_import=True)
                except OSError as problem:
                    status = "failed"
                    error = f"{error or 'Przerwano zadanie'}. Nie udało się posprzątać plików: {problem}"
                job.status, job.error, job.result = status, error, result
                cloud.status, cloud.error, cloud.entry_file = status, error, None
                if status == "queued":
                    job.progress = 0
                    if recovery:
                        job.log = (job.log + "\nWznowiono zadanie po restarcie workera.\n")[-60000:]
                session.commit()

    def finish_pending_cancellations(self):
        with self.database.session() as session:
            identifiers = list(session.scalars(select(ProcessingJob.id).where(ProcessingJob.status == "cancelling")))
        for identifier in identifiers:
            self.finish_attempt(identifier, "cancelled")

    def claim_next(self):
        with self.database.session() as session:
            job = session.scalar(select(ProcessingJob).join(Cloud, ProcessingJob.cloud_id == Cloud.id)
                                 .where(ProcessingJob.status == "queued", Cloud.deleted_at.is_(None),
                                        Cloud.status.not_in(("deleting", "deleted")))
                                 .order_by(ProcessingJob.updated_at, ProcessingJob.id).limit(1))
            if not job:
                return None
            claimed = session.execute(update(ProcessingJob).where(
                ProcessingJob.id == job.id, ProcessingJob.status == "queued").values(status="processing", updated_at=utcnow()))
            if claimed.rowcount != 1:
                session.rollback()
                return None
            session.execute(update(Cloud).where(Cloud.id == job.cloud_id).values(status="processing", updated_at=utcnow()))
            session.commit()
            return job.id

    def run_command(self, job_id, command, log_path: Path, status: str) -> str:
        self.update_job(job_id, status=status, progress=0)
        output_queue = queue.Queue()
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        environment["PATH"] = str(Path(command[0]).resolve().parent) + os.pathsep + environment.get("PATH", "")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        complete_output = ""
        with self.database.session() as session:
            saved_log = session.get(ProcessingJob, job_id).log
        progress = 0
        last_save = 0.0
        process = subprocess.Popen([str(argument) for argument in command], cwd=self.settings.root,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment, creationflags=creation_flags)

        def read_output():
            try:
                while block := process.stdout.read1(8192):
                    output_queue.put(block)
            finally:
                output_queue.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        patterns = {
            "normalizing": r"Przygotowanie skanu:\s*(\d+(?:\.\d+)?)%",
            "converting": r"(?:^|[\r\n])\s*\[(\d+(?:\.\d+)?)%\s*,",
            "segmenting": r"Segmentacja:\s*(\d+(?:\.\d+)?)%",
            "transforming": r"(?:Analiza|Zapisywanie) transformacji:\s*(\d+(?:\.\d+)?)%",
            "removing": r"Usuwanie punktów:\s*(\d+(?:\.\d+)?)%",
        }
        try:
            with log_path.open("wb") as log_stream:
                stream_closed = False
                while True:
                    self.check_interruption(job_id)
                    if stream_closed and process.poll() is not None:
                        break
                    try:
                        block = output_queue.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    if block is None:
                        stream_closed = True
                        continue
                    log_stream.write(block)
                    text = block.decode("utf-8", errors="replace")
                    complete_output = (complete_output + text)[-200000:]
                    saved_log = (saved_log + text)[-60000:]
                    matches = re.findall(patterns[status], complete_output)
                    if matches:
                        progress = max(progress, min(100, int(float(matches[-1]))))
                    if time.monotonic() - last_save >= 0.5:
                        self.update_job(job_id, log=saved_log, progress=progress)
                        last_save = time.monotonic()
            code = process.wait()
            self.check_interruption(job_id)
            self.update_job(job_id, log=saved_log, progress=progress)
            if code:
                details = complete_output.strip().splitlines()
                raise ValueError(f"Proces {Path(str(command[0])).name} zakończył się kodem {code}. "
                                 + (details[-1][:1000] if details else "Sprawdź log zadania."))
            return complete_output
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            reader.join(timeout=2)
            process.stdout.close()
            self.update_job(job_id, log=saved_log, progress=progress)

    def process(self, job_id):
        with self.database.session() as session:
            job = session.get(ProcessingJob, job_id)
            if not job:
                return
            cloud = session.get(Cloud, job.cloud_id)
            if not cloud or cloud.deleted_at is not None or cloud.status in ("deleting", "deleted"):
                return
            if job.status not in (*RUNNING_STATUSES, "cancelling"):
                return
            source = session.get(Cloud, job.source_cloud_id) if job.source_cloud_id else None
        work = child_path(self.settings.runtime_dir, str(job.id))
        output = child_path(self.settings.clouds_dir, cloud.storage_key)
        input_path = self.work_input_path(job, cloud)
        work.mkdir(parents=True, exist_ok=True)
        params = job.parameters
        result = None
        try:
            self.check_interruption(job_id)
            if job.kind != "import" and source is None:
                raise ValueError("Nie znaleziono chmury źródłowej.")
            if source:
                if not source.processing_filename or source.status != "ready":
                    raise ValueError("Chmura źródłowa nie jest gotowa do przetwarzania.")
                source_path = child_path(self.settings.clouds_dir, source.storage_key, source.processing_filename)
                command = [sys.executable, "-u", str(self.settings.root / "tools" / {
                    "segmentation": "segment_pointcloud.py", "transformation": "transform_pointcloud.py",
                    "point-removal": "remove_points.py"}[job.kind]),
                    "--input", source_path, "--output", input_path, "--laszip", self.settings.laszip]
                if job.kind == "segmentation":
                    command += ["--matrix", json.dumps(params["sourceToBoxMatrix"]), "--mode", params["mode"]]
                    status, prefix = "segmenting", "SEGMENT_RESULT"
                elif job.kind == "transformation":
                    command += ["--translation", json.dumps(params["translation"]),
                        "--rotation", json.dumps(params["rotationDegrees"]), "--scale", json.dumps(params["scale"]),
                        "--pivot-mode", params["pivotMode"], "--pivot", json.dumps(params["pivot"])]
                    status, prefix = "transforming", "TRANSFORM_RESULT"
                else:
                    selection_file = work / "selections.json"
                    selection_file.write_text(json.dumps(params["selections"]), encoding="utf-8")
                    command += ["--selections-file", selection_file]
                    status, prefix = "removing", "POINT_REMOVAL_RESULT"
                operation_output = self.run_command(job_id, command, work / f"{job.kind}.log", status)
                summaries = re.findall(rf"^{prefix}\s+(.+)$", operation_output, re.MULTILINE)
                if not summaries:
                    raise ValueError("Proces nie zwrócił podsumowania operacji.")
                result = json.loads(summaries[-1])
            original_path = input_path
            extension = input_path.suffix.lower()
            if job.kind == "import" and extension in NORMALIZED_EXTENSIONS:
                if not self.settings.pdal.is_file():
                    raise ValueError(f"Import {extension[1:].upper()} wymaga PDAL. Skonfiguruj PDAL_PATH.")
                pdal_source = original_path
                if extension in (".pts", ".ptx"):
                    pdal_source = work / "normalized.ply"
                    prepared = self.run_command(job_id,
                        [sys.executable, "-u", "-m", "backend.scan_text", "--input", original_path, "--output", pdal_source],
                        work / "scan-import.log", "normalizing")
                    summaries = re.findall(r"^SCAN_IMPORT_RESULT\s+(.+)$", prepared, re.MULTILINE)
                    if not summaries:
                        raise ValueError("Proces nie zwrócił podsumowania importu PTS/PTX.")
                    result = json.loads(summaries[-1])
                input_path = child_path(work, cloud.processing_file)
                pipeline_file = work / "ply-pipeline.json"
                pipeline_file.write_text(json.dumps(ply_pipeline(pdal_source, input_path, self.settings.clouds_dir)), encoding="utf-8")
                self.run_command(job_id, [self.settings.pdal, "pipeline", pipeline_file, "--stream"],
                                 work / "ply-import.log", "converting")
            ensure_conversion_space(input_path, self.settings.clouds_dir)
            self.check_interruption(job_id)
            output.mkdir(exist_ok=False)
            self.run_command(job_id, [self.settings.converter, input_path, "-o", output, "-m", "poisson"],
                             output / "conversion.log", "converting")
            validate_potree(output, check_cancelled=lambda: self.check_interruption(job_id))
            self.check_interruption(job_id)
            for log_path in work.glob("*.log"):
                log_path.rename(output / log_path.name)
            size = original_path.stat().st_size
            original_path.rename(child_path(output, cloud.source_file))
            if input_path != original_path:
                input_path.rename(child_path(output, cloud.processing_file))
            if result is not None:
                result["outputFile"] = cloud.source_file
            operation = {"type": job.kind, **params, "result": result}
            # Fetch metadata again: renaming / visibility changes may have occurred while processing.
            with FileLock(str(self.settings.data_dir / "cloud-mutations.lock"), timeout=30), self.database.session() as session:
                self.check_interruption(job_id)
                current = session.get(Cloud, cloud.id)
                info = {"schemaVersion": 2, "id": str(current.id), "name": current.name,
                    "visibility": current.visibility, "slug": current.storage_key, "sourceFile": current.source_file,
                    "processingFile": current.processing_file,
                    "pointCloudFile": "metadata.json", "createdAt": iso_utc(current.created_at),
                    "parentCloud": str(current.parent_id) if current.parent_id else None, "operation": operation}
                (output / "cloud-info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
                current.status = "ready"
                current.entry_file = "metadata.json"
                current.size_bytes = size
                current.operation = operation
                current.error = None
                current_job = session.get(ProcessingJob, job_id)
                current_job.status = "completed"
                current_job.progress = 100
                current_job.result = result
                current_job.error = None
                clean_work(self.settings, job, cloud, keep_import=False)
                session.commit()
        except JobCancelled:
            self.finish_attempt(job_id, "cancelled")
        except (WorkerStopped, KeyboardInterrupt):
            self.shutdown_requested = True
            self.finish_attempt(job_id, "queued")
        except Exception as problem:
            # Preserve a source moved just before a failed commit, so it is not lost.
            self.finish_attempt(job_id, "failed", error=str(problem), result=result)

    def run(self):
        try:
            with FileLock(str(self.settings.runtime_dir / "worker.lock"), timeout=0):
                self.recover()
                while not self.stopped():
                    self.finish_pending_cancellations()
                    identifier = self.claim_next()
                    if identifier:
                        self.process(identifier)
                    else:
                        time.sleep(0.4)
        except Timeout:
            print("Worker jest już uruchomiony dla tego katalogu danych.", flush=True)


def main():
    settings = Settings()
    settings.prepare()
    database = Database(settings)
    try:
        Worker(settings, database).run()
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
