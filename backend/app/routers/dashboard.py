from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import extract
from datetime import date

from app.utils.timezone import today_ist

from app.core.database import get_db
from app.core.security import get_current_user, require_admin_or_superadmin

from app.models.user import User
from app.models.attendance import AttendanceLog

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"]
)


@router.get("/admin-stats")
def admin_dashboard_stats(
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):

    today = today_ist()

    total_employees = db.query(User).filter(
        User.role == "employee"
    ).count()

    today_present = db.query(AttendanceLog).filter(
        AttendanceLog.date == today
    ).count()

    today_absent = total_employees - today_present

    full_day_count = db.query(AttendanceLog).filter(
        AttendanceLog.date == today,
        AttendanceLog.day_status == "full_day"
    ).count()

    half_day_count = db.query(AttendanceLog).filter(
        AttendanceLog.date == today,
        AttendanceLog.day_status == "half_day"
    ).count()

    return {
        "date": today,
        "total_employees": total_employees,
        "today_present": today_present,
        "today_absent": today_absent,
        "full_day_count": full_day_count,
        "half_day_count": half_day_count
    }


import calendar
from app.models.holiday import Holiday
from app.models.working_days import WorkingDaysConfig
from app.utils.timezone import now_ist

def calculate_dashboard_monthly_worked_days(db: Session, user_id: str, year: int, month: int, days_map: list) -> float:
    from app.models.attendance import AttendanceLog
    from app.models.holiday import Holiday
    from app.utils.timezone import now_ist
    import calendar
    from datetime import date

    num_days = calendar.monthrange(year, month)[1]

    # Get holidays in this month
    month_holidays = db.query(Holiday).filter(
        Holiday.date >= date(year, month, 1),
        Holiday.date <= date(year, month, num_days)
    ).all()
    holiday_dates = {h.date for h in month_holidays}

    current_date_ist = now_ist().date()

    # Fetch logs for this user in this month
    logs = db.query(AttendanceLog).filter(
        AttendanceLog.user_id == user_id,
        AttendanceLog.date >= date(year, month, 1),
        AttendanceLog.date <= date(year, month, num_days)
    ).all()

    log_by_date = {log.date: log for log in logs}

    # If it is a future month, return 0.0
    if year > current_date_ist.year or (year == current_date_ist.year and month > current_date_ist.month):
        return 0.0

    # If it is a past month, use the payroll billing days logic (30.0 - deductions)
    if year < current_date_ist.year or (year == current_date_ist.year and month < current_date_ist.month):
        total_deductions = 0.0
        for day_num in range(1, num_days + 1):
            d = date(year, month, day_num)
            is_sun = d.weekday() == 6
            is_hol = d in holiday_dates
            is_work_configured = days_map[d.weekday()]
            is_expected_working_day = is_work_configured and not is_sun and not is_hol

            log = log_by_date.get(d)
            if log:
                if log.day_status == "half_day":
                    if is_expected_working_day:
                        total_deductions += 0.5
                elif log.day_status == "absent":
                    if is_expected_working_day:
                        total_deductions += 1.0
            else:
                if is_expected_working_day:
                    total_deductions += 1.0
        return max(0.0, 30.0 - total_deductions)

    # If it is the current month, calculate accrued days to date
    accrued_days = 0.0
    for day_num in range(1, num_days + 1):
        d = date(year, month, day_num)
        if d > current_date_ist:
            continue

        is_sun = d.weekday() == 6
        is_hol = d in holiday_dates
        is_work_configured = days_map[d.weekday()]
        is_expected_working_day = is_work_configured and not is_sun and not is_hol

        log = log_by_date.get(d)
        if log:
            if log.day_status in ["full_day", "holiday_work", "comp_off_leave", "present"]:
                accrued_days += 1.0
            elif log.day_status == "half_day":
                accrued_days += 0.5
        else:
            # Weekend or Holiday or Configured non-working day is paid and counts as working
            if is_sun or is_hol or not is_work_configured:
                accrued_days += 1.0

    return min(30.0, accrued_days)

@router.get("/employee-stats")
def get_employee_stats(
    user_id: str = None,
    year: int = None,
    month: int = None,
    query_date: date = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 1. Authorize
    target_user_id = current_user.id
    if user_id:
        if current_user.role not in ["admin", "superadmin"] and user_id != str(current_user.id):
            raise HTTPException(status_code=403, detail="Not authorized to view other employee stats")
        target_user_id = user_id
        
    # Get user object
    user = db.query(User).filter(User.id == target_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Employee not found")

    # If no year or month, use current date's
    today = today_ist()
    q_year = year or today.year
    q_month = month or today.month

    # Get working days configuration
    working_days_cfg = db.query(WorkingDaysConfig).first()
    days_map = [True, True, True, True, True, True, False] # default: Mon-Sat working, Sun holiday
    if working_days_cfg:
        days_map = [
            working_days_cfg.monday, working_days_cfg.tuesday, working_days_cfg.wednesday,
            working_days_cfg.thursday, working_days_cfg.friday, working_days_cfg.saturday,
            working_days_cfg.sunday
        ]

    # 2. Yearly calculations
    yearly_logs = db.query(AttendanceLog).filter(
        AttendanceLog.user_id == target_user_id,
        AttendanceLog.date >= date(q_year, 1, 1),
        AttendanceLog.date <= date(q_year, 12, 31)
    ).all()

    yearly_hours = sum(log.total_hours or 0.0 for log in yearly_logs)
    
    # Calculate average hours per day (Option 1: scale Saturdays for half-day policy)
    yearly_active_logs = [log for log in yearly_logs if log.total_hours and log.total_hours > 0.0]
    yearly_active_days_count = len(yearly_active_logs)
    yearly_scaled_hours = 0.0
    for log in yearly_logs:
        log_hours = log.total_hours or 0.0
        if log.date.weekday() == 5 and user.saturday_policy == "all_sat_half_day":
            log_hours = log_hours * (9.0 / 6.5)
        yearly_scaled_hours += log_hours
    yearly_avg_hours = (yearly_scaled_hours / yearly_active_days_count) if yearly_active_days_count > 0 else 0.0

    # Calculate yearly worked days based on 30-day billing logic across all 12 months
    yearly_worked_days = sum(
        calculate_dashboard_monthly_worked_days(db, target_user_id, q_year, m, days_map)
        for m in range(1, 13)
    )
    yearly_worked_days = round(yearly_worked_days, 1)
    total_yearly_working_days = 360  # 12 months * 30 days

    # 3. Monthly calculations
    num_days = calendar.monthrange(q_year, q_month)[1]
    monthly_logs = db.query(AttendanceLog).filter(
        AttendanceLog.user_id == target_user_id,
        AttendanceLog.date >= date(q_year, q_month, 1),
        AttendanceLog.date <= date(q_year, q_month, num_days)
    ).all()

    monthly_hours = sum(log.total_hours or 0.0 for log in monthly_logs)
    
    # Calculate average hours per day (Option 1: scale Saturdays for half-day policy)
    monthly_active_logs = [log for log in monthly_logs if log.total_hours and log.total_hours > 0.0]
    monthly_active_days_count = len(monthly_active_logs)
    monthly_scaled_hours = 0.0
    for log in monthly_logs:
        log_hours = log.total_hours or 0.0
        if log.date.weekday() == 5 and user.saturday_policy == "all_sat_half_day":
            log_hours = log_hours * (9.0 / 6.5)
        monthly_scaled_hours += log_hours
    monthly_avg_hours = (monthly_scaled_hours / monthly_active_days_count) if monthly_active_days_count > 0 else 0.0

    # Calculate monthly worked days based on 30-day fixed billing logic
    monthly_worked_days = calculate_dashboard_monthly_worked_days(db, target_user_id, q_year, q_month, days_map)
    total_working_days = 30  # fixed 30 days

    # Breakdown of statuses in month
    breakdown = {
        "full_day": len([log for log in monthly_logs if log.day_status == "full_day"]),
        "half_day": len([log for log in monthly_logs if log.day_status == "half_day"]),
        "holiday_work": len([log for log in monthly_logs if log.day_status == "holiday_work"]),
        "comp_off_leave": len([log for log in monthly_logs if log.day_status == "comp_off_leave"]),
        "present": len([log for log in monthly_logs if log.day_status == "present"])
    }

    # 4. Single day detail (if queried)
    single_day_log = None
    if query_date:
        log = db.query(AttendanceLog).filter(
            AttendanceLog.user_id == target_user_id,
            AttendanceLog.date == query_date
        ).first()
        
        if log:
            single_day_log = {
                "id": str(log.id),
                "date": log.date,
                "checkin_time": log.checkin_time,
                "checkout_time": log.checkout_time,
                "total_hours": log.total_hours,
                "checkin_status": log.checkin_status,
                "checkout_status": log.checkout_status,
                "day_status": log.day_status,
                "checkin_note": log.checkin_note,
                "checkout_note": log.checkout_note,
                "checkin_photo_url": log.checkin_photo_url,
                "checkout_photo_url": log.checkout_photo_url
            }
        else:
            # Check if this day is a holiday
            is_sunday = query_date.weekday() == 6
            day_holiday = db.query(Holiday).filter(Holiday.date == query_date).first()
            
            is_work_day = True
            if working_days_cfg:
                day_of_week = query_date.weekday()
                days_map_cfg = [
                    working_days_cfg.monday, working_days_cfg.tuesday, working_days_cfg.wednesday,
                    working_days_cfg.thursday, working_days_cfg.friday, working_days_cfg.saturday,
                    working_days_cfg.sunday
                ]
                is_work_day = days_map_cfg[day_of_week]

            if day_holiday or is_sunday or not is_work_day:
                holiday_name = day_holiday.name if day_holiday else ("Sunday Holiday" if is_sunday else "Non-working Day")
                single_day_log = {
                    "id": "holiday",
                    "date": query_date,
                    "checkin_time": None,
                    "checkout_time": None,
                    "total_hours": 0.0,
                    "checkin_status": None,
                    "checkout_status": None,
                    "day_status": "holiday",
                    "checkin_note": holiday_name,
                    "checkout_note": None,
                    "checkin_photo_url": None,
                    "checkout_photo_url": None
                }

    calendar_records = [
        {
            "date": str(log.date),
            "day_status": log.day_status,
            "total_hours": log.total_hours
        }
        for log in monthly_logs
    ]

    return {
        "employee_name": user.name,
        "employee_id": user.employee_id,
        "query_year": q_year,
        "query_month": q_month,
        "saturday_policy": user.saturday_policy,
        "records": calendar_records,
        "yearly_stats": {
            "total_hours": round(yearly_hours, 2),
            "worked_days": yearly_worked_days,
            "total_working_days": total_yearly_working_days,
            "avg_hours": round(yearly_avg_hours, 2)
        },
        "monthly_stats": {
            "total_hours": round(monthly_hours, 2),
            "worked_days": monthly_worked_days,
            "total_working_days": total_working_days,
            "breakdown": breakdown,
            "avg_hours": round(monthly_avg_hours, 2)
        },
        "single_day_detail": single_day_log
    }