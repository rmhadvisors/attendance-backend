from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.attendance import AttendanceLog
from app.models.comp_off import CompOffBalance, CompOffTransaction
from app.models.leave import LeaveRequest
from app.models.notification import FaceVerificationFailure, NotificationLog, PushSubscription
from app.models.user import User
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeResponse,
    EmployeeUpdate
)
from app.core.security import hash_password, require_admin_or_superadmin

router = APIRouter(
    prefix="/employees",
    tags=["Employees"]
)


@router.post(
    "/",
    response_model=EmployeeResponse
)
def create_employee(
    employee: EmployeeCreate,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):

    existing_user = db.query(User).filter(
        (User.employee_id == employee.employee_id) |
        (User.email == employee.email)
    ).first()

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Employee ID or email already exists"
        )

    hashed_password = hash_password(
        employee.password
    )

    new_user = User(
        employee_id=employee.employee_id,
        name=employee.name,
        email=employee.email,
        password_hash=hashed_password,
        phone=employee.phone,
        role=employee.role,
        saturday_policy=employee.saturday_policy or "alt_sat_holiday",
        allow_remote_checkin=employee.allow_remote_checkin
    )

    try:
        db.add(new_user)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Employee ID or email already exists"
        )

    db.refresh(new_user)

    return new_user


@router.get("/", response_model=list[EmployeeResponse])
def get_employees(
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    return db.query(User).filter(User.email != "admin@glrattendance.com").all()

@router.put("/{employee_id}", response_model=EmployeeResponse)
def update_employee(
    employee_id: str,
    employee_data: EmployeeUpdate,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == employee_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Employee not found")

    if employee_data.name is not None:
        user.name = employee_data.name
    if employee_data.email is not None:
        user.email = employee_data.email
    if employee_data.phone is not None:
        user.phone = employee_data.phone
    if employee_data.role is not None:
        user.role = employee_data.role
    if employee_data.saturday_policy is not None:
        user.saturday_policy = employee_data.saturday_policy
    if employee_data.base_salary is not None:
        user.base_salary = employee_data.base_salary
    if employee_data.allow_remote_checkin is not None:
        user.allow_remote_checkin = employee_data.allow_remote_checkin

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Employee ID or email already exists"
        )

    return user

@router.delete("/{employee_id}")
def delete_employee(
    employee_id: str,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == employee_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Employee not found")

    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    if user.email == "admin@glrattendance.com":
        raise HTTPException(status_code=403, detail="The system administrator account (admin@glrattendance.com) cannot be deleted")

    db.query(AttendanceLog).filter(AttendanceLog.override_by == user.id).update(
        {AttendanceLog.override_by: None},
        synchronize_session=False
    )
    db.query(LeaveRequest).filter(LeaveRequest.action_by == user.id).update(
        {LeaveRequest.action_by: None},
        synchronize_session=False
    )
    db.query(CompOffTransaction).filter(CompOffTransaction.approved_by == user.id).update(
        {CompOffTransaction.approved_by: None},
        synchronize_session=False
    )

    db.query(FaceVerificationFailure).filter(FaceVerificationFailure.user_id == user.id).delete(synchronize_session=False)
    db.query(PushSubscription).filter(PushSubscription.user_id == user.id).delete(synchronize_session=False)
    db.query(NotificationLog).filter(NotificationLog.user_id == user.id).delete(synchronize_session=False)
    db.query(LeaveRequest).filter(LeaveRequest.user_id == user.id).delete(synchronize_session=False)
    db.query(CompOffTransaction).filter(CompOffTransaction.user_id == user.id).delete(synchronize_session=False)
    db.query(CompOffBalance).filter(CompOffBalance.user_id == user.id).delete(synchronize_session=False)
    db.query(AttendanceLog).filter(AttendanceLog.user_id == user.id).delete(synchronize_session=False)

    db.delete(user)
    db.commit()
    return {"message": "Employee deleted"}
