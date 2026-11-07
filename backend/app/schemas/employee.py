from datetime import date
from pydantic import BaseModel, EmailStr, Field
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
    date_of_joining: Optional[date] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    reporting_manager: Optional[str] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None


class EmployeeUpdate(BaseModel):
    employee_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    saturday_policy: Optional[str] = None
    base_salary: Optional[float] = None
    allow_remote_checkin: bool = False
    preferred_checkin_time: Optional[str] = None
    enable_checkin_reminder: Optional[bool] = None
    date_of_joining: Optional[date] = None
    date_of_exit: Optional[date] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    reporting_manager: Optional[str] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    # Admin-set password (e.g. when the employee forgets theirs). Omit/blank to leave unchanged.
    new_password: Optional[str] = Field(default=None, min_length=6)


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
    date_of_joining: Optional[date] = None
    date_of_exit: Optional[date] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    reporting_manager: Optional[str] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None

class UserPreferencesUpdate(BaseModel):
    preferred_checkin_time: Optional[str] = "09:00"
    enable_checkin_reminder: Optional[bool] = True

    class Config:
        from_attributes = True

