"""ClassPulse dashboard. Run with: streamlit run dashboard/app.py

Before sign-in the app shows only the sign-in page. After it, the role that comes back from the
backend decides the view: a student sees their own, a teacher theirs, an admin the overview.
"""

import sys
from pathlib import Path

# `streamlit run` puts only this folder on sys.path; add the project root so
# `dashboard` and `shared` import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from dashboard import session, signin  # noqa: E402
from dashboard.cloud import copy_secrets_to_environ  # noqa: E402
from dashboard.session import Identity  # noqa: E402
from dashboard.sidebar import render_sidebar  # noqa: E402
from dashboard.views import admin, student, teacher  # noqa: E402
from shared.config import Config, load_config  # noqa: E402

VIEWS = {"student": student, "teacher": teacher, "admin": admin}


@session.ends_on_expiry
def render_signed_in(config: Config, identity: Identity) -> None:
    render_sidebar(config, identity)
    VIEWS[identity.role].render(config)


st.set_page_config(page_title="ClassPulse", layout="wide")
copy_secrets_to_environ()  # hosted: Streamlit secrets become the settings load_config reads
_config = load_config()
_identity = session.current()
if _identity is None or _identity.role not in VIEWS:
    signin.render(_config)
else:
    render_signed_in(_config, _identity)
