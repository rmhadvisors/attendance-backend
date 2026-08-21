from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.core.database import get_db
from app.core.security import require_admin_or_superadmin, get_current_user
from app.models.working_days import WorkingDaysConfig
from app.models.holiday import Holiday
from app.schemas.company import WorkingDaysUpdate, HolidayCreate, HolidayResponse
from app.models.user import User

router = APIRouter(
    prefix="/company",
    tags=["Company Config"]
)

@router.get("/working-days")
def get_working_days(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    config = db.query(WorkingDaysConfig).first()
    if not config:
        config = WorkingDaysConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config

@router.put("/working-days")
def update_working_days(
    data: WorkingDaysUpdate,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_or_superadmin)
):
    config = db.query(WorkingDaysConfig).first()
    if not config:
        config = WorkingDaysConfig()
        db.add(config)
    
    config.monday = data.monday
    config.tuesday = data.tuesday
    config.wednesday = data.wednesday
    config.thursday = data.thursday
    config.friday = data.friday
    config.saturday = data.saturday
    config.sunday = data.sunday
    config.updated_by = admin_user.id
    
    db.commit()
    db.refresh(config)
    return config

@router.get("/holidays", response_model=list[HolidayResponse])
def get_holidays(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return db.query(Holiday).order_by(Holiday.date.asc()).all()

@router.post("/holidays", response_model=HolidayResponse)
def create_holiday(
    data: HolidayCreate,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_or_superadmin)
):
    holiday = db.query(Holiday).filter(Holiday.date == data.date).first()
    if holiday:
        raise HTTPException(status_code=400, detail="A holiday on this date already exists.")
    
    holiday = Holiday(
        name=data.name,
        date=data.date,
        type=data.type
    )
    db.add(holiday)
    db.commit()
    db.refresh(holiday)
    return holiday

@router.delete("/holidays/{holiday_id}")
def delete_holiday(
    holiday_id: UUID,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_or_superadmin)
):
    holiday = db.query(Holiday).filter(Holiday.id == holiday_id).first()
    if not holiday:
        raise HTTPException(status_code=404, detail="Holiday not found")
    
    db.delete(holiday)
    db.commit()
    return {"message": "Holiday deleted"}
