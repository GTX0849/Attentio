"""`python -m backend` runs the API on 127.0.0.1:8000 (loopback only)."""

import uvicorn

from shared.config import load_config

if __name__ == "__main__":
    port = int(load_config().backend_url.rsplit(":", 1)[-1])
    uvicorn.run("backend.main:create_app", factory=True, host="127.0.0.1", port=port)
