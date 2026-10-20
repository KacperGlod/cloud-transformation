from concurrent.futures import ThreadPoolExecutor
import threading
import subprocess
import sys
import time
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from backend.app import create_app
from backend.models import ProcessingJob
from backend.worker import JobCancelled, Worker
from test_api import application, las_bytes, register, seed_cloud, use_cookies


def upload(client, name="Test zadania"):
    response = client.post("/api/conversions", params={"filename": "test.las", "name": name}, content=las_bytes(5000))
    assert response.status_code == 202, response.text
    return response.json()


def detail(client, job):
    return client.get(f"/api/jobs/{job['id']}").json()


def test_queued_cancellation_is_immediate_idempotent_and_survives_restart(application):
    app, client, settings = application
    _, cookies = register(client)
    job = upload(client)
    response = client.post(f"/api/jobs/{job['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled" and response.json()["canRetry"]
    assert not response.json()["canCancel"]
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    worker = Worker(settings, app.state.database)
    assert worker.claim_next() is None
    assert (settings.runtime_dir / job["id"] / "test.las").read_bytes() == las_bytes(5000)
    with TestClient(create_app(settings)) as restarted:
        use_cookies(restarted, cookies)
        Worker(settings, app.state.database).recover()
        assert detail(restarted, job)["status"] == "cancelled"
    assert client.get(f"/api/clouds/{job['cloudId']}").json()["status"] == "cancelled"
    assert client.delete(f"/api/clouds/{job['cloudId']}").status_code == 204
    assert not (settings.runtime_dir / job["id"]).exists()
    assert not detail(client, job)["canRetry"]


def test_cancel_and_retry_require_owner_and_same_origin(application):
    app, client, settings = application
    _, owner_cookies = register(client)
    job = upload(client)
    register(client, "other@example.test")
    for action in ("cancel", "retry"):
        assert client.post(f"/api/jobs/{job['id']}/{action}").status_code == 404
    client.cookies.clear()
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 401
    use_cookies(client, owner_cookies)
    for action in ("cancel", "retry"):
        assert client.post(f"/api/jobs/{job['id']}/{action}", headers={"Origin": "https://foreign.example"}).status_code == 403
    assert detail(client, job)["status"] == "queued"


def test_retry_preserves_ids_parameters_error_log_and_queue_order(application, monkeypatch):
    app, client, settings = application
    register(client)
    first = upload(client, "Pierwsze")
    worker = Worker(settings, app.state.database)
    with monkeypatch.context() as attempt:
        def fail(*_args, **_kwargs):
            raise ValueError("Tymczasowa awaria konwertera")
        attempt.setattr(worker, "run_command", fail)
        worker.process(worker.claim_next())
    failed = detail(client, first)
    assert failed["status"] == "failed" and failed["canRetry"]
    assert not (settings.clouds_dir / first["cloudId"]).exists()
    second = upload(client, "Drugie")
    response = client.post(f"/api/jobs/{first['id']}/retry")
    assert response.status_code == 202, response.text
    retried = response.json()
    assert retried["id"] == first["id"] and retried["cloudId"] == first["cloudId"]
    assert retried["status"] == "queued" and retried["progress"] == 0 and retried["error"] is None
    assert "Tymczasowa awaria" in retried["log"] and "Ponowiono" in retried["log"]
    assert client.post(f"/api/jobs/{first['id']}/retry").status_code == 409
    assert worker.claim_next() == uuid.UUID(second["id"])
    worker.process(uuid.UUID(second["id"]))
    worker.process(worker.claim_next())
    completed = detail(client, first)
    assert completed["status"] == "completed", completed
    assert not completed["canCancel"] and not completed["canRetry"]
    assert not (settings.runtime_dir / first["id"]).exists()
    assert client.get(f"/api/clouds/{first['cloudId']}/download").content == las_bytes(5000)
    assert client.post(f"/api/jobs/{first['id']}/cancel").status_code == 409
    assert client.post(f"/api/jobs/{first['id']}/retry").status_code == 409
    with app.state.database.session() as session:
        assert len(list(session.scalars(select(ProcessingJob)))) == 2


@pytest.mark.parametrize("stage", ["normalizing", "segmenting", "transforming", "removing", "converting"])
def test_silent_running_process_is_terminated_without_stopping_worker(application, monkeypatch, stage):
    app, client, settings = application
    register(client)
    job = upload(client)
    following = upload(client, "Kolejne zadanie")
    worker = Worker(settings, app.state.database)
    identifier = worker.claim_next()
    started = settings.runtime_dir / "started"
    spawned = []
    original_popen = subprocess.Popen
    def record_process(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        spawned.append(process)
        return process
    # stdout is deliberately closed before sleeping: cancellation must not block on wait().
    script = f"import os,time; from pathlib import Path; Path({str(started)!r}).touch(); os.close(1); os.close(2); time.sleep(60)"
    original_run = worker.run_command
    with monkeypatch.context() as attempt:
        attempt.setattr("backend.worker.subprocess.Popen", record_process)
        attempt.setattr(worker, "run_command", lambda job_id, _command, log, _status:
                        original_run(job_id, [sys.executable, "-u", "-c", script], log, stage))
        with ThreadPoolExecutor(max_workers=1) as executor:
            running = executor.submit(worker.process, identifier)
            deadline = time.monotonic() + 10
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert started.exists()
            response = client.post(f"/api/jobs/{job['id']}/cancel")
            assert response.status_code == 200 and response.json()["status"] == "cancelling"
            assert not response.json()["canRetry"]
            assert client.delete(f"/api/clouds/{job['cloudId']}").status_code == 409
            assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409
            running.result(timeout=10)
    assert spawned and all(process.poll() is not None for process in spawned)
    assert not worker.stopped()
    assert detail(client, job)["status"] == "cancelled"
    assert detail(client, job)["canRetry"]
    assert not (settings.clouds_dir / job["cloudId"]).exists()
    assert [entry.name for entry in (settings.runtime_dir / job["id"]).iterdir()] == ["test.las"]
    assert worker.claim_next() == uuid.UUID(following["id"])
    worker.process(uuid.UUID(following["id"]))
    assert detail(client, following)["status"] == "completed"


def test_restart_finishes_cancellation_and_preserves_moved_import(application):
    app, client, settings = application
    register(client)
    job = upload(client)
    worker = Worker(settings, app.state.database)
    identifier = worker.claim_next()
    output = settings.clouds_dir / job["cloudId"]
    output.mkdir()
    (settings.runtime_dir / job["id"] / "test.las").rename(output / "test.las")
    (output / "partial.bin").write_bytes(b"partial")
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelling"
    worker.recover()
    assert detail(client, job)["status"] == "cancelled"
    assert not output.exists()
    assert (settings.runtime_dir / job["id"] / "test.las").read_bytes() == las_bytes(5000)
    assert worker.claim_next() is None
    worker.process(identifier)
    assert detail(client, job)["status"] == "cancelled"


def test_retry_rejects_incomplete_upload_and_removed_source(application, monkeypatch):
    app, client, settings = application
    owner, _ = register(client)
    bad = client.post("/api/conversions?filename=empty.las&name=Empty", content=b"")
    assert bad.status_code == 400
    failed = client.get("/api/jobs").json()["jobs"][0]
    assert not failed["canRetry"] and "Prześlij plik ponownie" in failed["retryUnavailableReason"]
    assert client.post(f"/api/jobs/{failed['id']}/retry").status_code == 409
    source, directory = seed_cloud(app, settings, owner)
    operation = client.post("/api/point-removals", json={"sourceCloud": source, "name": "Wynik",
        "selections": [{"position": [1, 2, 3], "radius": 1}]}).json()
    worker = Worker(settings, app.state.database)
    def fail(*_args):
        raise ValueError("Przejściowy błąd")
    monkeypatch.setattr(worker, "run_command", fail)
    worker.process(worker.claim_next())
    assert detail(client, operation)["canRetry"]
    before = (directory / "original.las").read_bytes()
    assert client.post(f"/api/jobs/{operation['id']}/retry").status_code == 202
    with app.state.database.session() as session:
        stored = session.get(ProcessingJob, uuid.UUID(operation["id"]))
        assert stored.parameters["selections"] == [{"position": [1.0, 2.0, 3.0], "radius": 1.0}]
    assert client.post(f"/api/jobs/{operation['id']}/cancel").json()["status"] == "cancelled"
    assert before == (directory / "original.las").read_bytes()
    assert client.delete(f"/api/clouds/{source}").status_code == 204
    assert not detail(client, operation)["canRetry"]
    assert client.post(f"/api/jobs/{operation['id']}/retry").status_code == 409


def test_concurrent_retries_enqueue_only_once(application, monkeypatch):
    app, client, settings = application
    register(client)
    job = upload(client)
    worker = Worker(settings, app.state.database)
    def fail(*_args):
        raise ValueError("Awaria")
    monkeypatch.setattr(worker, "run_command", fail)
    worker.process(worker.claim_next())
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(client.post, f"/api/jobs/{job['id']}/retry") for _ in range(2)]
        statuses = sorted(future.result(timeout=10).status_code for future in futures)
    assert statuses == [202, 409]
    assert worker.claim_next() == uuid.UUID(job["id"])
    assert worker.claim_next() is None


def test_stage_transition_cannot_overwrite_cancellation(application):
    app, client, settings = application
    register(client)
    job = upload(client)
    worker = Worker(settings, app.state.database)
    identifier = worker.claim_next()
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelling"
    with pytest.raises(JobCancelled):
        worker.update_job(identifier, status="converting", progress=0)
    worker.finish_attempt(identifier, "failed", error="Process exited during cancellation")
    assert detail(client, job)["status"] == "cancelled"


def test_claiming_and_cancelling_cannot_leave_a_runnable_cancelled_job(application):
    app, client, settings = application
    register(client)
    worker = Worker(settings, app.state.database)
    for _ in range(5):
        job = upload(client)
        barrier = threading.Barrier(2)
        def claim():
            barrier.wait(timeout=5)
            return worker.claim_next()
        def cancel():
            barrier.wait(timeout=5)
            return client.post(f"/api/jobs/{job['id']}/cancel")
        with ThreadPoolExecutor(max_workers=2) as executor:
            claiming = executor.submit(claim)
            cancelling = executor.submit(cancel)
            identifier, response = claiming.result(timeout=10), cancelling.result(timeout=10)
        assert response.status_code == 200
        if identifier is not None:
            assert identifier == uuid.UUID(job["id"])
            worker.process(identifier)
        assert detail(client, job)["status"] == "cancelled"
        assert worker.claim_next() is None


def test_cancellation_at_validation_prevents_result_publication(application, monkeypatch):
    app, client, settings = application
    register(client)
    job = upload(client)
    worker = Worker(settings, app.state.database)
    from backend.storage import validate_potree
    def cancel_after_validation(output, **kwargs):
        validate_potree(output, **kwargs)
        assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelling"
    monkeypatch.setattr("backend.worker.validate_potree", cancel_after_validation)
    worker.process(worker.claim_next())
    final = detail(client, job)
    assert final["status"] == "cancelled" and final["pointCloudUrl"] is None
    assert not (settings.clouds_dir / job["cloudId"]).exists()
    assert (settings.runtime_dir / job["id"] / "test.las").read_bytes() == las_bytes(5000)
