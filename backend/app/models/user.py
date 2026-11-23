from sqlalchemy import Column, String, Boolean, DateTime, Float, Date
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timedelta

from app.core.database import Base


class User(Base):

    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    employee_id = Column(
        String,
        unique=True,
        nullable=False
    )

    name = Column(
        String,
        nullable=False
    )

    email = Column(
        String,
        unique=True,
        nullable=False
    )

    password_hash = Column(
        String,
        nullable=False
    )

    phone = Column(String)

    role = Column(
        String,
        default="employee"
    )

    face_image_url = Column(
        String,
        nullable=True
    )

    face_enrolled = Column(
        Boolean,
        default=False
    )

    is_active = Column(
        Boolean,
        default=True
    )

    base_salary = Column(
        Float,
        default=0.0,
        nullable=True
    )

    saturday_policy = Column(
        String,
        default="alt_sat_holiday",
        nullable=False
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        nullable=True,
        onupdate=datetime.utcnow
    )

    allow_remote_checkin = Column(
        Boolean,
        default=False,
        nullable=False
    )

    preferred_checkin_time = Column(
        String,
        default="09:00",
        nullable=False
    )

    enable_checkin_reminder = Column(
        Boolean,
        default=True,
        nullable=False
    )

    date_of_joining = Column(
        Date,
        nullable=True
    )

    # Last paid working day. NULL for current employees.
    date_of_exit = Column(
        Date,
        nullable=True
    )

    department = Column(
        String,
        nullable=True
    )

    designation = Column(
        String,
        nullable=True
    )

    reporting_manager = Column(
        String,
        nullable=True
    )

    shift_start = Column(
        String,
        nullable=True
    )

    shift_end = Column(
        String,
        nullable=True
    )

    # --- Payslip details (admin-entered) ---
    date_of_birth = Column(Date, nullable=True)
    personal_email = Column(String, nullable=True)
    blood_group = Column(String(10), nullable=True)
    emergency_contact_name = Column(String(100), nullable=True)
    emergency_contact_number = Column(String(30), nullable=True)
    pan_number = Column(String(20), nullable=True)
    bank_name = Column(String(100), nullable=True)
    bank_account_no = Column(String(40), nullable=True)
    pf_uan_no = Column(String(60), nullable=True)
    esic_no = Column(String(40), nullable=True)
    work_location = Column(String(100), nullable=True)
    # 'full_time' or 'part_time' (label only; payroll rules are the same)
    employment_type = Column(String(20), nullable=False, default="full_time", server_default="full_time")

    @property
    def joining_date(self):
        """Joining date as YYYY-MM-DD: the admin-set date_of_joining if present,
        otherwise derived from account creation time (IST)."""
        if self.date_of_joining:
            return self.date_of_joining.isoformat()
        if not self.created_at:
            return None
        return (self.created_at + timedelta(hours=5, minutes=30)).date().isoformat()