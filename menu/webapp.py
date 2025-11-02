import os
import shutil
from fastapi import FastAPI
from menu.routes import api_router
from typing import Callable

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
        "--host",
        f"{host}",
        "--reload",
        "--log-level",
        "info",
    ]
    os.execl(*[str(v) for v in args])


def main(server_runner: Callable[[str, str], None] = start_server):
    config = ApplicationConfig()
    server_runner(host=config.host, port=config.port)


if __name__ == "__main__":
    main()
