from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, inspect, select

from backend.config import Settings
from backend.database import Database
from backend.models import Cloud, CloudCollection, CollectionCloud, ProcessingJob, Project, User, utcnow


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


def test_project_migration_preserves_clouds_and_matches_models(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", clouds_dir=tmp_path / "clouds",
                        runtime_dir=tmp_path / "runtime", database_url="", start_worker=False)
    settings.prepare()
    database = Database(settings)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "backend" / "migrations"))
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0003")
        with database.session() as session:
            user = User(email="project-migration@example.test", password_hash="unused")
            session.add(user)
            session.flush()
            cloud = Cloud(owner_id=user.id, name="Istniejąca chmura", status="ready", storage_key="project-migration",
                          source_file="original.laz", processing_file="working.laz")
            session.add(cloud)
            session.commit()
            owner_id, cloud_id = user.id, cloud.id
        database.migrate()
        with database.session() as session:
            project = Project(owner_id=owner_id, cloud_id=cloud_id, name="Zapis", scene_state={"schemaVersion": 1})
            session.add(project)
            session.commit()
            identifier = project.id
        database.migrate()
        with database.session() as session:
            assert session.get(Project, identifier).revision == 1
            assert session.get(Project, identifier).scene_state == {"schemaVersion": 1}
            assert session.get(Cloud, cloud_id).source_file == "original.laz"
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.check(config)
            indexes = {index["name"] for index in inspect(connection).get_indexes("projects")}
            assert indexes == {"ix_projects_cloud_id", "ix_project_owner_updated"}
            assert {key["referred_table"] for key in inspect(connection).get_foreign_keys("projects")} == {"users", "clouds"}
            command.downgrade(config, "0003")
            assert "projects" not in inspect(connection).get_table_names()
        database.migrate()
        with database.session() as session:
            assert session.get(Cloud, cloud_id).processing_file == "working.laz"
            assert session.get(User, owner_id).email == "project-migration@example.test"
    finally:
        database.engine.dispose()


def test_collection_migration_is_optional_preserves_projects_and_cascades_memberships(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", clouds_dir=tmp_path / "clouds",
                        runtime_dir=tmp_path / "runtime", database_url="", start_worker=False)
    settings.prepare()
    database = Database(settings)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "backend" / "migrations"))
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0004")
        with database.session() as session:
            user = User(email="collection-migration@example.test", password_hash="unused")
            session.add(user)
            session.flush()
            cloud = Cloud(owner_id=user.id, name="Istniejąca chmura", status="ready", storage_key="collection-migration")
            session.add(cloud)
            session.flush()
            project = Project(owner_id=user.id, cloud_id=cloud.id, name="Zapisany widok", scene_state={"schemaVersion": 1})
            session.add(project)
            session.commit()
            owner_id, cloud_id, project_id = user.id, cloud.id, project.id
        database.migrate()
        database.migrate()
        with database.session() as session:
            assert session.scalar(select(CloudCollection)) is None
            assert session.scalar(select(CollectionCloud)) is None
            assert session.get(Project, project_id).cloud_id == cloud_id
            collection = CloudCollection(owner_id=owner_id, name="Kolekcja")
            session.add(collection)
            session.flush()
            session.add(CollectionCloud(collection_id=collection.id, cloud_id=cloud_id))
            session.commit()
            identifier = collection.id
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.check(config)
            assert {key["referred_table"] for key in inspect(connection).get_foreign_keys("collection_clouds")} == {"cloud_collections", "clouds"}
        with database.session() as session:
            session.delete(session.get(CloudCollection, identifier))
            session.commit()
            assert session.scalar(select(CollectionCloud)) is None
            assert session.get(Cloud, cloud_id) is not None
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0004")
            assert "cloud_collections" not in inspect(connection).get_table_names()
            assert "collection_clouds" not in inspect(connection).get_table_names()
        database.migrate()
        with database.session() as session:
            assert session.get(Project, project_id).name == "Zapisany widok"
            assert session.get(Cloud, cloud_id).name == "Istniejąca chmura"
    finally:
        database.engine.dispose()


def test_collection_details_migration_preserves_existing_memberships_and_projects(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", clouds_dir=tmp_path / "clouds",
                        runtime_dir=tmp_path / "runtime", database_url="", start_worker=False)
    settings.prepare()
    database = Database(settings)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "backend" / "migrations"))
    collection_id = uuid.uuid4()
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0005")
        with database.session() as session:
            user = User(email="details-migration@example.test", password_hash="unused")
            session.add(user)
            session.flush()
            cloud = Cloud(owner_id=user.id, name="Chmura", status="ready", storage_key="details-migration")
            session.add(cloud)
            session.flush()
            project = Project(owner_id=user.id, cloud_id=cloud.id, name="Widok", scene_state={"schemaVersion": 1})
            session.add(project)
            session.commit()
            owner_id, cloud_id, project_id = user.id, cloud.id, project.id
        with database.engine.begin() as connection:
            collections = Table("cloud_collections", MetaData(), autoload_with=connection)
            connection.execute(collections.insert(), {"id": collection_id.hex, "owner_id": owner_id.hex,
                "name": "Istniejąca kolekcja", "created_at": utcnow(), "updated_at": utcnow()})
            memberships = Table("collection_clouds", MetaData(), autoload_with=connection)
            connection.execute(memberships.insert(), {"collection_id": collection_id.hex, "cloud_id": cloud_id.hex})
        database.migrate()
        database.migrate()
        with database.session() as session:
            collection = session.get(CloudCollection, collection_id)
            assert collection.description == collection.notes == ""
            assert collection.name == "Istniejąca kolekcja"
            assert session.get(CollectionCloud, (collection_id, cloud_id)) is not None
            collection.description, collection.notes = "Opis", "Pierwsza linia\nDruga linia"
            session.commit()
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.check(config)
            command.downgrade(config, "0005")
            columns = {column["name"] for column in inspect(connection).get_columns("cloud_collections")}
            assert "description" not in columns and "notes" not in columns
            assert connection.scalar(select(Table("collection_clouds", MetaData(), autoload_with=connection).c.cloud_id)) == cloud_id.hex
        database.migrate()
        with database.session() as session:
            assert session.get(CloudCollection, collection_id).name == "Istniejąca kolekcja"
            assert session.get(CloudCollection, collection_id).description == ""
            assert session.get(CollectionCloud, (collection_id, cloud_id)) is not None
            assert session.get(Project, project_id).name == "Widok"
    finally:
        database.engine.dispose()
