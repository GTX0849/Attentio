"""The score calculations, checked against numbers worked out by hand."""

from datetime import UTC, datetime, timedelta

import pytest

from backend import metrics
from backend.metrics import Seconds


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# Two 30-second windows used by several tests.
#   A: 30 s attentive                      -> attention 30 / 30 = 100%
#   B: 3 s attentive, 7 s distracted, 20 s not visible -> attention 3 / 10 = 30%
WINDOW_A = Seconds(attentive=30)
WINDOW_B = Seconds(attentive=3, distracted=7, not_visible=20)


def test_attention_comes_from_summed_seconds_not_from_averaged_percentages() -> None:
    averaged = (1.0 + 0.3) / 2  # what averaging the two window percentages would give: 65%
    total = metrics.total_seconds([WINDOW_A, WINDOW_B])
    figure = metrics.attention_figure(total, min_presence=0.5)
    assert averaged == pytest.approx(0.65)
    # (30 + 3) attentive over (30 + 3 + 7) attentive-or-distracted = 33 / 40
    assert figure.value == pytest.approx(0.825)
    assert figure.reason is None


def test_total_seconds_adds_every_state() -> None:
    total = metrics.total_seconds([Seconds(1, 2, 3, 4), Seconds(10, 20, 30, 40)])
    assert total == Seconds(11, 22, 33, 44)
    assert total.visible == 66
    assert total.total == 110
    assert metrics.total_seconds([]) == Seconds()


def test_attention_is_none_when_nothing_attentive_or_distracted_was_seen() -> None:
    assert metrics.attention(0, 0) is None
    assert metrics.attention(15, 5) == 0.75


def test_presence_is_the_visible_share_of_all_recorded_seconds() -> None:
    # (10 + 5 + 5) visible of 30 recorded
    assert metrics.presence(10, 5, 5, 10) == pytest.approx(20 / 30)
    assert metrics.presence(0, 0, 0, 0) is None
    assert metrics.period_presence(Seconds(10, 5, 5, 10)) == pytest.approx(20 / 30)
    assert metrics.period_presence(Seconds()) is None


def test_attention_is_insufficient_when_presence_is_below_the_threshold() -> None:
    # 5 attentive + 5 distracted visible out of 30 recorded: presence 10 / 30 = 33%
    seconds = Seconds(attentive=5, distracted=5, not_visible=20)
    figure = metrics.attention_figure(seconds, min_presence=0.5)
    assert figure.value is None
    assert figure.reason == metrics.LOW_PRESENCE
    assert figure.presence == pytest.approx(1 / 3)


def test_presence_exactly_at_the_threshold_passes_the_gate() -> None:
    # 15 visible of 30 recorded: 50%, attention 10 / 15
    figure = metrics.attention_figure(Seconds(attentive=10, distracted=5, not_visible=15), 0.5)
    assert figure.value == pytest.approx(10 / 15)
    assert figure.reason is None


def test_attention_figure_explains_why_there_is_no_value() -> None:
    assert metrics.attention_figure(Seconds(), 0.5).reason == metrics.NO_RECORDS
    # Visible all the time but only neutral: there is nothing to divide
    neutral_only = metrics.attention_figure(Seconds(neutral=30), 0.5)
    assert neutral_only.value is None
    assert neutral_only.reason == metrics.NO_SCORED_TIME
    assert neutral_only.presence == 1.0


def test_lecture_presence_uses_the_scheduled_length_as_denominator() -> None:
    # A 50-minute lecture is 3000 s long.
    start, end = utc("2026-03-02T04:30:00"), utc("2026-03-02T05:20:00")
    assert metrics.scheduled_seconds(start, end, now=utc("2026-03-02T09:00:00")) == 3000
    # Only 1470 s (49 windows) of records arrived: half the lecture is missing, 49%
    ratio = metrics.lecture_presence(Seconds(attentive=1470), 3000)
    assert ratio == pytest.approx(0.49)
    assert metrics.is_present(ratio, min_presence=0.5) is False
    # 1500 s is exactly half: present
    ratio = metrics.lecture_presence(Seconds(attentive=1000, neutral=500, not_visible=1000), 3000)
    assert ratio == pytest.approx(0.5)
    assert metrics.is_present(ratio, min_presence=0.5) is True


def test_lecture_presence_is_capped_at_one() -> None:
    assert metrics.lecture_presence(Seconds(attentive=3600), 3000) == 1.0


def test_lecture_presence_is_none_for_a_lecture_with_no_length() -> None:
    assert metrics.lecture_presence(Seconds(attentive=30), 0) is None
    assert metrics.is_present(None, 0.5) is False


def test_an_open_session_is_measured_up_to_now() -> None:
    start = utc("2026-03-02T04:30:00")
    now = start + timedelta(minutes=34)
    assert metrics.scheduled_seconds(start, None, now) == 34 * 60
    # A clock behind the start never gives a negative length
    assert metrics.scheduled_seconds(start, None, start - timedelta(minutes=1)) == 0


def test_elapsed_seconds_stop_at_the_end_of_a_lecture() -> None:
    start, end = utc("2026-03-02T04:30:00"), utc("2026-03-02T05:20:00")
    assert metrics.elapsed_seconds(start, end, start + timedelta(minutes=10)) == 600
    assert metrics.elapsed_seconds(start, end, end + timedelta(hours=1)) == 3000
    assert metrics.elapsed_seconds(start, None, start + timedelta(minutes=34)) == 34 * 60


def test_the_threshold_comes_from_the_scores_section_of_the_config() -> None:
    assert metrics.min_presence_ratio() == 0.5
