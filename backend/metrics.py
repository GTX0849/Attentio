"""Per-window figures derived from state seconds."""


def attention(attentive: float, distracted: float) -> float | None:
    """Attentive share of the time the student was visibly engaged or not; None if no such time."""
    denominator = attentive + distracted
    return attentive / denominator if denominator > 0 else None


def presence(attentive: float, neutral: float, distracted: float, not_visible: float) -> float | None:
    """Share of the window the student was in view; None for an empty window."""
    total = attentive + neutral + distracted + not_visible
    return (attentive + neutral + distracted) / total if total > 0 else None
