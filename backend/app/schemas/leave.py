from pydantic import BaseModel
from datetime import date
from typing import Optional, Literal
from uuid import UUID

class LeaveRequestCreate(BaseModel):
    start_date: date
    end_date: date
    reason: str
    leave_type: Literal["full_day", "first_half", "second_half"] = "full_day"

class LeaveRequestResponse(BaseModel):
    id: UUID
    user_id: UUID
    employee_name: str
    start_date: date
    end_date: date
    leave_type: str = "full_day"
    status: str
    reason: str
    admin_notes: Optional[str] = None
    
    class Config:
        from_attributes = True

class LeaveActionRequest(BaseModel):
    action: str # "approve" or "reject"
    notes: Optional[str] = None

class CompOffBalanceResponse(BaseModel):
    days_earned: float
    days_used: float
    days_paid_out: float
    available_balance: float
