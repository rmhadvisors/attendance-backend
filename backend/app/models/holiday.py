from sqlalchemy import Column, String, Date, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime

from app.core.database import Base


class Holiday(Base):
    __tablename__ = "holidays"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    date = Column(Date, nullable=False)
    type = Column(String(20), nullable=False)  # 'national' | 'state' | 'custom'
    is_preloaded = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
