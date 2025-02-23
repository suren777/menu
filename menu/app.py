import os
import shutil
from fastapi import FastAPI
from menu.routes import api_router

from menu.config import ApplicationConfig


def create_app():
    app = FastAPI()
    app.include_router(api_router)
    return app


def start_server(host: str, port: str) -> None:
    args = [
        shutil.which("uvicorn"),
        "uvicorn",
        "menu.app:create_app",
        "--port",
        f"{port}",
    ]
    os.execl(*[str(v) for v in args])


if __name__ == "__main__":
    config = ApplicationConfig()
    start_server(host=config.host, port=config.port)
