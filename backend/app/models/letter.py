import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class GeneratedLetter(Base):
    """One row per letter PDF the admin generated (shown on the Letters > History tab)."""
    __tablename__ = "generated_letters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    letter_type = Column(String(30), nullable=False, index=True)       # experience | relieving | ...
    letter_label = Column(String(100), nullable=False)
    employee_name = Column(String(200), nullable=False)
    filename = Column(String(300), nullable=False)
    # Where the PDF was saved: 's3', 'local', or NULL when saving failed (history row only).
    storage = Column(String(10), nullable=True)
    storage_key = Column(String(500), nullable=True)
    # The form values the letter was generated from.
    form_values = Column(JSON, nullable=True)
    generated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    generated_by_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
