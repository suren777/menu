from unittest.mock import patch, MagicMock
from menu.teleapp import main

@patch('menu.teleapp.telegram_bot')
def test_main(mock_telegram_bot):
    mock_app = MagicMock()
    mock_telegram_bot.build.return_value = mock_app

    # To prevent the app from running forever during the test
    mock_app.run_polling.return_value = None

    main()

    mock_telegram_bot.build.assert_called_once()
    assert mock_app.add_handler.call_count == 4
    mock_app.run_polling.assert_called_once()
