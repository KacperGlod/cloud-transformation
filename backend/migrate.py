"""Apply database migrations: python -m backend.migrate."""
from .config import Settings
from .database import Database


if __name__ == "__main__":
    settings = Settings()
    settings.prepare()
    database = Database(settings)
    try:
        database.migrate()
    finally:
        database.engine.dispose()
