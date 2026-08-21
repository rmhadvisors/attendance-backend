from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.utils.timezone import now_ist

from app.core.database import get_db
from app.core.security import get_current_user, require_admin_or_superadmin
from app.models.user import User
from app.models.leave import LeaveRequest
from app.models.comp_off import CompOffBalance, CompOffTransaction
from app.schemas.leave import LeaveRequestCreate, LeaveRequestResponse, LeaveActionRequest, CompOffBalanceResponse

router = APIRouter(
    prefix="/leave",
    tags=["Leave & Comp-Off"]
)

@router.get("/balance", response_model=CompOffBalanceResponse)
def get_my_balance(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    balance = db.query(CompOffBalance).filter(CompOffBalance.user_id == current_user.id).first()
    if not balance:
        return CompOffBalanceResponse(days_earned=0, days_used=0, days_paid_out=0, available_balance=0)
    
    available = float(balance.days_earned) - float(balance.days_used) - float(balance.days_paid_out)
    return CompOffBalanceResponse(
        days_earned=float(balance.days_earned),
        days_used=float(balance.days_used),
        days_paid_out=float(balance.days_paid_out),
        available_balance=available
    )

@router.post("/request", response_model=LeaveRequestResponse)
def request_leave(
    data: LeaveRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Calculate requested days
    days_requested = (data.end_date - data.start_date).days + 1
    if days_requested <= 0:
        raise HTTPException(status_code=400, detail="Invalid date range")

    # Allow request even with 0 comp-off balance; the balance will be managed during approval.
    pass

    leave = LeaveRequest(
        user_id=current_user.id,
        start_date=data.start_date,
        end_date=data.end_date,
        reason=data.reason,
        status="pending"
    )
    db.add(leave)
    db.commit()
    db.refresh(leave)

    return {
        "id": leave.id,
        "user_id": leave.user_id,
        "employee_name": current_user.name,
        "start_date": leave.start_date,
        "end_date": leave.end_date,
        "status": leave.status,
        "reason": leave.reason,
        "admin_notes": leave.admin_notes
    }

@router.get("/my-requests", response_model=list[LeaveRequestResponse])
def get_my_requests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    requests = db.query(LeaveRequest).filter(LeaveRequest.user_id == current_user.id).order_by(LeaveRequest.created_at.desc()).all()
    
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "employee_name": current_user.name,
            "start_date": r.start_date,
            "end_date": r.end_date,
            "status": r.status,
            "reason": r.reason,
            "admin_notes": r.admin_notes
        }
        for r in requests
    ]

@router.get("/all", response_model=list[LeaveRequestResponse])
def get_all_requests(
    admin_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    requests = db.query(LeaveRequest, User).join(User, LeaveRequest.user_id == User.id).filter(User.email != "admin@glrattendance.com").order_by(LeaveRequest.created_at.desc()).all()
    return [
        {
            "id": r.LeaveRequest.id,
            "user_id": r.LeaveRequest.user_id,
            "employee_name": r.User.name,
            "start_date": r.LeaveRequest.start_date,
            "end_date": r.LeaveRequest.end_date,
            "status": r.LeaveRequest.status,
            "reason": r.LeaveRequest.reason,
            "admin_notes": r.LeaveRequest.admin_notes
        }
        for r in requests
    ]

@router.post("/{leave_id}/action", response_model=LeaveRequestResponse)
def action_leave(
    leave_id: UUID,
    data: LeaveActionRequest,
    admin_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    leave = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")

    if leave.status != "pending":
        raise HTTPException(status_code=400, detail="Only pending requests can be approved/rejected")

    if data.action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="Invalid action")

    leave.status = "approved" if data.action == "approve" else "rejected"
    leave.admin_notes = data.notes
    leave.action_by = admin_user.id
    leave.action_at = now_ist()

    # If approved, deduct from balance if available
    if data.action == "approve":
        days_requested = (leave.end_date - leave.start_date).days + 1
        balance = db.query(CompOffBalance).filter(CompOffBalance.user_id == leave.user_id).first()
        if not balance:
            balance = CompOffBalance(user_id=leave.user_id)
            db.add(balance)
            db.flush()
            
        available = float(balance.days_earned) - float(balance.days_used) - float(balance.days_paid_out)
        
        if available > 0:
            deduct_amount = min(available, float(days_requested))
            balance.days_used = float(balance.days_used or 0) + deduct_amount
            
            txn = CompOffTransaction(
                user_id=leave.user_id,
                type="used_leave",
                amount=deduct_amount,
                reference_date=leave.start_date,
                notes=f"Leave approved. Deducted {deduct_amount} days from comp-off balance.",
                approved_by=admin_user.id
            )
            db.add(txn)

    db.commit()
    db.refresh(leave)
    
    user = db.query(User).filter(User.id == leave.user_id).first()

    return {
        "id": leave.id,
        "user_id": leave.user_id,
        "employee_name": user.name,
        "start_date": leave.start_date,
        "end_date": leave.end_date,
        "status": leave.status,
        "reason": leave.reason,
        "admin_notes": leave.admin_notes
    }
