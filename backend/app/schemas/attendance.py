from pydantic import BaseModel
from datetime import datetime, date
from typing import Optional


class CheckInRequest(BaseModel):
    latitude: float
    longitude: float
    note: Optional[str] = None
    photo: Optional[str] = None
    mood: Optional[str] = None
    mood_note: Optional[str] = None

class CheckOutRequest(BaseModel):
    latitude: float
    longitude: float
    note: Optional[str] = None
    photo: Optional[str] = None
    mood: Optional[str] = None
    mood_note: Optional[str] = None

class OverrideRequest(BaseModel):
    day_status: str
    admin_note: Optional[str] = None
    checkin_time: Optional[datetime] = None
    checkout_time: Optional[datetime] = None


class AttendanceResponse(BaseModel):
    date: date
    checkin_time: Optional[datetime] = None
    checkout_time: Optional[datetime] = None
    checkin_note: Optional[str] = None
    checkout_note: Optional[str] = None
    total_hours: Optional[float] = None
    checkin_status: Optional[str] = None
    checkout_status: Optional[str] = None
    day_status: Optional[str] = None
    is_manual_override: bool = False
    is_anomaly_flagged: bool = False
    checkin_mood: Optional[str] = None
    checkin_mood_note: Optional[str] = None
    checkout_mood: Optional[str] = None
    checkout_mood_note: Optional[str] = None

    class Config:
        from_attributes = True