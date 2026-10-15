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
from .models import Cloud, ProcessingJob, iso_utc, utcnow
from .storage import child_path, ensure_conversion_space, remove_directory, validate_potree


class WorkerStopped(Exception):
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
            session.execute(update(ProcessingJob).where(ProcessingJob.id == identifier).values(**values, updated_at=utcnow()))
            session.commit()

    def recover(self):
        with self.database.session() as session:
            jobs = list(session.scalars(select(ProcessingJob).where(
                ProcessingJob.status.in_(("processing", "converting", "segmenting", "transforming", "removing")))))
            for job in jobs:
                cloud = session.get(Cloud, job.cloud_id)
                if not cloud:
                    continue
                self.restore_input(job, cloud)
                job.status = "queued"
                job.progress = 0
                job.log = (job.log + "\nWznowiono zadanie po restarcie workera.\n")[-60000:]
                cloud.status = "queued"
            session.commit()

    def restore_input(self, job, cloud):
        work = child_path(self.settings.runtime_dir, str(job.id))
        output = child_path(self.settings.clouds_dir, cloud.storage_key)
        source = child_path(work, cloud.source_file)
        preserved = child_path(output, cloud.source_file)
        if not source.exists() and preserved.is_file():
            work.mkdir(parents=True, exist_ok=True)
            preserved.rename(source)
        remove_directory(self.settings.clouds_dir, cloud.storage_key)

    def claim_next(self):
        with self.database.session() as session:
            job = session.scalar(select(ProcessingJob).where(ProcessingJob.status == "queued")
                                 .order_by(ProcessingJob.created_at, ProcessingJob.id).limit(1))
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
            "converting": r"(?:^|[\r\n])\s*\[(\d+(?:\.\d+)?)%\s*,",
            "segmenting": r"Segmentacja:\s*(\d+(?:\.\d+)?)%",
            "transforming": r"(?:Analiza|Zapisywanie) transformacji:\s*(\d+(?:\.\d+)?)%",
            "removing": r"Usuwanie punktów:\s*(\d+(?:\.\d+)?)%",
        }
        try:
            with log_path.open("wb") as log_stream:
                while True:
                    if self.stopped():
                        raise WorkerStopped()
                    try:
                        block = output_queue.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    if block is None:
                        break
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

    def process(self, job_id):
        with self.database.session() as session:
            job = session.get(ProcessingJob, job_id)
            cloud = session.get(Cloud, job.cloud_id)
            source = session.get(Cloud, job.source_cloud_id) if job.source_cloud_id else None
        work = child_path(self.settings.runtime_dir, str(job.id))
        output = child_path(self.settings.clouds_dir, cloud.storage_key)
        input_path = child_path(work, cloud.source_file)
        work.mkdir(parents=True, exist_ok=True)
        params = job.parameters
        result = None
        interrupted = False
        try:
            if source:
                if not source.source_file or source.status != "ready":
                    raise ValueError("Chmura źródłowa nie jest gotowa do przetwarzania.")
                source_path = child_path(self.settings.clouds_dir, source.storage_key, source.source_file)
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
            ensure_conversion_space(input_path, self.settings.clouds_dir)
            output.mkdir(exist_ok=False)
            self.run_command(job_id, [self.settings.converter, input_path, "-o", output, "-m", "poisson"],
                             output / "conversion.log", "converting")
            validate_potree(output)
            for log_path in work.glob("*.log"):
                log_path.rename(output / log_path.name)
            size = input_path.stat().st_size
            input_path.rename(child_path(output, cloud.source_file))
            operation = {"type": job.kind, **params, "result": result}
            # Fetch metadata again: renaming / visibility changes may have occurred while processing.
            with self.database.session() as session:
                current = session.get(Cloud, cloud.id)
                info = {"schemaVersion": 2, "id": str(current.id), "name": current.name,
                    "visibility": current.visibility, "slug": current.storage_key, "sourceFile": current.source_file,
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
                session.commit()
        except (WorkerStopped, KeyboardInterrupt):
            interrupted = True
            self.shutdown_requested = True
            self.restore_input(job, cloud)
            with self.database.session() as session:
                current_job = session.get(ProcessingJob, job_id)
                current_job.status = "queued"
                current_job.progress = 0
                session.get(Cloud, cloud.id).status = "queued"
                session.commit()
        except Exception as problem:
            # Preserve a source moved just before a failed commit, so it is not lost.
            self.restore_input(job, cloud)
            with self.database.session() as session:
                current_job = session.get(ProcessingJob, job_id)
                current_job.status = "failed"
                current_job.error = str(problem)
                current_job.result = result
                current_cloud = session.get(Cloud, cloud.id)
                current_cloud.status = "failed"
                current_cloud.error = str(problem)
                session.commit()
        finally:
            if not interrupted:
                remove_directory(self.settings.runtime_dir, str(job.id))

    def run(self):
        try:
            with FileLock(str(self.settings.runtime_dir / "worker.lock"), timeout=0):
                self.recover()
                while not self.stopped():
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
