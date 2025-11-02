from unittest.mock import MagicMock, patch

from menu.config import ApplicationConfig
from menu.webapp import create_app, main, start_server


def test_create_app():
    app = create_app()
    assert app is not None


@patch("os.execl")
@patch("shutil.which", return_value="/path/to/uvicorn")
def test_start_server(mock_which, mock_execl):
    start_server("localhost", "8000")
    mock_execl.assert_called_once_with(
        "/path/to/uvicorn",
        "uvicorn",
        "menu.app:create_app",
        "--port",
        "8000",
        "--host",
        "localhost",
        "--reload",
        "--log-level",
        "info",
    )


def test_main():
    mock_runner = MagicMock()
    main(server_runner=mock_runner)
    config = ApplicationConfig()
    mock_runner.assert_called_once_with(config.host, config.port)
