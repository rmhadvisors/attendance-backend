from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import extract
from datetime import date, datetime
import calendar
from typing import Optional
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import get_current_user, require_admin_or_superadmin
from app.models.user import User
from app.models.attendance import AttendanceLog
from app.models.holiday import Holiday
from app.models.working_days import WorkingDaysConfig
from app.models.payroll import MonthlySalary

router = APIRouter(
    prefix="/payroll",
    tags=["Payroll"]
)

class SalaryUpdateSchema(BaseModel):
    user_id: str
    base_salary: float
    year: Optional[int] = None
    month: Optional[int] = None

def get_saturday_index(d: date) -> int:
    return (d.day - 1) // 7 + 1

def is_user_expected_working_day(d: date, user_saturday_policy: str, holiday_dates: set, days_map: list) -> bool:
    if d.weekday() == 6:  # Sunday
        return False
    if d in holiday_dates:  # Public holiday
        return False
    if d.weekday() == 5:  # Saturday
        sat_idx = get_saturday_index(d)
        if user_saturday_policy == "all_sat_holiday":
            return False
        elif user_saturday_policy == "all_sat_working":
            return True
        elif user_saturday_policy == "all_sat_half_day":
            return True
        elif user_saturday_policy == "all_sat_wfh":
            return True
        elif user_saturday_policy in ["alt_sat_holiday", "alt_sat_holiday_rest_wfh"]:
            if sat_idx in [2, 4]:
                return False
            else:
                return True
        return False
    return days_map[d.weekday()]

def get_employee_monthly_salary(db: Session, user_id: str, year: int, month: int, default_salary: float) -> float:
    # 1. Exact match
    exact = db.query(MonthlySalary).filter(
        MonthlySalary.user_id == user_id,
        MonthlySalary.year == year,
        MonthlySalary.month == month
    ).first()
    if exact:
        return exact.base_salary

    # 2. Most recent match on or before (year, month)
    recent = db.query(MonthlySalary).filter(
        MonthlySalary.user_id == user_id,
        (MonthlySalary.year < year) | ((MonthlySalary.year == year) & (MonthlySalary.month <= month))
    ).order_by(
        MonthlySalary.year.desc(),
        MonthlySalary.month.desc()
    ).first()
    if recent:
        return recent.base_salary

    # 3. Earliest match overall (if any exists)
    earliest = db.query(MonthlySalary).filter(
        MonthlySalary.user_id == user_id
    ).order_by(
        MonthlySalary.year.asc(),
        MonthlySalary.month.asc()
    ).first()
    if earliest:
        return earliest.base_salary

    # 4. Fallback to default user base salary
    return default_salary or 0.0

@router.get("/summary")
def get_payroll_summary(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    today = datetime.now()
    q_year = year or today.year
    q_month = month or today.month

    # Get working days config
    working_days_cfg = db.query(WorkingDaysConfig).first()
    days_map = [True, True, True, True, True, True, False] # default: Mon-Sat working, Sun holiday
    if working_days_cfg:
        days_map = [
            working_days_cfg.monday, working_days_cfg.tuesday, working_days_cfg.wednesday,
            working_days_cfg.thursday, working_days_cfg.friday, working_days_cfg.saturday,
            working_days_cfg.sunday
        ]

    # Count dynamic working days in this month
    num_days = calendar.monthrange(q_year, q_month)[1]

    # Get holidays in this month
    month_holidays = db.query(Holiday).filter(
        Holiday.date >= date(q_year, q_month, 1),
        Holiday.date <= date(q_year, q_month, num_days)
    ).all()
    holiday_dates = {h.date for h in month_holidays}

    from app.utils.timezone import now_ist
    ist_now = now_ist()
    current_date_ist = ist_now.date()

    # Fetch all employees (everyone except the core system administrator)
    employees = db.query(User).filter(User.email != "admin@glrattendance.com").order_by(User.name).all()

    payroll_records = []
    for emp in employees:
        # Fetch logs for this user in this month
        logs = db.query(AttendanceLog).filter(
            AttendanceLog.user_id == emp.id,
            AttendanceLog.date >= date(q_year, q_month, 1),
            AttendanceLog.date <= date(q_year, q_month, num_days)
        ).all()

        log_by_date = {log.date: log for log in logs}

        total_deductions = 0.0
        worked_days = 0.0
        paid_leaves = 0.0
        extra_days_worked = 0.0
        overtime_hours = 0.0

        user_policy = emp.saturday_policy or "alt_sat_holiday"

        for day_num in range(1, num_days + 1):
            d = date(q_year, q_month, day_num)
            
            # Is it an expected working day?
            is_expected_working_day = is_user_expected_working_day(
                d,
                user_policy,
                holiday_dates,
                days_map
            )

            log = log_by_date.get(d)
            if log:
                # Fallback: if employee checked in on a past day but forgot to check out, treat as half_day (5h)
                log_day_status = log.day_status
                log_total_hours = log.total_hours
                if d < current_date_ist and log.checkin_time is not None and log.checkout_time is None:
                    log_day_status = "half_day"
                    if log_total_hours is None or log_total_hours == 0.0:
                        log_total_hours = 5.0

                if is_expected_working_day:
                    if log_day_status in ["full_day", "holiday_work"]:
                        worked_days += 1.0
                    elif log_day_status == "half_day":
                        worked_days += 0.5
                        total_deductions += 0.5
                    elif log_day_status == "comp_off_leave":
                        paid_leaves += 1.0
                    elif log_day_status == "absent":
                        total_deductions += 1.0

                    # Overtime hours calculation on expected working days
                    if log_total_hours is not None:
                        target = 7.0 if (d.weekday() == 5 and user_policy == "all_sat_half_day") else 9.0
                        overtime_hours += max(0.0, log_total_hours - target)
                else:
                    # Holiday or Weekend
                    if log_day_status in ["full_day", "holiday_work"]:
                        worked_days += 1.0
                        extra_days_worked += 1.0
                    elif log_day_status == "half_day":
                        worked_days += 0.5
                        extra_days_worked += 0.5
                    elif log_day_status == "comp_off_leave":
                        paid_leaves += 1.0
            else:
                # No log on an expected working day is considered absent (only for past or current days)
                if is_expected_working_day and d <= current_date_ist:
                    total_deductions += 1.0

        # Fixed 30 days billing: paid days is 30 - deductions + extra days worked + overtime days (9 hours = 1 day)
        overtime_days = overtime_hours / 9.0
        total_paid_days = max(0.0, 30.0 - total_deductions + extra_days_worked + overtime_days)
        base_salary = get_employee_monthly_salary(db, emp.id, q_year, q_month, emp.base_salary)

        calculated_salary = 0.0
        if base_salary > 0:
            calculated_salary = ((base_salary / 30.0) * total_paid_days) * 0.99 # 1% TDS deduction

        total_hours_worked = sum(
            5.0 if (log.date < current_date_ist and log.checkin_time is not None and log.checkout_time is None) else (log.total_hours or 0.0)
            for log in logs
        )
        total_days_present = sum(1 for log in logs if log.checkin_time is not None and log.day_status != "absent")

        payroll_records.append({
            "user_id": str(emp.id),
            "employee_id": emp.employee_id,
            "name": emp.name,
            "email": emp.email,
            "base_salary": base_salary,
            "total_working_days": 30, # fixed 30 days
            "worked_days": worked_days,
            "total_days_present": total_days_present,
            "total_hours_worked": round(total_hours_worked, 2),
            "paid_leaves": paid_leaves,
            "extra_days_worked": extra_days_worked,
            "total_paid_days": total_paid_days,
            "calculated_salary": round(calculated_salary, 2),
            "saturday_policy": user_policy
        })

    return {
        "year": q_year,
        "month": q_month,
        "total_working_days": 30, # fixed 30 days
        "records": payroll_records
    }

@router.post("/salary")
def update_employee_salary(
    payload: SalaryUpdateSchema,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    emp = db.query(User).filter(User.id == payload.user_id).first()
    if not emp:
        # Fallback to employee_id
        emp = db.query(User).filter(User.employee_id == payload.user_id).first()

    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    from app.utils.timezone import now_ist
    ist_now = now_ist()
    q_year = payload.year or ist_now.year
    q_month = payload.month or ist_now.month

    # To prevent affecting past months, if we are changing the salary and the user
    # does not have any prior MonthlySalary records, we should lock in their
    # previous salary for the month before this change takes effect.
    if emp.base_salary and emp.base_salary != payload.base_salary:
        has_prior = db.query(MonthlySalary).filter(MonthlySalary.user_id == emp.id).first()
        if not has_prior:
            prev_year = q_year
            prev_month = q_month - 1
            if prev_month == 0:
                prev_month = 12
                prev_year -= 1
            
            baseline = MonthlySalary(
                user_id=emp.id,
                year=prev_year,
                month=prev_month,
                base_salary=emp.base_salary
            )
            db.add(baseline)

    # Check if a monthly salary record already exists
    record = db.query(MonthlySalary).filter(
        MonthlySalary.user_id == emp.id,
        MonthlySalary.year == q_year,
        MonthlySalary.month == q_month
    ).first()

    if record:
        record.base_salary = payload.base_salary
    else:
        record = MonthlySalary(
            user_id=emp.id,
            year=q_year,
            month=q_month,
            base_salary=payload.base_salary
        )
        db.add(record)

    # Also update the user's default base_salary
    emp.base_salary = payload.base_salary
    db.commit()

    return {"message": "Salary updated successfully", "base_salary": payload.base_salary}


@router.get("/my-slip")
def get_my_pay_slip(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    today = datetime.now()
    q_year = year or today.year
    q_month = month or today.month

    # Get working days config
    working_days_cfg = db.query(WorkingDaysConfig).first()
    days_map = [True, True, True, True, True, True, False] # default: Mon-Sat working, Sun holiday
    if working_days_cfg:
        days_map = [
            working_days_cfg.monday, working_days_cfg.tuesday, working_days_cfg.wednesday,
            working_days_cfg.thursday, working_days_cfg.friday, working_days_cfg.saturday,
            working_days_cfg.sunday
        ]

    # Count dynamic working days in this month
    num_days = calendar.monthrange(q_year, q_month)[1]

    # Get holidays in this month
    month_holidays = db.query(Holiday).filter(
        Holiday.date >= date(q_year, q_month, 1),
        Holiday.date <= date(q_year, q_month, num_days)
    ).all()
    holiday_dates = {h.date for h in month_holidays}

    from app.utils.timezone import now_ist
    ist_now = now_ist()
    current_date_ist = ist_now.date()

    # Fetch logs for this user in this month
    logs = db.query(AttendanceLog).filter(
        AttendanceLog.user_id == current_user.id,
        AttendanceLog.date >= date(q_year, q_month, 1),
        AttendanceLog.date <= date(q_year, q_month, num_days)
    ).all()

    log_by_date = {log.date: log for log in logs}

    total_deductions = 0.0
    worked_days = 0.0
    paid_leaves = 0.0
    extra_days_worked = 0.0
    overtime_hours = 0.0

    user_policy = current_user.saturday_policy or "alt_sat_holiday"

    for day_num in range(1, num_days + 1):
        d = date(q_year, q_month, day_num)
        
        # Is it an expected working day?
        is_expected_working_day = is_user_expected_working_day(
            d,
            user_policy,
            holiday_dates,
            days_map
        )

        log = log_by_date.get(d)
        if log:
            # Fallback: if employee checked in on a past day but forgot to check out, treat as half_day (5h)
            log_day_status = log.day_status
            log_total_hours = log.total_hours
            if d < current_date_ist and log.checkin_time is not None and log.checkout_time is None:
                log_day_status = "half_day"
                if log_total_hours is None or log_total_hours == 0.0:
                    log_total_hours = 5.0

            if is_expected_working_day:
                if log_day_status in ["full_day", "holiday_work"]:
                    worked_days += 1.0
                elif log_day_status == "half_day":
                    worked_days += 0.5
                    total_deductions += 0.5
                elif log_day_status == "comp_off_leave":
                    paid_leaves += 1.0
                elif log_day_status == "absent":
                    total_deductions += 1.0

                # Overtime hours calculation on expected working days
                if log_total_hours is not None:
                    target = 7.0 if (d.weekday() == 5 and user_policy == "all_sat_half_day") else 9.0
                    overtime_hours += max(0.0, log_total_hours - target)
            else:
                # Holiday or Weekend
                if log_day_status in ["full_day", "holiday_work"]:
                    worked_days += 1.0
                    extra_days_worked += 1.0
                elif log_day_status == "half_day":
                    worked_days += 0.5
                    extra_days_worked += 0.5
                elif log_day_status == "comp_off_leave":
                    paid_leaves += 1.0
        else:
            # No log on an expected working day is considered absent (only for past or current days)
            if is_expected_working_day and d <= current_date_ist:
                total_deductions += 1.0

    # Fixed 30 days billing: paid days is 30 - deductions + extra days worked + overtime days (9 hours = 1 day)
    overtime_days = overtime_hours / 9.0
    total_paid_days = max(0.0, 30.0 - total_deductions + extra_days_worked + overtime_days)
    base_salary = get_employee_monthly_salary(db, current_user.id, q_year, q_month, current_user.base_salary)

    calculated_salary = 0.0
    if base_salary > 0:
        calculated_salary = ((base_salary / 30.0) * total_paid_days) * 0.99 # 1% TDS deduction

    total_hours_worked = sum(
        5.0 if (log.date < current_date_ist and log.checkin_time is not None and log.checkout_time is None) else (log.total_hours or 0.0)
        for log in logs
    )
    total_days_present = sum(1 for log in logs if log.checkin_time is not None and log.day_status != "absent")

    return {
        "year": q_year,
        "month": q_month,
        "base_salary": base_salary,
        "total_working_days": 30, # fixed 30 days
        "worked_days": worked_days,
        "total_days_present": total_days_present,
        "total_hours_worked": round(total_hours_worked, 2),
        "paid_leaves": paid_leaves,
        "extra_days_worked": extra_days_worked,
        "total_paid_days": total_paid_days,
        "calculated_salary": round(calculated_salary, 2),
        "saturday_policy": user_policy
    }
