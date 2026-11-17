"""Admin view (placeholder, built in a later ticket)."""

import streamlit as st

from shared.config import Config


def render(config: Config) -> None:
    st.title("Admin view")
    st.info("Coming soon: rooms, cameras and student administration.")
