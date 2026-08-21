from pydantic import BaseModel, EmailStr
from uuid import UUID
from typing import Optional


class EmployeeCreate(BaseModel):
    employee_id: str
    name: str
    email: EmailStr
    password: str
    phone: str
    role: str
    saturday_policy: Optional[str] = "alt_sat_holiday"
    allow_remote_checkin: bool = False
    preferred_checkin_time: Optional[str] = "09:00"
    enable_checkin_reminder: Optional[bool] = True


class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    saturday_policy: Optional[str] = None
    base_salary: Optional[float] = None
    allow_remote_checkin: bool = False
    preferred_checkin_time: Optional[str] = None
    enable_checkin_reminder: Optional[bool] = None


class EmployeeResponse(BaseModel):
    id: UUID
    employee_id: str
    name: str
    email: EmailStr
    phone: str
    role: str
    face_enrolled: bool
    saturday_policy: str
    base_salary: Optional[float] = 0.0
    allow_remote_checkin: bool
    preferred_checkin_time: Optional[str] = "09:00"
    enable_checkin_reminder: bool = True

class UserPreferencesUpdate(BaseModel):
    preferred_checkin_time: Optional[str] = "09:00"
    enable_checkin_reminder: Optional[bool] = True

    class Config:
        from_attributes = True

