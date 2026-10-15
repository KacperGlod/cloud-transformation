from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .config import Settings


class Database:
    def __init__(self, settings: Settings):
        kwargs = {}
        if settings.database_url.startswith("sqlite:"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(settings.database_url, **kwargs)
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self):
        configuration = Config()
        configuration.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as connection:
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "head")
