import calendar
from datetime import datetime, date, timedelta
import os
from typing import Optional
import pandas as pd

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from app.core.database import get_db
from app.core.security import require_admin_or_superadmin
from app.models.user import User
from app.models.attendance import AttendanceLog
from app.models.company import Location
from app.models.holiday import Holiday
from app.models.working_days import WorkingDaysConfig
from app.routers.attendance import is_user_expected_working_day
from app.routers.payroll import get_employee_monthly_salary
from app.services.gps_service import calculate_distance_meters

router = APIRouter(
    prefix="/export",
    tags=["Export"]
)


def style_worksheet(ws):
    # Enable gridlines
    ws.views.sheetView[0].showGridLines = True

    # Font definitions
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Calibri", size=10)

    # Fills
    header_fill = PatternFill(start_color="0F5132", end_color="0F5132", fill_type="solid") # Dark green
    zebra_fill = PatternFill(start_color="F8F9FA", end_color="F8F9FA", fill_type="solid") # Off-white
    
    # Borders
    thin_border = Border(
        left=Side(style='thin', color='E0E0E0'),
        right=Side(style='thin', color='E0E0E0'),
        top=Side(style='thin', color='E0E0E0'),
        bottom=Side(style='thin', color='E0E0E0')
    )

    ws.row_dimensions[1].height = 28

    # Style headers
    for col_num in range(1, ws.max_column + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
        
    # Style rows
    for row_num in range(2, ws.max_row + 1):
        ws.row_dimensions[row_num].height = 20
        is_even = row_num % 2 == 0
        row_fill = zebra_fill if is_even else None
        
        for col_num in range(1, ws.max_column + 1):
            cell = ws.cell(row=row_num, column=col_num)
            cell.font = data_font
            cell.border = thin_border
            if row_fill:
                cell.fill = row_fill
                
            # Alignment and format rules
            header_val = (ws.cell(row=1, column=col_num).value or "").lower()
            
            # Alignments based on headers
            if any(term in header_val for term in ["date", "id", "time", "status", "policy"]):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif any(term in header_val for term in ["days", "hours", "count", "holidays", "present", "full", "half", "absent"]):
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.number_format = "0.0"
            elif "salary" in header_val or "payout" in header_val:
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.number_format = "₹#,##0.00"
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # Autofit column dimensions
    for column_cells in ws.columns:
        max_length = 0
        column_letter = column_cells[0].column_letter
        for cell in column_cells:
            val_str = str(cell.value or '')
            header_val = (ws.cell(row=1, column=cell.column).value or "").lower()
            if "salary" in header_val or "payout" in header_val:
                val_str = "₹" + val_str + ".00"
            max_length = max(max_length, len(val_str))
        ws.column_dimensions[column_letter].width = max(max_length, 12) + 3


@router.get("/attendance/xlsx")
def export_attendance_excel(
    query_date: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    employee_id: Optional[str] = Query(None),
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    from sqlalchemy import func
    from app.utils.timezone import today_ist

    today = today_ist()

    # Determine date range
    if year and month:
        num_days = calendar.monthrange(year, month)[1]
        start_dt = date(year, month, 1)
        end_dt = date(year, month, num_days)
    elif query_date:
        try:
            parsed_date = datetime.strptime(query_date, "%Y-%m-%d").date()
            start_dt = parsed_date
            end_dt = parsed_date
        except ValueError:
            start_dt = date(today.year, today.month, 1)
            end_dt = today
    elif year:
        start_dt = date(year, 1, 1)
        end_dt = date(year, 12, 31)
    else:
        # All time: fetch range from database
        min_date_val = db.query(func.min(AttendanceLog.date)).scalar()
        max_date_val = db.query(func.max(AttendanceLog.date)).scalar()
        if min_date_val and max_date_val:
            start_dt = min_date_val
            end_dt = max_date_val
        else:
            start_dt = date(today.year, today.month, 1)
            end_dt = today

    # Generate list of dates in the range
    delta = end_dt - start_dt
    date_list = [start_dt + timedelta(days=i) for i in range(delta.days + 1)]

    # Fetch expected working days config
    working_days_cfg = db.query(WorkingDaysConfig).first()
    days_map = [True, True, True, True, True, True, False]
    if working_days_cfg:
        days_map = [
            working_days_cfg.monday, working_days_cfg.tuesday, working_days_cfg.wednesday,
            working_days_cfg.thursday, working_days_cfg.friday, working_days_cfg.saturday,
            working_days_cfg.sunday
        ]

    # Fetch holidays in this range
    holidays_in_range = db.query(Holiday).filter(
        Holiday.date >= start_dt,
        Holiday.date <= end_dt
    ).all()
    holiday_dates = {h.date for h in holidays_in_range}

    # Fetch targeted employees
    emp_query = db.query(User).filter(User.email != "admin@glrattendance.com")
    if employee_id:
        from uuid import UUID
        is_uuid = False
        try:
            UUID(employee_id)
            is_uuid = True
        except ValueError:
            pass

        if is_uuid:
            emp_query = emp_query.filter((User.employee_id == employee_id) | (User.id == employee_id))
        else:
            emp_query = emp_query.filter(User.employee_id == employee_id)
    
    employees = emp_query.order_by(User.name).all()
    summary_rows = []

    for emp in employees:
        logs = db.query(AttendanceLog).filter(
            AttendanceLog.user_id == emp.id,
            AttendanceLog.date >= start_dt,
            AttendanceLog.date <= end_dt
        ).all()
        log_by_date = {log.date: log for log in logs}

        expected_working_days = 0.0
        worked_days = 0.0
        extra_days_worked = 0.0
        total_deductions = 0.0
        holidays_count = 0
        target_hours = 0.0
        full_days_count = 0
        half_days_count = 0
        absent_days = 0.0
        overtime_hours = 0.0
        user_policy = emp.saturday_policy or "alt_sat_holiday"

        for d in date_list:
            is_expected_work = is_user_expected_working_day(
                d, user_policy, holiday_dates, days_map
            )
            log = log_by_date.get(d)

            if is_expected_work:
                expected_working_days += 1.0
                target = 7.0 if (d.weekday() == 5 and user_policy == "all_sat_half_day") else 9.0
                target_hours += target

                if log:
                    if log.day_status in ["full_day", "holiday_work"]:
                        worked_days += 1.0
                        full_days_count += 1
                    elif log.day_status == "half_day":
                        worked_days += 0.5
                        total_deductions += 0.5
                        half_days_count += 1
                    elif log.day_status == "absent":
                        total_deductions += 1.0
                        absent_days += 1.0

                    # Overtime hours calculation on expected working days
                    if log.total_hours is not None:
                        overtime_hours += max(0.0, log.total_hours - target)
                else:
                    if d <= today:
                        total_deductions += 1.0
                        absent_days += 1.0
            else:
                holidays_count += 1
                if log:
                    if log.day_status in ["full_day", "holiday_work"]:
                        worked_days += 1.0
                        extra_days_worked += 1.0
                        full_days_count += 1
                    elif log.day_status == "half_day":
                        worked_days += 0.5
                        extra_days_worked += 0.5
                        half_days_count += 1

        # Calculate base monthly salary
        q_year = year or today.year
        q_month = month or today.month
        base_salary = get_employee_monthly_salary(db, emp.id, q_year, q_month, emp.base_salary)

        overtime_days = overtime_hours / 9.0
        total_paid_days = max(0.0, 30.0 - total_deductions + extra_days_worked + overtime_days)
        calculated_salary = 0.0
        if base_salary > 0:
            calculated_salary = ((base_salary / 30.0) * total_paid_days) * 0.99

        total_hours_worked = sum(log.total_hours or 0.0 for log in logs)

        summary_rows.append({
            "name": emp.name,
            "total no of days": expected_working_days,
            "total target hours": target_hours,
            "no of days present": full_days_count + half_days_count,
            "no of days full day": full_days_count,
            "half day": half_days_count,
            "absent": absent_days,
            "no. holidays in month": holidays_count,
            "total hours worked": round(total_hours_worked, 2),
            "total salary calculated": round(calculated_salary, 2)
        })

    df = pd.DataFrame(summary_rows)
    if df.empty:
        df = pd.DataFrame(columns=[
            "name",
            "total no of days",
            "total target hours",
            "no of days present",
            "no of days full day",
            "half day",
            "absent",
            "no. holidays in month",
            "total hours worked",
            "total salary calculated"
        ])

    os.makedirs("exports", exist_ok=True)
    file_name = f"attendance_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    file_path = os.path.join("exports", file_name)

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Attendance Summary")
        style_worksheet(writer.sheets["Attendance Summary"])

    return FileResponse(
        path=file_path,
        filename=file_name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )