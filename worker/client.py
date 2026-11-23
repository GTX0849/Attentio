"""Sends records to the backend over HTTP."""

import requests

from shared.config import http_timeout
from shared.schema import AttentionRecord
from worker.delivery import SendError

SERVICE_KEY_HEADER = "X-Attentio-Key"
NO_KEY_MESSAGE = (
    "ATTENTIO_SERVICE_KEY is not set, so the backend will refuse these records. Put the same "
    "key in .env for the backend and the worker (see Signing in in the README). Records stay in the "
    "spool until it is set."
)


class HttpSender:
    """Posts windows to `/api/records`, with the service key in `X-Attentio-Key` when there is one.

    `timeout` defaults to 3 s, or ATTENTIO_HTTP_TIMEOUT when set. Keep it short: a send that times out
    (a sleeping free host, say) leaves the records in the spool, which retries them.
    """

    def __init__(self, backend_url: str, timeout: float | None = None, service_key: str | None = None) -> None:
        self._url = backend_url.rstrip("/") + "/api/records"
        self._timeout = timeout if timeout is not None else http_timeout(3.0)
        self._headers = {SERVICE_KEY_HEADER: service_key} if service_key else {}

    def send(self, records: list[AttentionRecord]) -> None:
        payload = [r.model_dump(mode="json") for r in records]
        try:
            resp = requests.post(self._url, json=payload, headers=self._headers, timeout=self._timeout)
        except requests.RequestException as e:
            raise SendError(f"backend unreachable: {e}") from e
        if resp.status_code in (401, 403):
            # A missing or wrong key is fixed by configuration, so keep the records and retry.
            reason = "the service key is wrong" if self._headers else NO_KEY_MESSAGE
            raise SendError(f"backend refused the records ({resp.status_code}): {reason}")
        if resp.status_code >= 500:
            raise SendError(f"backend error {resp.status_code}")
        if resp.status_code >= 400:
            # 404 or 422: the data itself is wrong, so retrying cannot help.
            raise SendError(f"rejected ({resp.status_code}): {resp.text[:200]}", permanent=True)
