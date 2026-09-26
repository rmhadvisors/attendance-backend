from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from datetime import date
import os

from app.utils.timezone import now_ist, today_ist

from app.core.database import get_db
from app.core.security import get_current_user, require_admin_or_superadmin
from app.models.user import User
from app.models.attendance import AttendanceLog
from app.models.company import Location
from app.models.holiday import Holiday
from app.models.working_days import WorkingDaysConfig
from app.models.comp_off import CompOffBalance, CompOffTransaction
from app.models.notification import FaceVerificationFailure
from app.schemas.attendance import AttendanceResponse, CheckInRequest, CheckOutRequest, OverrideRequest
from app.services.aws_rekognition_service import AwsRekognitionError, compare_faces
from app.services.face_storage_service import FaceStorageError, get_face_path, save_attendance_face, BACKEND_ROOT
from app.services.gps_service import calculate_distance_meters

router = APIRouter(
    prefix="/attendance",
    tags=["Attendance"]
)


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


def is_user_wfh_today(d: date, user_saturday_policy: str) -> bool:
    if d.weekday() != 5:
        return False
    sat_idx = get_saturday_index(d)
    if user_saturday_policy == "all_sat_wfh":
        return True
    if user_saturday_policy == "alt_sat_holiday_rest_wfh" and sat_idx not in [2, 4]:
        return True
    return False


def verify_attendance_face(current_user: User, photo: str, action_type: str, db: Session):
    print(f"\n[FACE VERIFICATION] === Starting Verification for User: {current_user.id} ({current_user.email}) ===")
    
    if not photo:
        print("[FACE VERIFICATION] Error: No face photo provided in request.")
        raise HTTPException(
            status_code=400,
            detail="Face photo is required. Please allow camera access and capture a selfie."
        )

    try:
        enrolled_face_path = get_face_path(current_user.face_image_url)
        print(f"[FACE VERIFICATION] Retrieved enrolled face path/data (length: {len(enrolled_face_path) if enrolled_face_path else 0})")
        print(f"[FACE VERIFICATION] Candidate photo data (length: {len(photo) if photo else 0})")
        
        # Call AWS Rekognition Comparison API
        result = compare_faces(enrolled_face_path, photo)
        print(f"[FACE VERIFICATION] Rekognition Compare Response: {result}")
    except FaceStorageError as exc:
        print(f"[FACE VERIFICATION] Storage Error: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    except AwsRekognitionError as exc:
        print(f"[FACE VERIFICATION] AWS Rekognition Error: {exc}")
        raise HTTPException(
            status_code=502,
            detail=f"Face verification failed: {exc}"
        )

    if not result["is_match"]:
        print(f"[FACE VERIFICATION] Match failed! Similarity: {result['similarity']}% (Threshold: {result['threshold']}%)")
        failure_photo_url = None
        try:
            failure_photo_url = save_attendance_face(photo, current_user.id, f"{action_type}_failed")
            print(f"[FACE VERIFICATION] Saved failure selfie to: {failure_photo_url}")
        except FaceStorageError:
            print("[FACE VERIFICATION] Failed to save failure photo to disk.")

        db.add(FaceVerificationFailure(
            user_id=current_user.id,
            photo_url=failure_photo_url,
            similarity_score=str(result["similarity"]),
            action_type=action_type
        ))
        db.commit()
        print("[FACE VERIFICATION] Logged face verification failure to database.")

        raise HTTPException(
            status_code=403,
            detail="Face not match. Please try again."
        )

    print(f"[FACE VERIFICATION] Match SUCCESSFUL! Similarity: {result['similarity']}% (Threshold: {result['threshold']}%)")
    try:
        photo_url = save_attendance_face(photo, current_user.id, action_type)
        print(f"[FACE VERIFICATION] Saved successful attendance photo to: {photo_url}")
    except FaceStorageError as exc:
        print(f"[FACE VERIFICATION] Storage Error while saving check-in photo: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "photo_url": photo_url,
        "score": result["similarity"]
    }

@router.post("/checkin")
def checkin(
    location_data: CheckInRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user.face_enrolled:
        raise HTTPException(
            status_code=403,
            detail="Face enrollment required before check-in"
        )
    
    today = today_ist()
    is_wfh = is_user_wfh_today(today, current_user.saturday_policy)

    location_valid = False
    if is_wfh:
        location_valid = True
    else:
        active_locations = db.query(Location).filter(
            Location.is_active == True
        ).all()

        if not active_locations:
            raise HTTPException(
                status_code=400,
                detail="No active office location configured"
            )

        for office in active_locations:
            distance = calculate_distance_meters(
                location_data.latitude,
                location_data.longitude,
                office.latitude,
                office.longitude
            )
            print(f"[GPS DEBUG] Checking Location: '{office.name}'")
            print(f"            User coordinates:   Lat {location_data.latitude}, Lng {location_data.longitude}")
            print(f"            Office coordinates: Lat {office.latitude}, Lng {office.longitude}")
            print(f"            Calculated distance: {distance:.2f} meters (Required: <= {office.radius_meters}m)")

            if distance <= office.radius_meters:
                location_valid = True
                break

    if not location_valid:
        if current_user.allow_remote_checkin:
            if not location_data.note or len(location_data.note.strip()) < 3:
                raise HTTPException(
                    status_code=400,
                    detail="A reason note is required when checking in outside the office."
                )
            location_valid = True
        else:
            raise HTTPException(
                status_code=403,
                detail="You are outside the allowed check-in zone"
            )

    existing_attendance = db.query(
        AttendanceLog
    ).filter(
        AttendanceLog.user_id == current_user.id,
        AttendanceLog.date == today
    ).first()

    from app.models.attendance import AttendanceInterval

    if existing_attendance:
        active_interval = db.query(AttendanceInterval).filter(
            AttendanceInterval.attendance_log_id == existing_attendance.id,
            AttendanceInterval.checkout_time == None
        ).first()
        if active_interval:
            raise HTTPException(
                status_code=400,
                detail="Already checked in"
            )
        attendance = existing_attendance
    else:
        attendance = None

    if is_wfh:
        photo_url = None
        if location_data.photo:
            try:
                photo_url = save_attendance_face(location_data.photo, current_user.id, "checkin")
            except FaceStorageError:
                pass
        face_result = {
            "photo_url": photo_url,
            "score": 100.0
        }
    else:
        face_result = verify_attendance_face(
            current_user=current_user,
            photo=location_data.photo,
            action_type="checkin",
            db=db
        )

    now = now_ist()   # IST-aware datetime
    checkin_status = "on_time"

    if now.hour < 8:
        checkin_status = "early_bird"
    elif now.hour >= 11:
        checkin_status = "late"

    if not attendance:
        attendance = AttendanceLog(
            user_id=current_user.id,
            date=today,
            checkin_time=now,
            checkin_lat=location_data.latitude,
            checkin_lng=location_data.longitude,
            checkin_photo_url=face_result["photo_url"],
            checkin_note=location_data.note,
            checkin_face_score=face_result["score"],
            checkin_status=checkin_status,
            day_status="present",
            checkin_mood=location_data.mood,
            checkin_mood_note=location_data.mood_note
        )
        db.add(attendance)
        db.flush()

    new_interval = AttendanceInterval(
        attendance_log_id=attendance.id,
        checkin_time=now,
        checkin_lat=location_data.latitude,
        checkin_lng=location_data.longitude,
        checkin_photo_url=face_result["photo_url"],
        checkin_note=location_data.note,
        checkin_face_score=face_result["score"],
        checkin_mood=location_data.mood,
        checkin_mood_note=location_data.mood_note
    )
    db.add(new_interval)
    db.commit()
    db.refresh(attendance)

    return {
        "message": "Check-in successful",
        "checkin_time": now,
        "status": checkin_status,
        "face_score": face_result["score"]
    }


@router.post("/checkout")
def checkout(
    location_data: CheckOutRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user.face_enrolled:
        raise HTTPException(
            status_code=403,
            detail="Face enrollment required before checkout"
        )
    
    today = today_ist()
    is_wfh = is_user_wfh_today(today, current_user.saturday_policy)

    location_valid = False
    if is_wfh:
        location_valid = True
    else:
        active_locations = db.query(Location).filter(Location.is_active == True).all()

        if not active_locations:
            raise HTTPException(
                status_code=400,
                detail="No active office location configured"
            )

        for office in active_locations:
            distance = calculate_distance_meters(
                location_data.latitude,
                location_data.longitude,
                office.latitude,
                office.longitude
            )
            print(f"[GPS DEBUG] Checking Location: '{office.name}'")
            print(f"            User coordinates:   Lat {location_data.latitude}, Lng {location_data.longitude}")
            print(f"            Office coordinates: Lat {office.latitude}, Lng {office.longitude}")
            print(f"            Calculated distance: {distance:.2f} meters (Required: <= {office.radius_meters}m)")

            if distance <= office.radius_meters:
                location_valid = True
                break

    # if not location_valid:
    #     raise HTTPException(
    #         status_code=403,
    #         detail="You are outside the allowed checkout zone"
    #     )

    if not location_valid:
        if current_user.allow_remote_checkin:
            if not location_data.note or len(location_data.note.strip()) < 3:
                raise HTTPException(
                    status_code=400,
                    detail="A reason note is required when checking out from outside the office."
                )
            location_valid=True
        else:
            raise HTTPException(
                status_code=403,
                detail="You are outside the allowed check-out zone"
            )
        
    attendance = db.query(AttendanceLog).filter(
        AttendanceLog.user_id == current_user.id,
        AttendanceLog.date == today
    ).first()

    if not attendance:
        raise HTTPException(
            status_code=400,
            detail="No check-in found today"
        )

    from app.models.attendance import AttendanceInterval
    active_interval = db.query(AttendanceInterval).filter(
        AttendanceInterval.attendance_log_id == attendance.id,
        AttendanceInterval.checkout_time == None
    ).first()

    if not active_interval:
        raise HTTPException(
            status_code=400,
            detail="No active check-in session found"
        )

    if is_wfh:
        photo_url = None
        if location_data.photo:
            try:
                photo_url = save_attendance_face(location_data.photo, current_user.id, "checkout")
            except FaceStorageError:
                pass
        face_result = {
            "photo_url": photo_url,
            "score": 100.0
        }
    else:
        face_result = verify_attendance_face(
            current_user=current_user,
            photo=location_data.photo,
            action_type="checkout",
            db=db
        )

    now = now_ist()   # IST-aware datetime

    # Update the active interval session
    active_interval.checkout_time = now
    active_interval.checkout_lat = location_data.latitude
    active_interval.checkout_lng = location_data.longitude
    active_interval.checkout_photo_url = face_result["photo_url"]
    active_interval.checkout_note = location_data.note
    active_interval.checkout_face_score = face_result["score"]
    active_interval.checkout_mood = location_data.mood
    active_interval.checkout_mood_note = location_data.mood_note

    # Calculate duration of the current session
    checkin_naive = active_interval.checkin_time.replace(tzinfo=None)
    interval_seconds = (now - checkin_naive).total_seconds()
    active_interval.duration_hours = round(interval_seconds / 3600, 2)

    db.flush()

    # Sum up durations of all completed sessions today
    intervals = db.query(AttendanceInterval).filter(
        AttendanceInterval.attendance_log_id == attendance.id
    ).all()
    total_hours = sum(i.duration_hours or 0.0 for i in intervals)
    total_hours = round(total_hours, 2)

    # Update summary daily log
    attendance.checkout_time = now
    attendance.checkout_lat = location_data.latitude
    attendance.checkout_lng = location_data.longitude
    attendance.checkout_photo_url = face_result["photo_url"]
    attendance.checkout_note = location_data.note
    attendance.checkout_face_score = face_result["score"]
    attendance.checkout_mood = location_data.mood
    attendance.checkout_mood_note = location_data.mood_note
    attendance.total_hours = total_hours

    # PHASE 3: COMP-OFF LOGIC
    holiday = db.query(Holiday).filter(Holiday.date == today).first()
    
    working_days = db.query(WorkingDaysConfig).first()
    days_map = [True, True, True, True, True, True, False]
    if working_days:
        days_map = [
            working_days.monday, working_days.tuesday, working_days.wednesday,
            working_days.thursday, working_days.friday, working_days.saturday,
            working_days.sunday
        ]

    # Evaluate expected working day based on user policy
    is_work_configured = is_user_expected_working_day(
        today,
        current_user.saturday_policy,
        {holiday.date} if holiday else set(),
        days_map
    )
    is_special_day = not is_work_configured

    if is_special_day:
        attendance.day_status = "holiday_work"
        
        balance = db.query(CompOffBalance).filter(CompOffBalance.user_id == current_user.id).first()
        if not balance:
            balance = CompOffBalance(user_id=current_user.id)
            db.add(balance)
        
        # For comp-off work hours requirement
        expected_full_hours = 6.5 if (today.weekday() == 5 and current_user.saturday_policy == "all_sat_half_day") else 8.5
        amount_earned = 1.0 if total_hours >= expected_full_hours else (0.5 if total_hours >= 5.0 else 0.0)
        
        # Update existing earned txn if any, to avoid duplication across checkins/checkouts
        txn = db.query(CompOffTransaction).filter(
            CompOffTransaction.user_id == current_user.id,
            CompOffTransaction.reference_date == today,
            CompOffTransaction.type == "earned"
        ).first()

        old_amount = float(txn.amount) if txn else 0.0
        diff = amount_earned - old_amount
        balance.days_earned = float(balance.days_earned or 0) + diff

        if txn:
            txn.amount = amount_earned
            txn.notes = f"Worked {'full' if total_hours >= expected_full_hours else 'half' if total_hours >= 5.0 else 'zero'} day ({total_hours} hrs) on a holiday/weekend"
        else:
            txn = CompOffTransaction(
                user_id=current_user.id,
                type="earned",
                amount=amount_earned,
                reference_date=today,
                notes=f"Worked {'full' if total_hours >= expected_full_hours else 'half' if total_hours >= 5.0 else 'zero'} day ({total_hours} hrs) on a holiday/weekend"
            )
            db.add(txn)
    else:
        # Expected working day
        expected_full_hours = 6.5 if (today.weekday() == 5 and current_user.saturday_policy == "all_sat_half_day") else 8.5
        if total_hours >= expected_full_hours:
            attendance.day_status = "full_day"
        elif total_hours >= 5.0:
            attendance.day_status = "half_day"
        else:
            attendance.day_status = "absent"

    # Status based on shift completion
    expected_full_hours = 6.5 if (today.weekday() == 5 and current_user.saturday_policy == "all_sat_half_day") else 8.5
    if total_hours < expected_full_hours:
        attendance.checkout_status = "early_leave"
    else:
        attendance.checkout_status = "on_time_out"

    db.commit()

    return {
        "message": "Checkout successful",
        "checkout_time": now,
        "total_hours": total_hours,
        "day_status": attendance.day_status,
        "face_score": face_result["score"]
    }


@router.get("/today")
def today_attendance(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    today = today_ist()
    attendance = db.query(AttendanceLog).filter(
        AttendanceLog.user_id == current_user.id,
        AttendanceLog.date == today
    ).first()

    if not attendance:
        return {"message": "No attendance today", "is_checked_in": False}

    from app.models.attendance import AttendanceInterval
    active_interval = db.query(AttendanceInterval).filter(
        AttendanceInterval.attendance_log_id == attendance.id,
        AttendanceInterval.checkout_time == None
    ).first()

    is_checked_in = active_interval is not None

    resp = {
        "id": str(attendance.id),
        "user_id": str(attendance.user_id),
        "date": attendance.date,
        "checkin_time": attendance.checkin_time,
        "checkout_time": attendance.checkout_time,
        "total_hours": attendance.total_hours,
        "checkin_status": attendance.checkin_status,
        "checkout_status": attendance.checkout_status,
        "day_status": attendance.day_status,
        "checkin_photo_url": attendance.checkin_photo_url,
        "checkout_photo_url": attendance.checkout_photo_url,
        "checkin_note": attendance.checkin_note,
        "checkout_note": attendance.checkout_note,
        "is_manual_override": attendance.is_manual_override,
        "is_anomaly_flagged": attendance.is_anomaly_flagged,
        "checkin_mood": attendance.checkin_mood,
        "checkin_mood_note": attendance.checkin_mood_note,
        "checkout_mood": attendance.checkout_mood,
        "checkout_mood_note": attendance.checkout_mood_note,
        "is_checked_in": is_checked_in
    }
    return resp


@router.get(
    "/history",
    response_model=list[AttendanceResponse]
)
def attendance_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    records = db.query(AttendanceLog).filter(
        AttendanceLog.user_id == current_user.id
    ).order_by(
        AttendanceLog.date.desc()
    ).all()

    return records


@router.get("/all")
def all_attendance_records(
    date_filter: date = None,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    query_date = date_filter or today_ist()
    
    records = db.query(
        AttendanceLog,
        User
    ).join(
        User,
        AttendanceLog.user_id == User.id
    ).filter(
        AttendanceLog.date == query_date,
        User.email != "admin@glrattendance.com"
    ).order_by(
        AttendanceLog.checkin_time.desc()
    ).all()

    locations = db.query(Location).all()

    response_data = []
    for attendance, user in records:
        matched_location = None
        if attendance.checkin_lat is not None and attendance.checkin_lng is not None:
            # Look up matching location by coordinate distance
            for loc in locations:
                dist = calculate_distance_meters(
                    attendance.checkin_lat,
                    attendance.checkin_lng,
                    loc.latitude,
                    loc.longitude
                )
                if dist <= loc.radius_meters + 10:  # 10m buffer for GPS margin
                    matched_location = loc.name
                    break
            
            # Fallback to closest zone if none was within exact geofence radius
            if not matched_location and locations:
                closest_loc = min(
                    locations,
                    key=lambda l: calculate_distance_meters(
                        attendance.checkin_lat,
                        attendance.checkin_lng,
                        l.latitude,
                        l.longitude
                    )
                )
                matched_location = closest_loc.name

        response_data.append({
            "id": attendance.id,
            "user_id": attendance.user_id,
            "employee_id": user.employee_id,
            "employee_name": user.name,
            "date": attendance.date,
            "checkin_time": attendance.checkin_time,
            "checkout_time": attendance.checkout_time,
            "total_hours": attendance.total_hours,
            "checkin_status": attendance.checkin_status,
            "checkout_status": attendance.checkout_status,
            "day_status": attendance.day_status,
            "is_anomaly_flagged": attendance.is_anomaly_flagged,
            "is_manual_override": attendance.is_manual_override,
            "checkin_photo_url": attendance.checkin_photo_url,
            "checkout_photo_url": attendance.checkout_photo_url,
            "checkin_lat": attendance.checkin_lat,
            "checkin_lng": attendance.checkin_lng,
            "checkin_note": attendance.checkin_note,
            "checkout_note": attendance.checkout_note,
            "checkin_mood": attendance.checkin_mood,
            "checkin_mood_note": attendance.checkin_mood_note,
            "checkout_mood": attendance.checkout_mood,
            "checkout_mood_note": attendance.checkout_mood_note,
            "location": matched_location or "Remote"
        })

    return response_data

@router.put("/{attendance_id}/override")
def override_attendance(
    attendance_id: str,
    data: OverrideRequest,
    current_user: User = Depends(require_admin_or_superadmin),
    db: Session = Depends(get_db)
):
    attendance = db.query(AttendanceLog).filter(AttendanceLog.id == attendance_id).first()
    if not attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    # Fetch target user's saturday policy
    target_user = db.query(User).filter(User.id == attendance.user_id).first()
    target_policy = target_user.saturday_policy if target_user else "alt_sat_holiday"
    today_date = attendance.date

    # Determine if it's a holiday / weekend for Saturday policy
    from app.models.holiday import Holiday
    from app.models.working_days import WorkingDaysConfig
    holiday = db.query(Holiday).filter(Holiday.date == today_date).first()
    working_days = db.query(WorkingDaysConfig).first()
    days_map = [True, True, True, True, True, True, False]
    if working_days:
        days_map = [
            working_days.monday, working_days.tuesday, working_days.wednesday,
            working_days.thursday, working_days.friday, working_days.saturday,
            working_days.sunday
        ]
    is_expected_work = is_user_expected_working_day(
        today_date,
        target_policy,
        {holiday.date} if holiday else set(),
        days_map
    )
    is_special_day = not is_expected_work
    expected_full_hours = 6.5 if (today_date.weekday() == 5 and target_policy == "all_sat_half_day") else 8.5

    if data.day_status == "absent":
        attendance.checkin_time = None
        attendance.checkout_time = None
        attendance.total_hours = 0.0
        attendance.checkin_status = None
        attendance.checkout_status = None
        attendance.day_status = "absent"
    else:
        # Update checkin_time
        if data.checkin_time:
            attendance.checkin_time = data.checkin_time

        # Update checkout_time
        attendance.checkout_time = data.checkout_time
        
        if attendance.checkin_time:
            checkin_hour = attendance.checkin_time.hour
            if checkin_hour < 7:
                attendance.checkin_status = "early_bird"
            elif checkin_hour < 11:
                attendance.checkin_status = "on_time"
            else:
                attendance.checkin_status = "late"
        else:
            attendance.checkin_status = None

        if attendance.checkin_time and attendance.checkout_time:
            checkout_naive = attendance.checkout_time.replace(tzinfo=None)
            checkin_naive = attendance.checkin_time.replace(tzinfo=None)
            total_seconds = (checkout_naive - checkin_naive).total_seconds()
            total_hours = round(total_seconds / 3600, 2)
            attendance.total_hours = total_hours
            
            if total_hours >= expected_full_hours:
                attendance.checkout_status = "on_time_out"
            else:
                attendance.checkout_status = "early_leave"
                
            if data.day_status == "present":
                if is_special_day:
                    attendance.day_status = "holiday_work"
                else:
                    if total_hours >= expected_full_hours:
                        attendance.day_status = "full_day"
                    elif total_hours >= 5.0:
                        attendance.day_status = "half_day"
                    else:
                        attendance.day_status = "absent"
            else:
                attendance.day_status = data.day_status
        else:
            attendance.total_hours = 0.0
            attendance.checkout_status = None
            attendance.day_status = data.day_status

    # --- Sync Comp-Off Transactions and Balances ---
    # Query existing transaction
    txn = db.query(CompOffTransaction).filter(
        CompOffTransaction.user_id == attendance.user_id,
        CompOffTransaction.reference_date == today_date,
        CompOffTransaction.type == "earned"
    ).first()

    if attendance.day_status == "holiday_work" and attendance.checkin_time:
        # User is eligible for comp-off
        amount_earned = 1.0 if attendance.total_hours >= expected_full_hours else (0.5 if attendance.total_hours >= 5.0 else 0.0)
        
        balance = db.query(CompOffBalance).filter(CompOffBalance.user_id == attendance.user_id).first()
        if not balance:
            balance = CompOffBalance(user_id=attendance.user_id)
            db.add(balance)
            db.flush()

        old_amount = float(txn.amount) if txn else 0.0
        diff = amount_earned - old_amount
        balance.days_earned = float(balance.days_earned or 0.0) + diff

        if amount_earned > 0.0:
            if txn:
                txn.amount = amount_earned
                txn.notes = f"Manual override: Worked {'full' if attendance.total_hours >= expected_full_hours else 'half'} day ({attendance.total_hours} hrs) on holiday/weekend"
                txn.approved_by = current_user.id
            else:
                txn = CompOffTransaction(
                    user_id=attendance.user_id,
                    type="earned",
                    amount=amount_earned,
                    reference_date=today_date,
                    notes=f"Manual override: Worked {'full' if attendance.total_hours >= expected_full_hours else 'half'} day ({attendance.total_hours} hrs) on holiday/weekend",
                    approved_by=current_user.id
                )
                db.add(txn)
        else:
            if txn:
                db.delete(txn)
    else:
        # If not holiday work (or checkin is missing), delete any earned comp-off txn for this date
        if txn:
            balance = db.query(CompOffBalance).filter(CompOffBalance.user_id == attendance.user_id).first()
            if balance:
                balance.days_earned = float(balance.days_earned or 0.0) - float(txn.amount)
            db.delete(txn)

    attendance.is_manual_override = False
    attendance.override_by = None
    attendance.override_at = None
    attendance.override_note = None

    db.commit()
    db.refresh(attendance)

    return {"message": "Attendance overriden successfully"}

@router.get("/photo")
def get_attendance_photo(
    path: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Check authorization: admins can see all, employees can only see their own
    if current_user.role not in ["admin", "superadmin"]:
        user_pattern = f"/{current_user.id}/"
        user_pattern_win = f"\\{current_user.id}\\"
        if user_pattern not in path and user_pattern_win not in path:
            raise HTTPException(status_code=403, detail="Not authorized to view this photo")

    if path.startswith("http://") or path.startswith("https://"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=path)

    # Try resolving path relative to backend root if it is relative or doesn't exist
    if not os.path.exists(path):
        alt_path = os.path.join(BACKEND_ROOT, path)
        if os.path.exists(alt_path):
            path = alt_path
        else:
            raise HTTPException(status_code=404, detail="Photo file not found")

    return FileResponse(path)
