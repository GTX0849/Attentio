"""Sends finished windows to the backend, spooling them to disk when that fails."""

import logging
import os
from pathlib import Path
from typing import Protocol

from shared.schema import AttentionRecord

log = logging.getLogger("worker.delivery")


class SendError(Exception):
    """A send failed. `permanent` means retrying can never help (the backend rejected the data)."""

    def __init__(self, message: str, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class Sender(Protocol):
    def send(self, records: list[AttentionRecord]) -> None:
        """Deliver the records or raise SendError."""
        ...


class Spool:
    """Unsent records, one JSON object per line."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def append(self, records: list[AttentionRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            for r in records:
                f.write(r.model_dump_json() + "\n")
            f.flush()
            os.fsync(f.fileno())

    def load(self) -> list[AttentionRecord]:
        if not self._path.exists():
            return []
        records: list[AttentionRecord] = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    records.append(AttentionRecord.model_validate_json(line))
                except ValueError:
                    log.warning("Skipping unreadable line in spool %s", self._path)
        return records

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)


class Delivery:
    """Writes records to the spool first, then tries to send everything spooled.

    Because a record is on disk before the send is attempted, a crash or a
    backend outage never loses a window.
    """

    def __init__(self, sender: Sender, spool: Spool) -> None:
        self._sender = sender
        self._spool = spool

    def deliver(self, records: list[AttentionRecord]) -> None:
        if records:
            self._spool.append(records)
        self.retry_pending()

    def retry_pending(self) -> None:
        pending = self._spool.load()
        if not pending:
            return
        try:
            self._sender.send(pending)
        except SendError as e:
            if e.permanent:
                log.error("Backend rejected %d record(s), dropping them: %s", len(pending), e)
                self._spool.clear()
            else:
                log.warning("Could not send %d record(s), kept for retry: %s", len(pending), e)
            return
        self._spool.clear()
