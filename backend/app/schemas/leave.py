from pydantic import BaseModel
from datetime import date
from typing import Optional
from uuid import UUID

class LeaveRequestCreate(BaseModel):
    start_date: date
    end_date: date
    reason: str

class LeaveRequestResponse(BaseModel):
    id: UUID
    user_id: UUID
    employee_name: str
    start_date: date
    end_date: date
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
