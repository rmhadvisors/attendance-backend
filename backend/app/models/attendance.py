from sqlalchemy import (
    Column,
    String,
    Date,
    DateTime,
    Float,
    Boolean,
    Text,
    ForeignKey
)

from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime

from app.core.database import Base


class AttendanceLog(Base):

    __tablename__ = "attendance_logs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id")
    )

    date = Column(Date)

    # --- Check-in ---
    checkin_time = Column(DateTime, nullable=True)
    checkin_lat = Column(Float, nullable=True)
    checkin_lng = Column(Float, nullable=True)
    checkin_photo_url = Column(Text, nullable=True)
    checkin_note = Column(String(280), nullable=True)
    checkin_face_score = Column(Float, nullable=True)

    # --- Check-out ---
    checkout_time = Column(DateTime, nullable=True)
    checkout_lat = Column(Float, nullable=True)
    checkout_lng = Column(Float, nullable=True)
    checkout_photo_url = Column(Text, nullable=True)
    checkout_note = Column(String(280), nullable=True)
    checkout_face_score = Column(Float, nullable=True)

    total_hours = Column(Float, default=0)

    # checkin_status: 'early_bird' | 'on_time' | 'late'
    checkin_status = Column(String(20), nullable=True)

    # checkout_status: 'early_leave' | 'on_time_out' | 'present'
    checkout_status = Column(String(20), nullable=True)

    # day_status: 'full_day' | 'half_day' | 'absent' | 'holiday_work' | 'comp_off_leave'
    day_status = Column(String(20), nullable=True)

    # --- Admin override ---
    is_manual_override = Column(Boolean, default=False)
    override_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    override_at = Column(DateTime, nullable=True)
    override_note = Column(Text, nullable=True)

    # --- Anomaly flags ---
    is_anomaly_flagged = Column(Boolean, default=False)
    # 'drift' | 'impossible_travel'
    anomaly_type = Column(String(30), nullable=True)

    # --- Mood tracking ---
    checkin_mood = Column(String(50), nullable=True)
    checkin_mood_note = Column(Text, nullable=True)
    checkout_mood = Column(String(50), nullable=True)
    checkout_mood_note = Column(Text, nullable=True)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )


class AttendanceInterval(Base):
    __tablename__ = "attendance_intervals"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    attendance_log_id = Column(
        UUID(as_uuid=True),
        ForeignKey("attendance_logs.id", ondelete="CASCADE"),
        nullable=False
    )

    checkin_time = Column(DateTime, nullable=False)
    checkin_lat = Column(Float, nullable=True)
    checkin_lng = Column(Float, nullable=True)
    checkin_photo_url = Column(Text, nullable=True)
    checkin_note = Column(String(280), nullable=True)
    checkin_face_score = Column(Float, nullable=True)
    checkin_mood = Column(String(50), nullable=True)
    checkin_mood_note = Column(Text, nullable=True)

    checkout_time = Column(DateTime, nullable=True)
    checkout_lat = Column(Float, nullable=True)
    checkout_lng = Column(Float, nullable=True)
    checkout_photo_url = Column(Text, nullable=True)
    checkout_note = Column(String(280), nullable=True)
    checkout_face_score = Column(Float, nullable=True)
    checkout_mood = Column(String(50), nullable=True)
    checkout_mood_note = Column(Text, nullable=True)

    duration_hours = Column(Float, default=0.0)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

