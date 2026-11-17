"""Design tokens and small presentational helpers shared by every dashboard view.

Colours come from the ClassPulse design. The page theme itself (primary colour,
background, fonts) lives in `.streamlit/config.toml`; this module holds the
colours that Streamlit's theme has no slot for and the helpers that draw the
repeated pieces: header, stat card, simulated banner and the states legend.

Helpers that draw take plain strings and escape them. The `*_html` functions
return the markup so tests can check it without running Streamlit.
"""

from html import escape
from pathlib import Path

import altair as alt
import streamlit as st

# The four attention states, in display order: label -> colour.
STATE_COLOURS: dict[str, str] = {
    "Attentive": "#1B5FD0",
    "Neutral": "#9DB3D6",
    "Distracted": "#D4622A",
    "Not visible": "#C9CDD2",
}
# One line per state for the legend.
STATE_DESCRIPTIONS: dict[str, str] = {
    "Attentive": "Facing the board, eyes open",
    "Neutral": "Note-taking, up to 8 s",
    "Distracted": "Looking away or eyes closed",
    "Not visible": "Out of view, not scored",
}
# API field name -> state label, for charts built from the backend's records.
STATE_FIELDS: dict[str, str] = {
    "attentive": "Attentive",
    "neutral": "Neutral",
    "distracted": "Distracted",
    "not_visible": "Not visible",
}

PRIMARY = "#1B5FD0"
TEXT = "#18212B"
SECONDARY_TEXT = "#525C66"
CARD_BACKGROUND = "#F8F9F7"
BORDER = "#DFE2DD"
BANNER_BACKGROUND = "#FFF4DA"
BANNER_TEXT = "#7A4E00"


def state_scale() -> alt.Scale:
    """Altair colour scale for the four states, keyed by the labels in STATE_COLOURS."""
    return alt.Scale(domain=list(STATE_COLOURS), range=list(STATE_COLOURS.values()))


def state_color(field: str = "state") -> alt.Color:
    """An Altair colour encoding for a column holding state labels, legend titled "State"."""
    return alt.Color(f"{field}:N", scale=state_scale(), title="State", sort=list(STATE_COLOURS))


# ---- markup builders ------------------------------------------------------------


def stat_card_html(label: str, figure: str, comparison: str, accent: bool = False) -> str:
    figure_colour = PRIMARY if accent else TEXT
    return (
        f'<div style="background:{CARD_BACKGROUND};border:1px solid {BORDER};border-radius:12px;'
        f'padding:16px 18px;">'
        f'<div style="font-size:13px;color:{SECONDARY_TEXT};">{escape(label)}</div>'
        f'<div style="font-size:30px;font-weight:600;line-height:1.2;margin:4px 0;'
        f"font-variant-numeric:tabular-nums;color:{figure_colour};\">{escape(figure)}</div>"
        f'<div style="font-size:13px;color:{SECONDARY_TEXT};">{escape(comparison)}</div>'
        f"</div>"
    )


def page_header_html(eyebrow: str, title: str, context_line: str = "") -> str:
    context = (
        f'<div style="font-size:14px;color:{SECONDARY_TEXT};margin-top:6px;">'
        f"{escape(context_line)}</div>"
        if context_line
        else ""
    )
    return (
        f'<div style="margin:0 0 16px 0;">'
        f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:0.12em;'
        f'color:{SECONDARY_TEXT};">{escape(eyebrow)}</div>'
        f'<div style="font-size:30px;font-weight:600;line-height:1.25;color:{TEXT};">'
        f"{escape(title)}</div>{context}</div>"
    )


def simulated_banner_html(text: str) -> str:
    return (
        f'<div style="background:{BANNER_BACKGROUND};color:{BANNER_TEXT};border-radius:8px;'
        f'padding:8px 14px;font-size:14px;margin:0 0 16px 0;">{escape(text)}</div>'
    )


def wordmark_html() -> str:
    return (
        f'<div style="font-size:20px;font-weight:600;letter-spacing:-0.01em;color:{TEXT};'
        f'padding:4px 0 8px 0;">Class<span style="color:{PRIMARY};">Pulse</span></div>'
    )


def states_legend_html() -> str:
    rows = "".join(
        f'<div style="display:flex;gap:10px;align-items:flex-start;margin-top:10px;">'
        f'<span style="flex:none;width:12px;height:12px;border-radius:3px;margin-top:3px;'
        f'background:{colour};"></span>'
        f'<span><span style="font-size:14px;font-weight:500;color:{TEXT};">{escape(name)}</span><br>'
        f'<span style="font-size:13px;color:{SECONDARY_TEXT};">'
        f"{escape(STATE_DESCRIPTIONS[name])}</span></span></div>"
        for name, colour in STATE_COLOURS.items()
    )
    return (
        f'<div style="border-top:1px solid {BORDER};padding-top:14px;">'
        f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:0.12em;'
        f'color:{SECONDARY_TEXT};">States</div>{rows}</div>'
    )


# ---- Streamlit renderers ----------------------------------------------------------


def stat_card(label: str, figure: str, comparison: str, accent: bool = False) -> None:
    """A card with a small label, a large tabular figure and one comparison line."""
    st.html(stat_card_html(label, figure, comparison, accent))


def page_header(eyebrow: str, title: str, context_line: str = "") -> None:
    """Eyebrow (small caps), page title and an optional context line."""
    st.html(page_header_html(eyebrow, title, context_line))


def simulated_banner(text: str) -> None:
    """A banner marking data that is simulated, not measured."""
    st.html(simulated_banner_html(text))


def wordmark() -> None:
    st.html(wordmark_html())


# Streamlit has no setting for "last item at the bottom of the sidebar", so sidebar_pin.css
# stretches the sidebar's content to full height and pushes the keyed legend container down.
LEGEND_KEY = "states_legend"  # the css file targets .st-key-states_legend
_PIN_LEGEND_CSS = Path(__file__).with_name("sidebar_pin.css")


def pin_legend_css() -> None:
    """Load the CSS that pins the legend. Call once per run, outside `with st.sidebar:`."""
    st.html(_PIN_LEGEND_CSS)  # a .css path: Streamlit wraps it in <style> and takes no space


def states_legend() -> None:
    """The four states with a swatch, name and one line each, pinned to the sidebar bottom.

    Call as the last thing inside `with st.sidebar:`.
    """
    with st.container(key=LEGEND_KEY):
        st.html(states_legend_html())
