"""ClassPulse dashboard. Run with: streamlit run dashboard/app.py"""

import sys
from pathlib import Path

# `streamlit run` puts only this folder on sys.path; add the project root so
# `dashboard` and `shared` import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from dashboard.sidebar import render_sidebar  # noqa: E402
from dashboard.views import admin, student, teacher  # noqa: E402
from shared.config import load_config  # noqa: E402

VIEWS = {"Student": student, "Teacher": teacher, "Admin": admin}

st.set_page_config(page_title="ClassPulse", layout="wide")
config = load_config()

role = render_sidebar(config)
VIEWS[role].render(config)
