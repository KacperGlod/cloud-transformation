"""Start the Python API, web application and local processing worker."""
import argparse
import os

import uvicorn

from backend.app import create_app
from backend.config import Settings


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cloud Transformation")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "1234")))
    parser.add_argument("--no-worker", action="store_true", help="Start only the API; run backend.worker separately")
    arguments = parser.parse_args()
    settings = Settings()
    if arguments.no_worker:
        settings.start_worker = False
    uvicorn.run(create_app(settings), host=arguments.host, port=arguments.port)
