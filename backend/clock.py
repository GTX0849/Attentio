"""Time helpers: the injectable clock and the configurable local time zone.

The timetable is written in local wall-clock time. Attention records and
lectures are stored in UTC. The zone comes from the ATTENTIO_TIMEZONE
environment variable and defaults to Asia/Kolkata.
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from shared.config import PROJECT_ROOT

DEFAULT_TIMEZONE = "Asia/Kolkata"
TIMEZONE_ENV = "ATTENTIO_TIMEZONE"

Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(UTC)


def resolve_timezone(name: str | None = None) -> ZoneInfo:
    """The IANA zone for `name`, else ATTENTIO_TIMEZONE, else Asia/Kolkata."""
    if name is None:
        load_dotenv(PROJECT_ROOT / ".env")  # lets ATTENTIO_TIMEZONE live in .env; real env vars win
    chosen = name or os.environ.get(TIMEZONE_ENV) or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(chosen)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(
            f"Unknown time zone '{chosen}'. Use an IANA name such as {DEFAULT_TIMEZONE}."
        ) from e
