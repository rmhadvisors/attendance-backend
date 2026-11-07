import uuid
from sqlalchemy import Column, Integer, Float, ForeignKey, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
from app.core.database import Base

class MonthlySalary(Base):
    __tablename__ = "monthly_salaries"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    base_salary = Column(Float, nullable=False, default=0.0)
    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )
    updated_at = Column(
        DateTime,
        nullable=True,
        onupdate=datetime.utcnow
    )


class PayrollAdjustment(Base):
    """Manual, admin-only change to one employee's pay for one month.

    kind 'addition'  -> bonus, incentive, reimbursement ... (adds to pay)
    kind 'deduction' -> advance, penalty, loan recovery ... (subtracts from pay)
    """
    __tablename__ = "payroll_adjustments"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    kind = Column(String(12), nullable=False)
    amount = Column(Float, nullable=False)
    reason = Column(String(200), nullable=False)
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )
    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )
