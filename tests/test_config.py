from __future__ import annotations

from unittest.mock import patch

from kalshi_weather.config import get_settings


def test_get_settings_loads_dotenv_with_override_enabled(monkeypatch) -> None:
    monkeypatch.setenv("KALSHI_ENV", "demo")
    with patch("kalshi_weather.config.load_dotenv") as mocked_load_dotenv:
        _ = get_settings(load_dotenv_file=True)
    mocked_load_dotenv.assert_called_once_with(override=True)
