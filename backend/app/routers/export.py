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
from app.routers.payroll import get_employee_monthly_salary, get_joining_adjustment, get_adjustment_totals
from app.services.gps_service import calculate_distance_meters

# Hours the attendance policy credits for a day, regardless of hours actually worked.
POLICY_ABSENT_HOURS = 0.0
POLICY_HALF_DAY_HOURS = 5.0

router = APIRouter(
    prefix="/export",
    tags=["Export"]
)


def format_time_display(dt_val):
    if not dt_val:
        return "-"
    if isinstance(dt_val, str):
        try:
            dt_val = datetime.fromisoformat(dt_val)
        except Exception:
            return dt_val
    return dt_val.strftime("%I:%M %p")


def style_worksheet(ws):
    # Enable gridlines
    ws.views.sheetView[0].showGridLines = True

    # Font definitions
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Calibri", size=10)

    # Fills
    header_fill = PatternFill(start_color="0F5132", end_color="0F5132", fill_type="solid")  # Dark green
    zebra_fill = PatternFill(start_color="F8F9FA", end_color="F8F9FA", fill_type="solid")    # Off-white
    
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
            if "details" in header_val:
                # Multi-line, one date per line: wrap and grow the row to fit
                cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
                line_count = str(cell.value or "").count("\n") + 1
                ws.row_dimensions[row_num].height = max(ws.row_dimensions[row_num].height or 20, 15 * line_count + 5)
            elif header_val == "late" or any(term in header_val for term in ["date", "day", "id", "time", "status", "policy", "attendance"]):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif any(term in header_val for term in ["days", "hours", "count", "holidays", "week off", "present", "full", "half", "absent", "working", "target"]):
                cell.alignment = Alignment(horizontal="center", vertical="center")
                if isinstance(cell.value, (int, float)):
                    cell.number_format = "0.0"
            elif "salary" in header_val or "payout" in header_val or header_val.startswith("manual "):
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
            if "salary" in header_val or "payout" in header_val or header_val.startswith("manual "):
                val_str = "₹" + val_str + ".00"
            max_length = max(max_length, len(val_str))
        header_text = str(column_cells[0].value or "").lower()
        if "details" in header_text:
            longest_line = max(
                (len(line) for cell in column_cells for line in str(cell.value or "").split("\n")),
                default=12
            )
            ws.column_dimensions[column_letter].width = max(longest_line, 24) + 4
        else:
            ws.column_dimensions[column_letter].width = max(max_length, 12) + 4


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
    is_individual = False
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
        is_individual = True
    
    employees = emp_query.order_by(User.name).all()
    if len(employees) == 1:
        is_individual = True

    summary_rows = []
    daily_rows = []

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
        week_offs_count = 0
        target_hours = 0.0
        full_days_count = 0
        half_days_count = 0
        absent_days = 0.0
        overtime_hours = 0.0
        absent_hours_total = 0.0
        policy_absent_hours_total = 0.0
        half_day_hours_total = 0.0
        policy_half_day_hours_total = 0.0
        absent_details = []
        half_day_details = []
        paid_leave_details = []
        paid_leave_days = 0.0
        late_days = 0.0
        user_policy = emp.saturday_policy or "alt_sat_holiday"
        _q_year = year or today.year
        _q_month = month or today.month
        join_date, exit_date, billing_days = get_joining_adjustment(
            emp, _q_year, _q_month, calendar.monthrange(_q_year, _q_month)[1]
        )

        for d in date_list:
            is_expected_work = is_user_expected_working_day(
                d, user_policy, holiday_dates, days_map
            )
            log = log_by_date.get(d)
            not_joined = bool(join_date and d < join_date)
            exited = bool(exit_date and d > exit_date)
            if not_joined or exited:
                log = None

            # Daily status determination
            log_day_status = log.day_status if log else None
            log_total_hours = log.total_hours if log else None
            if log and d < today and log.checkin_time is not None and log.checkout_time is None:
                log_day_status = "half_day"
                if log_total_hours is None or log_total_hours == 0.0:
                    log_total_hours = 5.0

            if log:
                if log_day_status == "full_day":
                    day_status_display = "Full Day"
                elif log_day_status == "half_day":
                    day_status_display = "Half Day"
                elif log_day_status == "holiday_work":
                    day_status_display = "Holiday Work"
                elif log_day_status == "absent":
                    day_status_display = "Absent"
                elif log_day_status == "comp_off_leave":
                    day_status_display = "Paid Leave"
                elif log.checkin_time:
                    day_status_display = "Present"
                else:
                    day_status_display = "Absent"
            else:
                if d in holiday_dates:
                    hol_obj = next((h for h in holidays_in_range if h.date == d), None)
                    day_status_display = f"Holiday ({hol_obj.name})" if hol_obj else "Holiday"
                elif not is_expected_work:
                    day_status_display = "Weekly Off"
                elif d <= today:
                    day_status_display = "Absent"
                else:
                    day_status_display = "Upcoming"

            if not_joined:
                day_status_display = "Not Joined"
            elif exited:
                day_status_display = "Exited"

            # Check-in and Check-out times
            checkin_time_str = format_time_display(log.checkin_time) if (log and log.checkin_time) else "-"
            checkout_time_str = format_time_display(log.checkout_time) if (log and log.checkout_time) else "-"
            total_hours_val = round(log_total_hours, 2) if (log and log_total_hours is not None) else 0.0
            checkin_status_str = log.checkin_status.replace('_', ' ').title() if (log and log.checkin_status) else "-"
            is_late = bool(log) and log.checkin_status == "late"
            checkout_status_str = log.checkout_status.replace('_', ' ').title() if (log and log.checkout_status) else "-"

            # Notes
            notes_list = []
            if log:
                if log.checkin_note:
                    notes_list.append(f"In: {log.checkin_note}")
                if log.checkout_note:
                    notes_list.append(f"Out: {log.checkout_note}")
                if log.override_note:
                    notes_list.append(f"Override: {log.override_note}")
            notes_str = "; ".join(notes_list) if notes_list else "-"

            # Actual hours worked on absent / half-day days vs. hours the policy credits for them
            is_absent_day = bool(log) and log_day_status == "absent"
            is_half_day = bool(log) and log_day_status == "half_day"
            absent_hrs = total_hours_val if is_absent_day else 0.0
            policy_absent_hrs = POLICY_ABSENT_HOURS if is_absent_day else 0.0
            half_day_hrs = total_hours_val if is_half_day else 0.0
            policy_half_day_hrs = POLICY_HALF_DAY_HOURS if is_half_day else 0.0
            absent_hours_total += absent_hrs
            policy_absent_hours_total += policy_absent_hrs
            half_day_hours_total += half_day_hrs
            policy_half_day_hours_total += policy_half_day_hrs
            if is_absent_day:
                absent_details.append(f"{d.strftime('%d %b')}: worked {absent_hrs:g}h, counted {policy_absent_hrs:g}h")
            is_paid_leave = bool(log) and log_day_status == "comp_off_leave"
            if is_paid_leave:
                paid_leave_details.append(d.strftime('%d %b'))
            if is_half_day:
                half_day_details.append(f"{d.strftime('%d %b')}: worked {half_day_hrs:g}h, counted {policy_half_day_hrs:g}h")

            daily_entry = {
                "Date": d.strftime("%Y-%m-%d"),
                "Day": d.strftime("%A"),
                "Attendance": day_status_display,
                "Check-In Time": checkin_time_str,
                "Late": "Late" if is_late else "-",
                "Check-Out Time": checkout_time_str,
                "Total Hours": total_hours_val,
                "Absent - Hours Worked": absent_hrs,
                "Absent - Hours Counted": policy_absent_hrs,
                "Half Day - Hours Worked": half_day_hrs,
                "Half Day - Hours Counted": policy_half_day_hrs,
                "Paid Leave Days": 1.0 if is_paid_leave else 0.0,
                "Check-In Status": checkin_status_str,
                "Check-Out Status": checkout_status_str,
                "Notes": notes_str
            }

            if not is_individual:
                daily_entry = {
                    "Employee Name": emp.name,
                    "Employee ID": emp.employee_id or "-",
                    **daily_entry
                }

            daily_rows.append(daily_entry)

            if not_joined or exited:
                continue  # days outside the employment period are not counted anywhere

            if is_late:
                late_days += 1.0

            # Paid leave (admin-granted / comp-off leave) is paid, not deducted
            if log and log_day_status == "comp_off_leave":
                paid_leave_days += 1.0

            # Aggregate calculations
            if is_expected_work:
                expected_working_days += 1.0
                target = 7.0 if (d.weekday() == 5 and user_policy == "all_sat_half_day") else 9.0
                target_hours += target

                if log:
                    if log_day_status in ["full_day", "holiday_work"]:
                        worked_days += 1.0
                        full_days_count += 1
                    elif log_day_status == "half_day":
                        worked_days += 0.5
                        total_deductions += 0.5
                        half_days_count += 1
                    elif log_day_status == "absent":
                        total_deductions += 1.0
                        absent_days += 1.0

                    # Overtime hours calculation on expected working days
                    if log_total_hours is not None:
                        overtime_hours += max(0.0, log_total_hours - target)
                else:
                    if d <= today:
                        total_deductions += 1.0
                        absent_days += 1.0
            else:
                # Company holiday (e.g. Dussehra) takes precedence; otherwise it's a weekly off
                if d in holiday_dates:
                    holidays_count += 1
                else:
                    week_offs_count += 1
                if log:
                    if log_day_status in ["full_day", "holiday_work"]:
                        worked_days += 1.0
                        extra_days_worked += 1.0
                        full_days_count += 1
                    elif log_day_status == "half_day":
                        worked_days += 0.5
                        extra_days_worked += 0.5
                        half_days_count += 1

        # Calculate base monthly salary
        q_year = year or today.year
        q_month = month or today.month
        base_salary = get_employee_monthly_salary(db, emp.id, q_year, q_month, emp.base_salary)

        overtime_days = overtime_hours / 9.0
        # No work and no paid leave in the period => nothing is payable (week offs and
        # holidays are not paid for an employee who never worked).
        has_paid_attendance = worked_days > 0 or paid_leave_days > 0 or any(l.day_status == "present" for l in logs)
        total_payable_days = max(0.0, billing_days - total_deductions + extra_days_worked) if has_paid_attendance else 0.0
        total_paid_days = (total_payable_days + overtime_days) if has_paid_attendance else 0.0
        calculated_salary = 0.0
        if base_salary > 0:
            calculated_salary = (base_salary / 30.0) * total_paid_days
        adj_added, adj_deducted = get_adjustment_totals(db, emp.id, _q_year, _q_month)

        total_hours_worked = sum(
            5.0 if (log.date < today and log.checkin_time is not None and log.checkout_time is None) else (log.total_hours or 0.0)
            for log in logs
        )

        summary_rows.append({
            "Employee Name": emp.name,
            "Employee ID": emp.employee_id or "-",
            "Designation": emp.designation or "-",
            "Reporting Manager": emp.reporting_manager or "-",
            "Date of Joining": emp.joining_date or "-",
            "Date of Exit": emp.date_of_exit.isoformat() if emp.date_of_exit else "-",
            "Total Working Days": expected_working_days,
            "Target Hours": target_hours,
            "Days Present": full_days_count + half_days_count,
            "Full Days": full_days_count,
            "Half Days": half_days_count,
            "Absent Days": absent_days,
            "Late Days": late_days,
            "Absent - Hours Worked": round(absent_hours_total, 2),
            "Absent - Hours Counted": round(policy_absent_hours_total, 2),
            "Half Day - Hours Worked": round(half_day_hours_total, 2),
            "Half Day - Hours Counted": round(policy_half_day_hours_total, 2),
            "Absent Days - Date-wise Details": "\n".join(absent_details) or "-",
            "Half Days - Date-wise Details": "\n".join(half_day_details) or "-",
            "Paid Leave - Date-wise Details": "\n".join(paid_leave_details) or "-",
            "Week Offs": week_offs_count,
            "Holidays": holidays_count,
            "Paid Leave Days": paid_leave_days,
            "Total Work (Days)": worked_days,
            # Fixed 30-day month: 30 - deductions + extra days worked. Week offs, holidays and
            # paid leave are not deducted, but the result is 0 if nothing was worked and no paid leave.
            "Total Payable Days": total_payable_days,
            "Total Hours Worked": round(total_hours_worked, 2),
            "Calculated Salary": round(calculated_salary, 2),
            "Manual Additions": adj_added,
            "Manual Deductions": adj_deducted,
            "Net Salary": round(calculated_salary + adj_added - adj_deducted, 2)
        })

    df_summary = pd.DataFrame(summary_rows)
    if df_summary.empty:
        df_summary = pd.DataFrame(columns=[
            "Employee Name",
            "Employee ID",
            "Designation",
            "Reporting Manager",
            "Date of Joining",
            "Date of Exit",
            "Total Working Days",
            "Target Hours",
            "Days Present",
            "Full Days",
            "Half Days",
            "Absent Days",
            "Late Days",
            "Absent - Hours Worked",
            "Absent - Hours Counted",
            "Half Day - Hours Worked",
            "Half Day - Hours Counted",
            "Absent Days - Date-wise Details",
            "Half Days - Date-wise Details",
            "Paid Leave - Date-wise Details",
            "Week Offs",
            "Holidays",
            "Paid Leave Days",
            "Total Work (Days)",
            "Total Payable Days",
            "Total Hours Worked",
            "Calculated Salary",
            "Manual Additions",
            "Manual Deductions",
            "Net Salary"
        ])

    df_daily = pd.DataFrame(daily_rows)
    if df_daily.empty:
        daily_cols = ["Date", "Day", "Attendance", "Check-In Time", "Late", "Check-Out Time", "Total Hours", "Absent - Hours Worked", "Absent - Hours Counted", "Half Day - Hours Worked", "Half Day - Hours Counted", "Paid Leave Days", "Check-In Status", "Check-Out Status", "Notes"]
        if not is_individual:
            daily_cols = ["Employee Name", "Employee ID"] + daily_cols
        df_daily = pd.DataFrame(columns=daily_cols)

    os.makedirs("exports", exist_ok=True)
    file_name = f"attendance_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    file_path = os.path.join("exports", file_name)

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        if is_individual:
            # For individual reports, place the detailed Daily Attendance sheet first
            df_daily.to_excel(writer, index=False, sheet_name="Daily Attendance")
            style_worksheet(writer.sheets["Daily Attendance"])
            df_summary.to_excel(writer, index=False, sheet_name="Attendance Summary")
            style_worksheet(writer.sheets["Attendance Summary"])
        else:
            # For multi-employee reports, place summary first, then daily breakdown
            df_summary.to_excel(writer, index=False, sheet_name="Attendance Summary")
            style_worksheet(writer.sheets["Attendance Summary"])
            df_daily.to_excel(writer, index=False, sheet_name="Daily Attendance")
            style_worksheet(writer.sheets["Daily Attendance"])

    return FileResponse(
        path=file_path,
        filename=file_name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )