"""Sends records to the backend over HTTP."""

import requests

from shared.schema import AttentionRecord
from worker.delivery import SendError


class HttpSender:
    def __init__(self, backend_url: str, timeout: float = 3.0) -> None:
        self._url = backend_url.rstrip("/") + "/api/records"
        self._timeout = timeout

    def send(self, records: list[AttentionRecord]) -> None:
        payload = [r.model_dump(mode="json") for r in records]
        try:
            resp = requests.post(self._url, json=payload, timeout=self._timeout)
        except requests.RequestException as e:
            raise SendError(f"backend unreachable: {e}") from e
        if resp.status_code >= 500:
            raise SendError(f"backend error {resp.status_code}")
        if resp.status_code >= 400:
            # 404 or 422: the data itself is wrong, so retrying cannot help.
            raise SendError(f"rejected ({resp.status_code}): {resp.text[:200]}", permanent=True)
