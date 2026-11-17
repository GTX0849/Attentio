"""The attention record: the one message shape shared by worker and backend."""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field


class AttentionState(StrEnum):
    ATTENTIVE = "attentive"
    NEUTRAL = "neutral"
    DISTRACTED = "distracted"
    NOT_VISIBLE = "not_visible"


class AttentionRecord(BaseModel):
    """Seconds spent in each state by one student in one fixed window.

    camera_id is the camera's code (for example "CAM1") and student_id is the
    student's register number (for example "STU001").
    """

    camera_id: str = Field(min_length=1)
    student_id: str = Field(min_length=1)
    window_start: AwareDatetime  # UTC, aligned to the window length
    attentive: float = Field(default=0.0, ge=0)
    neutral: float = Field(default=0.0, ge=0)
    distracted: float = Field(default=0.0, ge=0)
    not_visible: float = Field(default=0.0, ge=0)
