from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import extract
from datetime import date, datetime
import calendar
import math
import re
import json
from typing import Optional
from uuid import UUID
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import get_current_user, require_admin_or_superadmin
from app.models.user import User
from app.models.attendance import AttendanceLog
from app.models.holiday import Holiday
from app.models.working_days import WorkingDaysConfig
from app.models.payroll import MonthlySalary, PayrollAdjustment, PayslipEntry

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

def get_joining_adjustment(user, year: int, month: int, num_days: int):
    """Only days between joining and exit (inclusive) are payable.

    Returns (join_date, exit_date, billing_days):
      - join_date / exit_date are None when they do not limit this month
      - billing_days is the fixed-30-day base: 30 for a full month, otherwise the number of
        employed days in the month (capped at 30), and 0 if not employed at all that month
    """
    first = date(year, month, 1)
    last = date(year, month, num_days)

    joining = None
    raw = getattr(user, "joining_date", None)
    if raw:
        try:
            joining = date.fromisoformat(raw)
        except ValueError:
            joining = None
    exit_date = getattr(user, "date_of_exit", None)

    if (joining and joining > last) or (exit_date and exit_date < first):
        return joining, exit_date, 0.0

    start = joining if (joining and joining > first) else first
    end = exit_date if (exit_date and exit_date < last) else last
    if start == first and end == last:
        return None, None, 30.0

    billing_days = float(min(30, (end - start).days + 1))
    return (joining if start != first else None), (exit_date if end != last else None), billing_days


ADJUSTMENT_KINDS = ("addition", "deduction")
MAX_ADJUSTMENT_AMOUNT = 10_000_000


class AdjustmentCreate(BaseModel):
    user_id: UUID
    year: int
    month: int
    kind: str
    amount: float
    reason: str


def get_adjustment_totals(db: Session, user_id, year: int, month: int):
    """(total additions, total deductions) of manual adjustments for one employee-month."""
    rows = db.query(PayrollAdjustment).filter(
        PayrollAdjustment.user_id == user_id,
        PayrollAdjustment.year == year,
        PayrollAdjustment.month == month
    ).all()
    added = sum(r.amount for r in rows if r.kind == "addition")
    deducted = sum(r.amount for r in rows if r.kind == "deduction")
    return round(added, 2), round(deducted, 2)


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

    month_adjustments = db.query(PayrollAdjustment).filter(
        PayrollAdjustment.year == q_year,
        PayrollAdjustment.month == q_month
    ).all()
    adjustment_totals = {}
    for adj in month_adjustments:
        added, deducted = adjustment_totals.get(adj.user_id, (0.0, 0.0))
        if adj.kind == "addition":
            added += adj.amount
        else:
            deducted += adj.amount
        adjustment_totals[adj.user_id] = (added, deducted)

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
        join_date, exit_date, billing_days = get_joining_adjustment(emp, q_year, q_month, num_days)

        for day_num in range(1, num_days + 1):
            d = date(q_year, q_month, day_num)
            if (join_date and d < join_date) or (exit_date and d > exit_date):
                continue
            
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

        # Fixed 30 days billing: paid days is billing days (30, fewer for a mid-month joiner) - deductions + extra days worked + overtime days (9 hours = 1 day)
        overtime_days = overtime_hours / 9.0
        # No work and no paid leave in the period => nothing is payable (week offs/holidays
        # are not paid for an employee who never worked)
        has_paid_attendance = worked_days > 0 or paid_leaves > 0 or any(l.day_status == "present" for l in logs)
        total_paid_days = (
            max(0.0, billing_days - total_deductions + extra_days_worked + overtime_days)
            if has_paid_attendance else 0.0
        )
        base_salary = get_employee_monthly_salary(db, emp.id, q_year, q_month, emp.base_salary)

        calculated_salary = 0.0
        if base_salary > 0:
            calculated_salary = (base_salary / 30.0) * total_paid_days
        adj_added, adj_deducted = adjustment_totals.get(emp.id, (0.0, 0.0))
        net_salary = calculated_salary + adj_added - adj_deducted

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
            "adjustments_added": round(adj_added, 2),
            "adjustments_deducted": round(adj_deducted, 2),
            "net_salary": round(net_salary, 2),
            "saturday_policy": user_policy
        })

    return {
        "year": q_year,
        "month": q_month,
        "total_working_days": 30, # fixed 30 days
        "records": payroll_records
    }

def apply_employee_salary(db: Session, emp: User, q_year: int, q_month: int, new_salary: float):
    """Set an employee's monthly salary from (q_year, q_month) onwards. Does not commit.

    Payroll reads MonthlySalary rows first and the user's base_salary only as a fallback, so
    every salary change must go through here or it can be silently ignored.
    """
    # To prevent affecting past months, if we are changing the salary and the user
    # does not have any prior MonthlySalary records, lock in their previous salary for the
    # month before this change takes effect.
    if emp.base_salary and emp.base_salary != new_salary:
        has_prior = db.query(MonthlySalary).filter(MonthlySalary.user_id == emp.id).first()
        if not has_prior:
            prev_year = q_year
            prev_month = q_month - 1
            if prev_month == 0:
                prev_month = 12
                prev_year -= 1
            db.add(MonthlySalary(
                user_id=emp.id,
                year=prev_year,
                month=prev_month,
                base_salary=emp.base_salary
            ))

    record = db.query(MonthlySalary).filter(
        MonthlySalary.user_id == emp.id,
        MonthlySalary.year == q_year,
        MonthlySalary.month == q_month
    ).first()
    if record:
        record.base_salary = new_salary
    else:
        db.add(MonthlySalary(
            user_id=emp.id,
            year=q_year,
            month=q_month,
            base_salary=new_salary
        ))
    emp.base_salary = new_salary


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

    apply_employee_salary(db, emp, q_year, q_month, payload.base_salary)
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
    join_date, exit_date, billing_days = get_joining_adjustment(current_user, q_year, q_month, num_days)

    for day_num in range(1, num_days + 1):
        d = date(q_year, q_month, day_num)
        if (join_date and d < join_date) or (exit_date and d > exit_date):
            continue
        
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

    # Fixed 30 days billing: paid days is billing days (30, fewer for a mid-month joiner) - deductions + extra days worked + overtime days (9 hours = 1 day)
    overtime_days = overtime_hours / 9.0
    # No work and no paid leave in the period => nothing is payable
    has_paid_attendance = worked_days > 0 or paid_leaves > 0 or any(l.day_status == "present" for l in logs)
    total_paid_days = (
        max(0.0, billing_days - total_deductions + extra_days_worked + overtime_days)
        if has_paid_attendance else 0.0
    )
    base_salary = get_employee_monthly_salary(db, current_user.id, q_year, q_month, current_user.base_salary)

    calculated_salary = 0.0
    if base_salary > 0:
        calculated_salary = (base_salary / 30.0) * total_paid_days
    my_adjustments = db.query(PayrollAdjustment).filter(
        PayrollAdjustment.user_id == current_user.id,
        PayrollAdjustment.year == q_year,
        PayrollAdjustment.month == q_month
    ).order_by(PayrollAdjustment.created_at).all()
    adj_added = round(sum(a.amount for a in my_adjustments if a.kind == "addition"), 2)
    adj_deducted = round(sum(a.amount for a in my_adjustments if a.kind == "deduction"), 2)
    net_salary = calculated_salary + adj_added - adj_deducted

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
        "adjustments_added": adj_added,
        "adjustments_deducted": adj_deducted,
        "net_salary": round(net_salary, 2),
        "adjustments": [
            {"kind": a.kind, "amount": a.amount, "reason": a.reason}
            for a in my_adjustments
        ],
        "saturday_policy": user_policy
    }


def _adjustment_dict(a: PayrollAdjustment):
    return {
        "id": str(a.id),
        "kind": a.kind,
        "amount": a.amount,
        "reason": a.reason,
        "created_at": a.created_at
    }


@router.get("/adjustments")
def list_adjustments(
    user_id: UUID,
    year: int,
    month: int,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    rows = db.query(PayrollAdjustment).filter(
        PayrollAdjustment.user_id == user_id,
        PayrollAdjustment.year == year,
        PayrollAdjustment.month == month
    ).order_by(PayrollAdjustment.created_at).all()
    return [_adjustment_dict(a) for a in rows]


@router.post("/adjustments")
def add_adjustment(
    payload: AdjustmentCreate,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    if payload.kind not in ADJUSTMENT_KINDS:
        raise HTTPException(status_code=400, detail="Type must be 'addition' or 'deduction'")
    if not (1 <= payload.month <= 12) or not (2000 <= payload.year <= 2100):
        raise HTTPException(status_code=400, detail="Invalid month or year")
    if not math.isfinite(payload.amount) or payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than 0")
    if payload.amount > MAX_ADJUSTMENT_AMOUNT:
        raise HTTPException(status_code=400, detail="Amount is too large")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="A reason is required")
    if len(reason) > 200:
        raise HTTPException(status_code=400, detail="Reason must be 200 characters or fewer")

    employee = db.query(User).filter(User.id == payload.user_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    adjustment = PayrollAdjustment(
        user_id=employee.id,
        year=payload.year,
        month=payload.month,
        kind=payload.kind,
        amount=round(payload.amount, 2),
        reason=reason,
        created_by=current_user.id
    )
    db.add(adjustment)
    db.commit()
    db.refresh(adjustment)
    return _adjustment_dict(adjustment)


@router.delete("/adjustments/{adjustment_id}")
def delete_adjustment(
    adjustment_id: UUID,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    adjustment = db.query(PayrollAdjustment).filter(PayrollAdjustment.id == adjustment_id).first()
    if not adjustment:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    db.delete(adjustment)
    db.commit()
    return {"message": "Adjustment removed"}



# ── Payslips (admin only) ────────────────────────────────────────────────────

class EarningComponent(BaseModel):
    name: str
    amount: float


class PayslipEntryPayload(BaseModel):
    user_id: UUID
    year: int
    month: int
    pf: float = 0.0
    esic: float = 0.0
    prof_tax: float = 0.0
    employer_pf: float = 0.0
    employer_esic: float = 0.0
    # None = use what the payroll calculation says
    paid_days_override: Optional[float] = None
    base_salary_override: Optional[float] = None
    basic_earned_override: Optional[float] = None
    # Parts of the base salary printed as their own earning lines (HRA, ...). Basic = the rest.
    earning_components: list[EarningComponent] = []
    # TDS: a fixed amount, or a % of gross earnings (the % wins when set)
    tds: float = 0.0
    tds_percent: Optional[float] = None
    # Employee payslip details, stored on the employee and reused every month
    pan_number: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_no: Optional[str] = None
    pf_uan_no: Optional[str] = None
    esic_no: Optional[str] = None
    work_location: Optional[str] = None


PAN_REGEX = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")
# 4th character of a PAN is the holder type: Company, Person, HUF, Firm, AOP, Trust, Local authority,
# Juridical person.
PAN_HOLDER_TYPES = "ABCFGHJLPT"


def pan_error(pan: str) -> Optional[str]:
    if not PAN_REGEX.fullmatch(pan):
        return "PAN must be 10 characters: 5 letters, 4 digits, 1 letter (e.g. ABCPE1234F)"
    if pan[3] not in PAN_HOLDER_TYPES:
        return "PAN is not valid: the 4th character must be one of A, B, C, F, G, H, J, L, P, T"
    return None


def parse_components(raw) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return [
        {"name": str(c.get("name", "")), "amount": float(c.get("amount", 0))}
        for c in data if isinstance(c, dict)
    ]


EMPLOYEE_PAYSLIP_FIELDS = ("pan_number", "bank_name", "bank_account_no", "pf_uan_no", "esic_no", "work_location")
OVERRIDE_FIELDS = ("paid_days_override", "base_salary_override", "basic_earned_override")


def _entry_dict(entry: Optional[PayslipEntry]):
    data = {
        "pf": entry.pf if entry else 0.0,
        "esic": entry.esic if entry else 0.0,
        "prof_tax": entry.prof_tax if entry else 0.0,
        "employer_pf": entry.employer_pf if entry else 0.0,
        "employer_esic": entry.employer_esic if entry else 0.0,
    }
    for name in OVERRIDE_FIELDS:
        data[name] = getattr(entry, name) if entry else None
    data["earning_components"] = parse_components(entry.earning_components) if entry else []
    data["tds"] = entry.tds if entry else 0.0
    data["tds_percent"] = entry.tds_percent if entry else None
    return data


@router.get("/payslip-entry")
def get_payslip_entry(
    user_id: UUID,
    year: int,
    month: int,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    emp = db.query(User).filter(User.id == user_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    entry = db.query(PayslipEntry).filter(
        PayslipEntry.user_id == user_id,
        PayslipEntry.year == year,
        PayslipEntry.month == month
    ).first()

    summary = get_payroll_summary(year=year, month=month, current_user=current_user, db=db)
    rec = next((r for r in summary["records"] if r["user_id"] == str(user_id)), None)

    data = _entry_dict(entry)
    if not (entry and entry.earning_components):
        earlier = db.query(PayslipEntry).filter(
            PayslipEntry.user_id == user_id,
            PayslipEntry.earning_components.isnot(None),
            (PayslipEntry.year < year) | ((PayslipEntry.year == year) & (PayslipEntry.month < month))
        ).order_by(PayslipEntry.year.desc(), PayslipEntry.month.desc()).first()
        if earlier:
            data["earning_components"] = parse_components(earlier.earning_components)
    data["employee"] = {name: getattr(emp, name) or "" for name in EMPLOYEE_PAYSLIP_FIELDS}
    data["calc"] = {
        key: rec[key] for key in (
            "base_salary", "total_paid_days", "calculated_salary", "worked_days", "paid_leaves",
            "extra_days_worked", "total_days_present", "adjustments_added", "adjustments_deducted"
        )
    } if rec else None
    return data


@router.put("/payslip-entry")
def save_payslip_entry(
    payload: PayslipEntryPayload,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    if not (1 <= payload.month <= 12) or not (2000 <= payload.year <= 2100):
        raise HTTPException(status_code=400, detail="Invalid month or year")
    values = {
        "pf": payload.pf, "esic": payload.esic, "prof_tax": payload.prof_tax,
        "employer_pf": payload.employer_pf, "employer_esic": payload.employer_esic,
    }
    for name, value in values.items():
        if not math.isfinite(value) or value < 0 or value > MAX_ADJUSTMENT_AMOUNT:
            raise HTTPException(status_code=400, detail=f"Invalid amount for {name}")
    overrides = {name: getattr(payload, name) for name in OVERRIDE_FIELDS}
    for name, value in overrides.items():
        if value is not None and (not math.isfinite(value) or value < 0 or value > MAX_ADJUSTMENT_AMOUNT):
            raise HTTPException(status_code=400, detail=f"Invalid value for {name}")
    if overrides["paid_days_override"] is not None and overrides["paid_days_override"] > 31:
        raise HTTPException(status_code=400, detail="Paid days cannot be more than 31")

    emp = db.query(User).filter(User.id == payload.user_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if not math.isfinite(payload.tds) or payload.tds < 0 or payload.tds > MAX_ADJUSTMENT_AMOUNT:
        raise HTTPException(status_code=400, detail="Invalid TDS amount")
    if payload.tds_percent is not None and (
        not math.isfinite(payload.tds_percent) or payload.tds_percent < 0 or payload.tds_percent > 100
    ):
        raise HTTPException(status_code=400, detail="TDS % must be between 0 and 100")

    components = []
    seen_names = set()
    for comp in payload.earning_components:
        name = comp.name.strip()
        if not name or len(name) > 40:
            raise HTTPException(status_code=400, detail="Each earning needs a name (up to 40 characters)")
        if name.lower() in seen_names or name.lower() == "basic":
            raise HTTPException(status_code=400, detail=f"Duplicate earning name: {name}")
        if not math.isfinite(comp.amount) or comp.amount <= 0 or comp.amount > MAX_ADJUSTMENT_AMOUNT:
            raise HTTPException(status_code=400, detail=f"Amount for {name} must be greater than 0")
        seen_names.add(name.lower())
        components.append({"name": name, "amount": round(comp.amount, 2)})
    if components:
        effective_base = overrides["base_salary_override"]
        if effective_base is None:
            effective_base = get_employee_monthly_salary(
                db, emp.id, payload.year, payload.month, emp.base_salary
            )
        if sum(c["amount"] for c in components) > effective_base + 0.005:
            raise HTTPException(
                status_code=400,
                detail="HRA and other earnings add up to more than the base salary, so Basic would be negative"
            )

    details = {name: (getattr(payload, name) or "").strip() for name in EMPLOYEE_PAYSLIP_FIELDS}
    details["pan_number"] = details["pan_number"].upper()
    if details["pan_number"]:
        error = pan_error(details["pan_number"])
        if error:
            raise HTTPException(status_code=400, detail=error)
    if details["bank_account_no"] and not re.fullmatch(r"[0-9]{6,24}", details["bank_account_no"]):
        raise HTTPException(status_code=400, detail="Bank account number should be digits only")
    for name, value in details.items():
        setattr(emp, name, value or None)

    entry = db.query(PayslipEntry).filter(
        PayslipEntry.user_id == payload.user_id,
        PayslipEntry.year == payload.year,
        PayslipEntry.month == payload.month
    ).first()
    if not entry:
        entry = PayslipEntry(user_id=payload.user_id, year=payload.year, month=payload.month)
        db.add(entry)
    for name, value in values.items():
        setattr(entry, name, round(value, 2))
    for name, value in overrides.items():
        setattr(entry, name, None if value is None else round(value, 2))
    entry.earning_components = json.dumps(components) if components else None
    entry.tds = round(payload.tds, 2)
    entry.tds_percent = payload.tds_percent
    db.commit()
    return _entry_dict(entry)


def _collect_payslips(db: Session, current_user: User, year: int, month: int, only_user_id=None):
    """Build payslip data dicts, reusing the exact /summary calculation so slips always
    match the payroll screen."""
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Invalid month or year")

    summary = get_payroll_summary(year=year, month=month, current_user=current_user, db=db)
    records = summary["records"]
    if only_user_id is not None:
        records = [r for r in records if r["user_id"] == str(only_user_id)]
    else:
        records = [r for r in records if r["base_salary"] > 0 and r["total_paid_days"] > 0]
    if not records:
        return []

    user_ids = [UUID(r["user_id"]) for r in records]
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()}
    entries = {
        e.user_id: e for e in db.query(PayslipEntry).filter(
            PayslipEntry.year == year, PayslipEntry.month == month,
            PayslipEntry.user_id.in_(user_ids)
        ).all()
    }
    adjustments = {}
    for a in db.query(PayrollAdjustment).filter(
        PayrollAdjustment.year == year, PayrollAdjustment.month == month,
        PayrollAdjustment.user_id.in_(user_ids)
    ).order_by(PayrollAdjustment.created_at).all():
        adjustments.setdefault(a.user_id, []).append(a)

    slips = []
    for rec in records:
        uid = UUID(rec["user_id"])
        emp = users[uid]
        entry = entries.get(uid)
        adjs = adjustments.get(uid, [])

        # Admin overrides of the payroll calculation (if any) win over the computed values
        paid_days = rec["total_paid_days"]
        base_salary = rec["base_salary"]
        salary_earned = rec["calculated_salary"]
        if entry:
            if entry.paid_days_override is not None:
                paid_days = entry.paid_days_override
            if entry.base_salary_override is not None:
                base_salary = entry.base_salary_override
            if entry.paid_days_override is not None or entry.base_salary_override is not None:
                salary_earned = round(base_salary / 30.0 * paid_days, 2)
            if entry.basic_earned_override is not None:
                salary_earned = entry.basic_earned_override

        # Split the base salary into Basic + HRA/other lines. Basic is the remainder, and its
        # earned amount is the remainder of the total, so the lines always add up to the salary.
        components = parse_components(entry.earning_components) if entry else []
        component_total = sum(c["amount"] for c in components)
        basic_monthly = round(base_salary - component_total, 2)
        component_rows = [
            (c["name"], c["amount"], round(c["amount"] / 30.0 * paid_days, 2)) for c in components
        ]
        basic_earned = round(salary_earned - sum(r[2] for r in component_rows), 2)

        earnings = [("Basic", basic_monthly, basic_earned)] + component_rows
        earnings += [(a.reason, None, a.amount) for a in adjs if a.kind == "addition"]
        gross = round(sum(e[2] for e in earnings), 2)

        deductions = []
        if entry:
            tds = round(gross * entry.tds_percent / 100.0, 2) if entry.tds_percent is not None else entry.tds
            for label, value in (("PF", entry.pf), ("ESIC", entry.esic), ("Prof. Tax", entry.prof_tax), ("TDS", tds)):
                if value:
                    deductions.append((label, value))
        deductions += [(a.reason, a.amount) for a in adjs if a.kind == "deduction"]
        total_deductions = round(sum(d[1] for d in deductions), 2)

        slips.append({
            "year": year,
            "month": month,
            "employee_id": emp.employee_id,
            "name": emp.name,
            "email": emp.email,
            "personal_email": emp.personal_email,
            "designation": emp.designation,
            "department": emp.department,
            "work_location": emp.work_location,
            "date_of_joining": emp.date_of_joining or (
                date.fromisoformat(emp.joining_date) if emp.joining_date else None
            ),
            "date_of_birth": emp.date_of_birth,
            "pan_number": emp.pan_number,
            "bank_name": emp.bank_name,
            "bank_account_no": emp.bank_account_no,
            "pf_uan_no": emp.pf_uan_no,
            "esic_no": emp.esic_no,
            "paid_days": paid_days,
            "rate_total": base_salary,
            "earnings": earnings,
            "deductions": deductions,
            "gross": gross,
            "total_deductions": total_deductions,
            "net": round(gross - total_deductions, 2),
            "employer_pf": entry.employer_pf if entry else 0.0,
            "employer_esic": entry.employer_esic if entry else 0.0,
        })
    return slips


def _pdf_response(pdf: bytes, filename: str):
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/payslip/{user_id}")
def download_payslip(
    user_id: UUID,
    year: int,
    month: int,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    from app.services.payslip_pdf import build_payslips_pdf

    slips = _collect_payslips(db, current_user, year, month, only_user_id=user_id)
    if not slips:
        raise HTTPException(status_code=404, detail="Employee not found")
    slip = slips[0]
    safe_id = "".join(ch for ch in slip["employee_id"] if ch.isalnum() or ch in "-_") or "employee"
    return _pdf_response(build_payslips_pdf(slips), f"Payslip_{safe_id}_{year}-{month:02d}.pdf")


@router.get("/payslips")
def download_all_payslips(
    year: int,
    month: int,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    """One PDF, one page per employee who has a salary and paid days this month."""
    from app.services.payslip_pdf import build_payslips_pdf

    slips = _collect_payslips(db, current_user, year, month)
    if not slips:
        raise HTTPException(status_code=404, detail="No payable employees for this month")
    return _pdf_response(build_payslips_pdf(slips), f"Payslips_{year}-{month:02d}.pdf")
