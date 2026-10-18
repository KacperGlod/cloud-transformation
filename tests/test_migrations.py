from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, inspect, select

from backend.config import Settings
from backend.database import Database
from backend.models import Cloud, ProcessingJob, User, utcnow


def test_soft_delete_migration_preserves_existing_clouds_and_jobs(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", clouds_dir=tmp_path / "clouds",
                        runtime_dir=tmp_path / "runtime", database_url="", start_worker=False)
    settings.prepare()
    database = Database(settings)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "backend" / "migrations"))
    owner_id, parent_id, child_id, job_id = (uuid.uuid4() for _ in range(4))
    created = utcnow()
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0001")
            metadata = MetaData()
            users = Table("users", metadata, autoload_with=connection)
            clouds = Table("clouds", metadata, autoload_with=connection)
            jobs = Table("processing_jobs", metadata, autoload_with=connection)
            connection.execute(users.insert(), {"id": owner_id.hex, "email": "migration@example.test",
                "password_hash": "unused", "created_at": created})
            for identifier, parent in ((parent_id, None), (child_id, parent_id.hex)):
                connection.execute(clouds.insert(), {"id": identifier.hex, "owner_id": owner_id.hex,
                    "name": "Istniejąca chmura", "visibility": "private", "status": "ready",
                    "storage_key": str(identifier), "source_file": "original.las", "entry_file": "metadata.json",
                    "size_bytes": 1234, "parent_id": parent, "created_at": created, "updated_at": created})
            connection.execute(jobs.insert(), {"id": job_id.hex, "cloud_id": child_id.hex, "owner_id": owner_id.hex,
                "source_cloud_id": parent_id.hex, "kind": "transformation", "status": "completed",
                "progress": 100, "parameters": {"translation": [1, 2, 3]}, "result": {"pointCount": 10},
                "log": "Zakończono", "created_at": created, "updated_at": created})
        database.migrate()
        database.migrate()
        with database.session() as session:
            parent = session.get(Cloud, parent_id)
            child = session.get(Cloud, child_id)
            job = session.get(ProcessingJob, job_id)
            assert session.get(User, owner_id).email == "migration@example.test"
            assert parent.deleted_at is None and child.deleted_at is None
            assert parent.processing_file is None and child.processing_filename == "original.las"
            assert parent.status == "ready" and child.parent_id == parent_id
            assert child.source_file == "original.las" and child.size_bytes == 1234
            assert job.source_cloud_id == parent_id and job.parameters == {"translation": [1, 2, 3]}
            assert job.status == "completed" and job.result == {"pointCount": 10}
            assert set(session.scalars(select(Cloud.id))) == {parent_id, child_id}
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.check(config)
            command.downgrade(config, "0001")
            assert "deleted_at" not in {column["name"] for column in inspect(connection).get_columns("clouds")}
        database.migrate()
        with database.session() as session:
            assert session.get(Cloud, child_id).parent_id == parent_id
            assert session.get(ProcessingJob, job_id).result == {"pointCount": 10}
    finally:
        database.engine.dispose()


def test_processing_file_migration_preserves_originals_and_existing_schema(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", clouds_dir=tmp_path / "clouds",
                        runtime_dir=tmp_path / "runtime", database_url="", start_worker=False)
    settings.prepare()
    database = Database(settings)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "backend" / "migrations"))
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0002")
        database.migrate()
        with database.session() as session:
            user = User(email="ply-migration@example.test", password_hash="unused")
            session.add(user)
            session.flush()
            cloud = Cloud(owner_id=user.id, name="PLY", status="ready", storage_key="ply-migration",
                          source_file="original.ply", processing_file="working.laz")
            session.add(cloud)
            session.commit()
            identifier = cloud.id
        database.migrate()
        with database.session() as session:
            cloud = session.get(Cloud, identifier)
            assert cloud.source_file == "original.ply" and cloud.processing_filename == "working.laz"
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.check(config)
            command.downgrade(config, "0002")
            assert "processing_file" not in {column["name"] for column in inspect(connection).get_columns("clouds")}
            clouds = Table("clouds", MetaData(), autoload_with=connection)
            assert connection.scalar(select(clouds.c.source_file)) == "original.ply"
        database.migrate()
        with database.session() as session:
            cloud = session.get(Cloud, identifier)
            assert cloud.source_file == "original.ply" and cloud.processing_file is None
    finally:
        database.engine.dispose()
