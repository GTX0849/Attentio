"""Database tables. Later tickets add lectures, teachers, timetable and so on."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(primary_key=True)  # e.g. "R101"
    cameras: Mapped[list["Camera"]] = relationship(back_populates="room")


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(primary_key=True)  # e.g. "CAM1"
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"))
    room: Mapped[Room] = relationship(back_populates="cameras")


class Student(Base):
    __tablename__ = "students"

    register_no: Mapped[str] = mapped_column(primary_key=True)  # e.g. "STU001"
    name: Mapped[str | None] = mapped_column(default=None)


class AttentionRecordRow(Base):
    __tablename__ = "attention_records"
    __table_args__ = (UniqueConstraint("student_id", "window_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.register_no"))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attentive: Mapped[float] = mapped_column(Float, default=0.0)
    neutral: Mapped[float] = mapped_column(Float, default=0.0)
    distracted: Mapped[float] = mapped_column(Float, default=0.0)
    not_visible: Mapped[float] = mapped_column(Float, default=0.0)
