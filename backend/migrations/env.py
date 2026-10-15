from alembic import context
from backend.config import Settings
from backend.database import Database
from backend.models import Base


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=Settings().database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    migrate(context.config.attributes["connection"])
else:
    settings = Settings()
    settings.prepare()
    database = Database(settings)
    try:
        with database.engine.connect() as connection:
            migrate(connection)
    finally:
        database.engine.dispose()
