"""Settings that come from the environment on a cloud host: database URLs, listen address, timeouts."""

import pytest

from backend.__main__ import listen_address
from shared.config import http_timeout, normalise_database_url

NEON_QUERY = "sslmode=require&channel_binding=require"


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_a_provider_url_is_pointed_at_the_installed_psycopg_driver(scheme: str) -> None:
    url = f"{scheme}://user:p%40ss@ep-cool-123.ap-southeast-1.aws.neon.tech/neondb?{NEON_QUERY}"
    assert normalise_database_url(url) == (
        f"postgresql+psycopg://user:p%40ss@ep-cool-123.ap-southeast-1.aws.neon.tech/neondb?{NEON_QUERY}"
    )


def test_an_already_normalised_url_is_left_alone() -> None:
    url = "postgresql+psycopg://postgres@127.0.0.1:55432/attentio?sslmode=require"
    assert normalise_database_url(url) == url


def test_pasted_whitespace_around_the_url_is_dropped() -> None:
    assert normalise_database_url("  postgres://u:p@h/db\n") == "postgresql+psycopg://u:p@h/db"


def test_the_http_timeout_comes_from_the_environment_else_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATTENTIO_HTTP_TIMEOUT", raising=False)
    assert http_timeout(10) == 10
    monkeypatch.setenv("ATTENTIO_HTTP_TIMEOUT", "75")
    assert http_timeout(10) == 75
    for bad in ("soon", "0", "-5", "nan"):
        monkeypatch.setenv("ATTENTIO_HTTP_TIMEOUT", bad)
        assert http_timeout(10) == 10


# ---- python -m backend: where to listen ----------------------------------------------------------------------


def test_the_backend_listens_on_loopback_and_the_configured_port_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ATTENTIO_HOST", "PORT", "ATTENTIO_BACKEND_URL"):
        monkeypatch.delenv(name, raising=False)
    host, port = listen_address()
    assert host == "127.0.0.1"
    assert port == 8000  # the port in config/attentio.yaml backend.url


def test_the_backend_default_port_follows_the_configured_backend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("ATTENTIO_BACKEND_URL", "http://127.0.0.1:8123")
    assert listen_address()[1] == 8123


def test_a_cloud_host_sets_the_address_and_port_through_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTENTIO_HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "10000")
    assert listen_address() == ("0.0.0.0", 10000)


def test_a_port_that_is_not_a_number_is_named_in_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "ten-thousand")
    with pytest.raises(ValueError, match="PORT"):
        listen_address()
