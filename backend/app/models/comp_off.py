from sqlalchemy import Column, String, Date, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime

from app.core.database import Base


class CompOffBalance(Base):
    __tablename__ = "comp_off_balance"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False)
    days_earned = Column(Numeric(5, 1), default=0)
    days_used = Column(Numeric(5, 1), default=0)
    days_paid_out = Column(Numeric(5, 1), default=0)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CompOffTransaction(Base):
    __tablename__ = "comp_off_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    # 'earned' | 'used_leave' | 'paid_out'
    type = Column(String(20), nullable=False)
    amount = Column(Numeric(5, 1), nullable=False)
    reference_date = Column(Date, nullable=False)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    notes = Column(String(280), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
