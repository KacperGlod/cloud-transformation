from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import sys
import uuid

import anyio
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from .auth import COOKIE_NAME, current_user, hash_password, start_session, token_hash, user_json, verify_password
from .config import Settings
from .database import Database
from .models import Cloud, LoginSession, ProcessingJob, User, utcnow
from .schemas import AccountRead, CloudList, CloudName, CloudRead, CloudUpdate, Credentials, JobList, JobRead, PointRemoval, Segmentation, Transformation, Visibility
from .services import ACTIVE_STATUSES, cloud_json, create_job, get_cloud, import_legacy, job_json
from .storage import child_path, remove_directory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.prepare()
    database = Database(settings)

    @asynccontextmanager
    async def lifespan(app):
        with FileLock(str(settings.data_dir / "migration.lock"), timeout=30):
            database.migrate()
            with database.session() as session:
                owner = session.scalar(select(User).order_by(User.created_at, User.id).limit(1))
                if owner:
                    import_legacy(session, settings, owner)
                # A disconnected upload cannot be resumed as a valid complete LAS/LAZ.
                for job in session.scalars(select(ProcessingJob).where(ProcessingJob.status == "uploading")):
                    job.status = "failed"
                    job.error = "Przesyłanie przerwano. Prześlij plik ponownie."
                    cloud = session.get(Cloud, job.cloud_id)
                    cloud.status, cloud.error = "failed", job.error
                    remove_directory(settings.runtime_dir, str(job.id))
                # Finish a deletion interrupted between its database tombstone and file cleanup.
                for cloud in session.scalars(select(Cloud).where(Cloud.status == "deleting")):
                    try:
                        remove_directory(settings.clouds_dir, cloud.storage_key)
                        for job in session.scalars(select(ProcessingJob).where(ProcessingJob.cloud_id == cloud.id)):
                            remove_directory(settings.runtime_dir, str(job.id))
                        session.delete(cloud)
                    except OSError as problem:
                        cloud.status, cloud.error = "failed", f"Nie udało się dokończyć usuwania: {problem}"
                session.commit()
        process = None
        log_stream = None
        stop_file = settings.runtime_dir / f"stop-{uuid.uuid4()}"
        parent_lock_path = settings.runtime_dir / f"api-{uuid.uuid4()}.lock"
        parent_lock = FileLock(str(parent_lock_path))
        if settings.start_worker:
            parent_lock.acquire()
            log_stream = (settings.runtime_dir / "worker.log").open("ab")
            environment = dict(os.environ, DATABASE_URL=settings.database_url, DATA_DIR=str(settings.data_dir),
                POINTCLOUDS_DIR=str(settings.clouds_dir), RUNTIME_DIR=str(settings.runtime_dir),
                WORKER_STOP_FILE=str(stop_file), WORKER_PARENT_LOCK=str(parent_lock_path),
                PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            process = subprocess.Popen([sys.executable, "-m", "backend.worker"], cwd=settings.root,
                stdout=log_stream, stderr=subprocess.STDOUT, env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            yield
        finally:
            if process:
                stop_file.touch()
                try:
                    await anyio.to_thread.run_sync(lambda: process.wait(timeout=12))
                except subprocess.TimeoutExpired:
                    process.terminate()
                    await anyio.to_thread.run_sync(process.wait)
                stop_file.unlink(missing_ok=True)
                log_stream.close()
                parent_lock.release()
                parent_lock_path.unlink(missing_ok=True)
            database.engine.dispose()

    app = FastAPI(title="Cloud Transformation API", version="1.0.0", lifespan=lifespan)
    app.state.database = database
    app.state.settings = settings

    def get_session():
        with database.session() as session:
            yield session

    @app.exception_handler(HTTPException)
    async def http_error(_request, error):
        return JSONResponse({"error": error.detail}, status_code=error.status_code, headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, error):
        errors = [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
                  for item in error.errors()]
        return JSONResponse({"error": "Sprawdź podane dane.", "errors": errors}, status_code=422)

    @app.middleware("http")
    async def same_origin(request, call_next):
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"error": "Żądanie musi pochodzić z tej aplikacji."}, status_code=403)
            if request.method == "POST" and request.url.path in ("/api/clouds", "/api/conversions"):
                # Authenticate and reject oversized requests before multipart parsing can spool a file.
                with database.session() as session:
                    if not current_user(request, session, required=False):
                        return JSONResponse({"error": "Zaloguj się, aby przesłać plik."}, status_code=401)
                try:
                    length = int(request.headers.get("content-length", 0))
                except ValueError:
                    return JSONResponse({"error": "Nieprawidłowy rozmiar pliku."}, status_code=400)
                allowance = 1024 * 1024 if request.headers.get("content-type", "").startswith("multipart/") else 0
                if length > settings.max_upload_bytes + allowance:
                    return JSONResponse({"error": "Plik przekracza dopuszczalny rozmiar uploadu."}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/api/auth/status")
    def auth_status(session=Depends(get_session)):
        return {"needsSetup": session.scalar(select(func.count()).select_from(User)) == 0}

    @app.post("/api/auth/register", status_code=201, response_model=AccountRead)
    def register(body: Credentials, response: Response, session=Depends(get_session)):
        # Serialize the first-account adoption of legacy local clouds.
        with FileLock(str(settings.data_dir / "registration.lock"), timeout=30):
            first_account = session.scalar(select(func.count()).select_from(User)) == 0
            user = User(email=body.email, password_hash=hash_password(body.password))
            session.add(user)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(409, "Konto z tym adresem e-mail już istnieje.")
            if first_account:
                import_legacy(session, settings, user)
        start_session(session, user, response, settings)
        return {"user": user_json(user)}

    @app.post("/api/auth/login", response_model=AccountRead)
    def login(body: Credentials, response: Response, session=Depends(get_session)):
        user = session.scalar(select(User).where(User.email == body.email))
        if not user or not verify_password(body.password, user.password_hash):
            raise HTTPException(401, "Nieprawidłowy e-mail lub hasło.")
        start_session(session, user, response, settings)
        return {"user": user_json(user)}

    @app.get("/api/auth/me", response_model=AccountRead)
    def me(request: Request, session=Depends(get_session)):
        return {"user": user_json(current_user(request, session))}

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, response: Response, session=Depends(get_session)):
        token = request.cookies.get(COOKIE_NAME)
        if token:
            session.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(token)))
            session.commit()
        response.delete_cookie(COOKIE_NAME, path="/")

    @app.get("/api/health")
    def health(request: Request, session=Depends(get_session)):
        user = current_user(request, session, required=False)
        return {"ok": True, "backend": "python", "database": database.engine.dialect.name,
            "converterAvailable": settings.converter.is_file(),
            "segmentationAvailable": settings.laszip.is_file() and (settings.root / "tools/segment_pointcloud.py").is_file(),
            "transformationAvailable": settings.laszip.is_file() and (settings.root / "tools/transform_pointcloud.py").is_file(),
            "pointRemovalAvailable": settings.laszip.is_file() and (settings.root / "tools/remove_points.py").is_file(),
            "queuedJobs": session.scalar(select(func.count()).select_from(ProcessingJob).where(
                ProcessingJob.owner_id == user.id, ProcessingJob.status == "queued")) if user else 0}

    @app.get("/api/clouds", response_model=CloudList)
    def clouds(request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        records = session.scalars(select(Cloud).where(Cloud.owner_id == user.id, Cloud.status != "deleting")
                                  .order_by(Cloud.created_at.desc()))
        return {"clouds": [cloud_json(cloud, user, settings) for cloud in records]}

    @app.get("/api/clouds/{identifier}", response_model=CloudRead)
    def cloud_detail(identifier: str, request: Request, session=Depends(get_session)):
        user = current_user(request, session, required=False)
        return cloud_json(get_cloud(session, identifier, user), user, settings)

    @app.patch("/api/clouds/{identifier}", response_model=CloudRead)
    def update_cloud(identifier: str, body: CloudUpdate, request: Request, session=Depends(get_session)):
        with FileLock(str(settings.data_dir / "cloud-mutations.lock"), timeout=30):
            return update_cloud_record(identifier, body, request, session)

    def update_cloud_record(identifier, body, request, session):
        user = current_user(request, session)
        cloud = get_cloud(session, identifier, user, write=True)
        if body.name is not None:
            cloud.name = body.name
        if body.visibility is not None:
            cloud.visibility = body.visibility.value
        session.commit()
        return cloud_json(cloud, user, settings)

    @app.delete("/api/clouds/{identifier}", status_code=204)
    def delete_cloud(identifier: str, request: Request, session=Depends(get_session)):
        with FileLock(str(settings.data_dir / "cloud-mutations.lock"), timeout=30):
            return delete_cloud_record(identifier, request, session)

    def delete_cloud_record(identifier, request, session):
        user = current_user(request, session)
        cloud = get_cloud(session, identifier, user, write=True)
        if cloud.status in ACTIVE_STATUSES:
            raise HTTPException(409, "Zaczekaj na zakończenie przetwarzania przed usunięciem chmury.")
        dependent = session.scalar(select(ProcessingJob.id).where(
            ProcessingJob.source_cloud_id == cloud.id, ProcessingJob.status.in_(ACTIVE_STATUSES)).limit(1))
        if dependent:
            raise HTTPException(409, "Chmura jest używana przez trwające zadanie przetwarzania.")
        # Commit a tombstone before touching files; a worker cannot publish deleted data.
        cloud.status = "deleting"
        session.commit()
        try:
            remove_directory(settings.clouds_dir, cloud.storage_key)
            jobs = session.scalars(select(ProcessingJob).where(ProcessingJob.cloud_id == cloud.id))
            for job in jobs:
                remove_directory(settings.runtime_dir, str(job.id))
            session.delete(cloud)
            session.commit()
        except OSError as problem:
            session.rollback()
            cloud.status, cloud.error = "failed", f"Nie udało się usunąć plików: {problem}"
            session.commit()
            raise HTTPException(409, cloud.error)
        return Response(status_code=204)

    def upload_name(filename):
        if (not filename or len(filename) > 240 or any(char in filename for char in ('/', '\\', ':', '\0'))
                or Path(filename).suffix.lower() not in (".las", ".laz")):
            raise HTTPException(400, "Dozwolone są pliki LAS i LAZ z prawidłową nazwą.")
        return filename

    async def receive_upload(request, session, user, name, visibility, filename, chunks, declared_length=0):
        if not settings.converter.is_file():
            raise HTTPException(503, "Nie znaleziono PotreeConvertera.")
        if declared_length > settings.max_upload_bytes:
            raise HTTPException(413, "Plik przekracza dopuszczalny rozmiar uploadu.")
        cloud, job = create_job(session, user, name, visibility, upload_name(filename))
        work = child_path(settings.runtime_dir, str(job.id))
        work.mkdir()
        input_path = child_path(work, filename)
        received = 0
        try:
            async with await anyio.open_file(input_path, "xb") as stream:
                async for chunk in chunks:
                    received += len(chunk)
                    if received > settings.max_upload_bytes:
                        raise HTTPException(413, "Plik przekracza dopuszczalny rozmiar uploadu.")
                    await stream.write(chunk)
            if not received:
                raise HTTPException(400, "Przesłany plik jest pusty.")
            async with await anyio.open_file(input_path, "rb") as stream:
                header = await stream.read(227)
            if len(header) < 227 or header[:4] != b"LASF":
                raise HTTPException(400, "Plik nie ma prawidłowego nagłówka LAS/LAZ.")
            cloud.status, cloud.size_bytes = "queued", received
            job.status, job.upload_progress = "queued", 100
            session.commit()
            return job_json(job, cloud)
        except BaseException as problem:
            session.rollback()
            cloud.status, cloud.error = "failed", str(getattr(problem, "detail", problem))
            job.status, job.error = "failed", cloud.error
            session.commit()
            remove_directory(settings.runtime_dir, str(job.id))
            raise

    @app.post("/api/clouds", status_code=202, response_model=JobRead)
    async def upload(request: Request, file: UploadFile = File(...), name: CloudName = Form(...),
                     visibility: Visibility = Form(Visibility.PRIVATE), session=Depends(get_session)):
        user = current_user(request, session)
        async def chunks():
            while chunk := await file.read(1024 * 1024):
                yield chunk
        try:
            return await receive_upload(request, session, user, name, visibility.value, file.filename, chunks(), file.size or 0)
        finally:
            await file.close()

    @app.post("/api/conversions", status_code=202, include_in_schema=False)
    async def raw_upload(request: Request, filename: str, name: CloudName = Query(...),
                         visibility: Visibility = Visibility.PRIVATE, session=Depends(get_session)):
        user = current_user(request, session)
        try:
            length = int(request.headers.get("content-length", 0))
        except ValueError:
            raise HTTPException(400, "Nieprawidłowy rozmiar pliku.")
        return await receive_upload(request, session, user, name, visibility.value, filename, request.stream(), length)

    def operation_job(body, kind, request, session):
        with FileLock(str(settings.data_dir / "cloud-mutations.lock"), timeout=30):
            return create_operation_job(body, kind, request, session)

    def create_operation_job(body, kind, request, session):
        user = current_user(request, session)
        source = get_cloud(session, body.sourceCloud, user, write=True)
        if source.status != "ready" or not source.source_file:
            raise HTTPException(409, "Chmura źródłowa nie jest gotowa lub nie ma oryginalnego LAS/LAZ.")
        if not child_path(settings.clouds_dir, source.storage_key, source.source_file).is_file():
            raise HTTPException(409, "Nie znaleziono oryginalnego pliku LAS/LAZ.")
        if not settings.converter.is_file() or not settings.laszip.is_file():
            raise HTTPException(503, "Lokalny silnik przetwarzania jest niedostępny.")
        parameters = body.model_dump(mode="json", exclude={"name", "sourceCloud", "visibility"})
        cloud, job = create_job(session, user, body.name, body.visibility.value, f"{uuid.uuid4()}.laz",
                                kind=kind, source=source, parameters=parameters)
        return job_json(job, cloud)

    @app.post("/api/segmentations", status_code=202, response_model=JobRead)
    def segment(body: Segmentation, request: Request, session=Depends(get_session)):
        return operation_job(body, "segmentation", request, session)

    @app.post("/api/transformations", status_code=202, response_model=JobRead)
    def transform(body: Transformation, request: Request, session=Depends(get_session)):
        return operation_job(body, "transformation", request, session)

    @app.post("/api/point-removals", status_code=202, response_model=JobRead)
    def remove_points(body: PointRemoval, request: Request, session=Depends(get_session)):
        return operation_job(body, "point-removal", request, session)

    @app.get("/api/jobs", response_model=JobList)
    def jobs(request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        records = session.scalars(select(ProcessingJob).where(ProcessingJob.owner_id == user.id)
                                 .order_by(ProcessingJob.created_at.desc()).limit(100))
        return {"jobs": [job_json(job, session.get(Cloud, job.cloud_id)) for job in records]}

    @app.get("/api/jobs/{identifier}", response_model=JobRead)
    @app.get("/api/conversions/{identifier}", include_in_schema=False)
    def job_detail(identifier: uuid.UUID, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        job = session.get(ProcessingJob, identifier)
        if not job or job.owner_id != user.id:
            raise HTTPException(404, "Nie znaleziono zadania.")
        return job_json(job, session.get(Cloud, job.cloud_id))

    def serve_cloud_file(cloud, asset_path, user):
        if cloud.status != "ready":
            raise HTTPException(409, "Chmura nie jest gotowa do wyświetlenia.")
        if any(part.startswith(".") for part in Path(asset_path).parts) or "\\" in asset_path:
            raise HTTPException(404, "Nie znaleziono pliku.")
        try:
            directory = child_path(settings.clouds_dir, cloud.storage_key)
            target = child_path(directory, asset_path)
        except ValueError:
            raise HTTPException(404, "Nie znaleziono pliku.")
        owns = user is not None and user.id == cloud.owner_id
        if not owns and (asset_path == cloud.source_file or target.suffix == ".log" or target.name == "cloud-info.json"):
            raise HTTPException(404, "Nie znaleziono pliku.")
        if not target.is_file():
            raise HTTPException(404, "Nie znaleziono pliku.")
        # Starlette FileResponse handles HEAD and HTTP Range (206 / 416).
        return FileResponse(target, headers={"Cache-Control": "private, no-store"})

    @app.api_route("/api/clouds/{identifier}/files/{asset_path:path}", methods=["GET", "HEAD"])
    def cloud_file(identifier: str, asset_path: str, request: Request, session=Depends(get_session)):
        user = current_user(request, session, required=False)
        return serve_cloud_file(get_cloud(session, identifier, user), asset_path, user)

    @app.api_route("/pointclouds/{identifier}/{asset_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def legacy_file(identifier: str, asset_path: str, request: Request, session=Depends(get_session)):
        user = current_user(request, session, required=False)
        return serve_cloud_file(get_cloud(session, identifier, user), asset_path, user)

    @app.get("/")
    @app.get("/clouds", include_in_schema=False)
    def dashboard(request: Request, session=Depends(get_session)):
        if not current_user(request, session, required=False):
            return RedirectResponse("/login", status_code=303)
        return FileResponse(settings.root / "web" / "dashboard.html")

    @app.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(settings.root / "web" / "login.html")

    @app.get("/viewer", include_in_schema=False)
    def viewer(request: Request, cloud: str | None = None, session=Depends(get_session)):
        user = current_user(request, session, required=False)
        if cloud:
            get_cloud(session, cloud, user)
        elif not user:
            return RedirectResponse("/login", status_code=303)
        return FileResponse(settings.root / "index.html")

    @app.get("/index.html", include_in_schema=False)
    def old_entry():
        return RedirectResponse("/", status_code=303)

    # Explicit mounts prevent serving SQLite, Python source, tools, or runtime files.
    for prefix, directory in (("assets", "web/assets"), ("libs", "libs"), ("build", "build"), ("resources", "resources")):
        target = settings.root / directory
        if target.is_dir():
            app.mount(f"/{prefix}", StaticFiles(directory=target), name=prefix)
    return app
