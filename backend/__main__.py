"""`python -m backend` runs the API.

Locally it listens on 127.0.0.1 (loopback only), on the port in config `backend.url` (8000).
A host sets `CLASSPULSE_HOST` and `PORT` instead: Render needs `CLASSPULSE_HOST=0.0.0.0` (it sets
`PORT` itself), otherwise nothing outside the machine can reach the service.
"""

import uvicorn

from shared.config import env_setting, load_config

HOST_ENV = "CLASSPULSE_HOST"
PORT_ENV = "PORT"
LOOPBACK = "127.0.0.1"


def listen_address() -> tuple[str, int]:
    """The (host, port) to listen on: CLASSPULSE_HOST (default loopback) and PORT (default: the port in config)."""
    host = env_setting(HOST_ENV) or LOOPBACK
    raw_port = env_setting(PORT_ENV)
    if raw_port is None:
        return host, int(load_config().backend_url.rsplit(":", 1)[-1])
    try:
        return host, int(raw_port)
    except ValueError:
        raise ValueError(f"{PORT_ENV} must be a port number, not {raw_port!r}") from None


if __name__ == "__main__":
    _host, _port = listen_address()
    uvicorn.run("backend.main:create_app", factory=True, host=_host, port=_port)
