from sqlalchemy import Column, String, Date, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime

from app.core.database import Base


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    # 'full_day' | 'first_half' | 'second_half' (halves only for a single date)
    leave_type = Column(String(20), default="full_day", nullable=False, server_default="full_day")
    reason = Column(String(280), nullable=True)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    action_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    action_at = Column(DateTime, nullable=True)
    admin_notes = Column(String(280), nullable=True)
