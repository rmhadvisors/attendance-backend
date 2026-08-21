from pydantic import BaseModel
from datetime import date
from uuid import UUID

class WorkingDaysUpdate(BaseModel):
    monday: bool
    tuesday: bool
    wednesday: bool
    thursday: bool
    friday: bool
    saturday: bool
    sunday: bool

class HolidayCreate(BaseModel):
    name: str
    date: date
    type: str = "national"

class HolidayResponse(HolidayCreate):
    id: UUID
    
    class Config:
        from_attributes = True
