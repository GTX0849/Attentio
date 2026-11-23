"""Settings on a hosted dashboard (Streamlit Community Cloud): its "secrets" become environment variables."""

import os
from collections.abc import Mapping, MutableMapping
from typing import Any

import streamlit as st


def copy_secrets_to_environ(secrets: Mapping[str, Any] | None = None, environ: MutableMapping[str, str] | None = None) -> None:
    """Copy root-level string secrets into `environ` (default `os.environ`) when not already set there.

    `shared.config` reads its settings (CLASSPULSE_BACKEND_URL, CLASSPULSE_HTTP_TIMEOUT) from the
    environment, so this is what lets the same code run locally from `.env` and hosted from secrets.
    Sections (tables) and non-strings are skipped. With no secrets file, `st.secrets` raises; that is
    not an error here. Values are never printed.
    """
    target = os.environ if environ is None else environ
    try:
        source = st.secrets if secrets is None else secrets
        for key, value in source.items():
            if isinstance(value, str) and key not in target:
                target[key] = value
    except Exception:  # noqa: BLE001  (no secrets file locally: st.secrets raises on first use)
        return
