from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from datetime import datetime

from app.core.database import Base


class NotificationLog(Base):
    __tablename__ = "notifications_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    # 'checkout_reminder' | 'azure_down' | 'anomaly_flag' | 'leave_approved' | 'leave_rejected'
    type = Column(String(50), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(10), nullable=False)  # 'sent' | 'failed'
    payload = Column(JSONB, nullable=True)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    endpoint = Column(Text, nullable=False)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class FaceVerificationFailure(Base):
    __tablename__ = "face_verification_failures"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    attempt_at = Column(DateTime, default=datetime.utcnow)
    photo_url = Column(Text, nullable=True)
    similarity_score = Column(String(10), nullable=True)
    # 'checkin' | 'checkout' | 'enrollment'
    action_type = Column(String(20), nullable=False)
