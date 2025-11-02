"""This module contains the web application for the menu."""
import os
import shutil
from typing import Callable

from fastapi import FastAPI

from menu.config import ApplicationConfig
from menu.routes import api_router


def create_app():
    """Create the FastAPI application."""
    app = FastAPI()
    app.include_router(api_router)
    return app


def start_server(host: str, port: str) -> None:
    """Start the uvicorn server."""
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
    """The main entry point for the application."""
    config = ApplicationConfig()
    server_runner(config.host, config.port)


if __name__ == "__main__":
    main()
