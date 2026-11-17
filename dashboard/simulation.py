"""The "this is simulated data" banner, worded from what the backend says is loaded."""

from typing import Any

from dashboard import api, theme
from dashboard.api import BackendError
from shared.config import Config

FALLBACK = "Simulated data — generated, not real classroom records"


def banner_text(simulation: dict[str, Any] | None) -> str:
    """"Simulated data — generated cohort of 60 students over 4 weeks, not real classroom records"."""
    if not simulation or not simulation.get("students"):
        return FALLBACK
    students = simulation["students"]
    weeks = simulation.get("weeks")
    span = f" over {weeks} week{'s' if weeks != 1 else ''}" if weeks else ""
    return f"Simulated data — generated cohort of {students} students{span}, not real classroom records"


def show_banner(config: Config) -> None:
    """Draw the banner. Call at the top of any view that shows simulated data."""
    try:
        text = banner_text(api.get_simulation(config.backend_url))
    except BackendError:
        text = FALLBACK
    theme.simulated_banner(text)
