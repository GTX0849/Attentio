"""The dashboard on a cloud host: settings from Streamlit secrets, the HTTP timeout, and a sleeping backend."""

from collections.abc import Iterator, Mapping
from typing import Any

import pytest
import requests
from streamlit.testing.v1 import AppTest

from dashboard import api, signin
from dashboard.cloud import copy_secrets_to_environ
from tests.test_dashboard_render import APP, run_app

WAKING = "The backend is waking up (free hosting sleeps when idle). Refresh in a moment"


class FakeSecrets(Mapping[str, Any]):
    """Stands in for `st.secrets`: a mapping that can hold sections (tables) as well as plain strings."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class MissingSecrets(Mapping[str, Any]):
    """What `st.secrets` does with no secrets file: every read raises."""

    def __getitem__(self, key: str) -> Any:
        raise FileNotFoundError("No secrets found")

    def __iter__(self) -> Iterator[str]:
        raise FileNotFoundError("No secrets found")

    def __len__(self) -> int:
        raise FileNotFoundError("No secrets found")


# ---- secrets -> environment -----------------------------------------------------------------------------------


def test_root_level_string_secrets_are_copied_into_the_environment() -> None:
    environ: dict[str, str] = {}
    copy_secrets_to_environ(
        FakeSecrets({"CLASSPULSE_BACKEND_URL": "https://cp.example.test", "CLASSPULSE_HTTP_TIMEOUT": "75"}), environ
    )
    assert environ == {"CLASSPULSE_BACKEND_URL": "https://cp.example.test", "CLASSPULSE_HTTP_TIMEOUT": "75"}


def test_a_setting_that_is_already_in_the_environment_wins() -> None:
    environ = {"CLASSPULSE_BACKEND_URL": "http://127.0.0.1:8000"}
    copy_secrets_to_environ(FakeSecrets({"CLASSPULSE_BACKEND_URL": "https://cp.example.test"}), environ)
    assert environ == {"CLASSPULSE_BACKEND_URL": "http://127.0.0.1:8000"}


def test_only_root_level_strings_are_copied_not_sections_or_numbers() -> None:
    environ: dict[str, str] = {}
    copy_secrets_to_environ(
        FakeSecrets({"section": {"CLASSPULSE_BACKEND_URL": "x"}, "retries": 3, "NAME": "kept"}), environ
    )
    assert environ == {"NAME": "kept"}


def test_no_secrets_at_all_is_fine() -> None:
    environ: dict[str, str] = {}
    copy_secrets_to_environ(MissingSecrets(), environ)
    assert environ == {}


# ---- the HTTP timeout and the sleeping backend ----------------------------------------------------------------


def test_the_dashboard_waits_ten_seconds_unless_told_otherwise(monkeypatch: pytest.MonkeyPatch) -> None:
    waited: list[float] = []

    def request(method: str, url: str, **kwargs: Any) -> Any:
        waited.append(kwargs["timeout"])
        raise requests.Timeout("slow")

    monkeypatch.setattr(requests, "request", request)
    monkeypatch.delenv("CLASSPULSE_HTTP_TIMEOUT", raising=False)
    with pytest.raises(api.BackendError):
        api.list_rooms("http://b")
    monkeypatch.setenv("CLASSPULSE_HTTP_TIMEOUT", "75")
    with pytest.raises(api.BackendError):
        api.list_rooms("http://b")
    assert waited == [10, 75]


def test_a_sign_in_waits_as_long_as_the_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    waited: list[float] = []

    def post(url: str, **kwargs: Any) -> Any:
        waited.append(kwargs["timeout"])
        raise requests.Timeout("slow")

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setenv("CLASSPULSE_HTTP_TIMEOUT", "75")
    with pytest.raises(api.SignInError):
        api.login("http://b", "a@x.test", "pw")
    assert waited == [75]


@pytest.mark.parametrize("failure", [requests.Timeout("read timed out"), requests.ConnectionError("refused")])
def test_a_backend_that_is_asleep_gets_a_plain_message_in_the_view(
    monkeypatch: pytest.MonkeyPatch, failure: requests.RequestException
) -> None:
    def request(method: str, url: str, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(requests, "request", request)
    at = run_app()
    assert not at.exception
    errors = [e.value for e in at.error]
    assert errors and all(e == WAKING for e in errors)
    assert not any("refused" in e or "timed out" in e or "http" in e for e in errors)


def test_the_app_takes_its_backend_address_from_streamlit_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSPULSE_BACKEND_URL", "placeholder")
    monkeypatch.delenv("CLASSPULSE_BACKEND_URL")  # unset now, and put back (unset) when the test ends
    tried: list[str] = []

    def login(url: str, email: str, password: str) -> dict[str, Any]:
        tried.append(url)
        raise api.SignInError("invalid")

    monkeypatch.setattr(api, "login", login)
    at = AppTest.from_file(APP, default_timeout=20)
    at.secrets["CLASSPULSE_BACKEND_URL"] = "https://cp.example.test/"
    at.run()
    at.text_input(key=signin.EMAIL_KEY).input("a@example.edu")
    at.text_input(key=signin.PASSWORD_KEY).input("whatever-1")
    at.button[0].click()
    at.run()
    assert tried == ["https://cp.example.test"]
