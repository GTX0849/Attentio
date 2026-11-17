"""CLASSPULSE_BACKEND_URL overrides backend.url from the shared config."""

import pytest

from shared.config import load_config


def test_the_config_file_url_is_used_when_the_variable_is_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLASSPULSE_BACKEND_URL", raising=False)
    assert load_config().backend_url == "http://127.0.0.1:8000"


def test_the_environment_variable_overrides_the_url_and_loses_a_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLASSPULSE_BACKEND_URL", "http://127.0.0.1:8012/")
    assert load_config().backend_url == "http://127.0.0.1:8012"
