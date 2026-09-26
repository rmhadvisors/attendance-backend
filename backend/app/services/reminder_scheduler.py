from datetime import datetime, timedelta
import logging
from sqlalchemy.orm import Session
from pywebpush import webpush, WebPushException
import json
from urllib.parse import urlparse
import os

from app.core.database import SessionLocal
from app.utils.timezone import now_ist, today_ist, IST
from app.models.user import User
from app.models.attendance import AttendanceLog, AttendanceInterval
from app.models.notification import NotificationLog, PushSubscription
from app.models.holiday import Holiday
from app.models.working_days import WorkingDaysConfig
from app.routers.attendance import is_user_expected_working_day

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "9T4SHbsYgUO4pbpk5_SkqPuvuIZBBPohBXML1VSAmOE")

def calculate_target_time(preferred_time_str: str) -> str:
    """Adds 30 minutes to 'HH:MM' string (e.g. '09:00' -> '09:30')"""
    try:
        t = datetime.strptime(preferred_time_str, '%H:%M')
        t_plus_30 = t + timedelta(minutes=30)
        return t_plus_30.strftime('%H:%M')
    except ValueError:
        return ""

def check_and_send_reminders():
    now = now_ist()
    today = today_ist()

    # 1. Non-DB check: Exclude Sundays immediately without opening a database session
    if today.weekday() == 6:
        return

    # 2. Non-DB check: Avoid opening a DB session if outside check-in reminder hours (08:00 - 13:00 IST)
    if now.hour < 8 or now.hour > 13:
        return

    db: Session = SessionLocal()
    try:
        # 3. Get holidays and working days map for Saturday check
        holiday = db.query(Holiday).filter(Holiday.date == today).first()
        working_days = db.query(WorkingDaysConfig).first()
        days_map = [True, True, True, True, True, True, False]
        if working_days:
            days_map = [
                working_days.monday, working_days.tuesday, working_days.wednesday,
                working_days.thursday, working_days.friday, working_days.saturday,
                working_days.sunday
            ]

        # 4. Query active users with reminders enabled
        users = db.query(User).filter(
            User.is_active == True,
            User.enable_checkin_reminder == True
        ).all()

        if not users:
            return
        for user in users:
            # print(
            #     f"\n========== CHECKING USER: {user.name} ({user.id}) =========="
            # )

            # print(f"Current IST time: {now}")
            # print(f"Today: {today}")
            # print(f"Preferred time: {user.preferred_checkin_time}")
            # Verify if today is an expected working day for this user
        

            is_work_day = is_user_expected_working_day(
                today,
                user.saturday_policy,
                {holiday.date} if holiday else set(),
                days_map
            )

            # print(f"Is working day: {is_work_day}")
            
            if not is_work_day:
                continue
            # Parse preferred time HH:MM
            pref_str = user.preferred_checkin_time or "09:00"
            target_str = calculate_target_time(pref_str)

            #print(f"Preferred time: {pref_str}")
            #print(f"Target time (+30 min): {target_str}")

            if not target_str:
                continue

            try:
                target_h, target_m = map(int, target_str.split(":"))
                current_minutes = now.hour * 60 + now.minute
                target_minutes = target_h * 60 + target_m

                #print(f"Current minutes: {current_minutes}")
                #print(f"Target minutes: {target_minutes}")

                # Send only after preferred time + 30 minutes
                if current_minutes < target_minutes:
                    # print("❌ SKIP: 30-minute waiting period has not passed")
                    continue

            except ValueError:
                continue
            # Check if user already checked in today
            already_checked_in = db.query(AttendanceLog).filter(
                AttendanceLog.user_id == user.id,
                AttendanceLog.date == today,
                AttendanceLog.checkin_time != None
            ).first()
            if already_checked_in:
                #print(f"Already checked in: {bool(already_checked_in)}")
                continue
            # Check if reminder was already sent today
            start_of_today = datetime(
                today.year,
                today.month,
                today.day
            )

            today_sent = db.query(NotificationLog).filter(
                NotificationLog.user_id == user.id,
                NotificationLog.type == "checkin_reminder",
                NotificationLog.status == "sent",
                NotificationLog.sent_at >= start_of_today
            ).first()

            #print(f"Reminder already sent today: {bool(today_sent)}")

            if today_sent:
                #print("❌ SKIP: Reminder already sent today")
                continue
            #Send Web Push Notification to user's devices
            subscriptions = db.query(PushSubscription).filter(
                PushSubscription.user_id == user.id
            ).all()
            #print(f"Push subscriptions found: {len(subscriptions)}")

            if not subscriptions:
                #print("User has no subscriptions")
                continue

            sent_any = False
            for sub in subscriptions:
                endpoint = sub.endpoint

                parsed = urlparse(endpoint)
                audience = f"{parsed.scheme}://{parsed.netloc}"
                # print(f"🌐 Push endpoint: {endpoint}")
                # print(f"🎯 VAPID audience: {audience}")
                try:
                    #print(f"Attempting push for {user.name}")
                    res = webpush(
                        subscription_info={
                            "endpoint": sub.endpoint,
                            "keys": {"p256dh": sub.p256dh, "auth": sub.auth}
                        },
                        data=json.dumps({
                            "title": "Check in Reminder ⏰",
                            "body": "You forgot to check in today!"
                        }),
                        vapid_private_key=VAPID_PRIVATE_KEY,
                        vapid_claims={
                            "sub": "mailto:admin@glrattendance.com",
                            "aud": audience
                        },
                        ttl=86400,
                        headers={"Urgency": "high"}
                    )
                    # print(
                    #     f"✅ PUSH SENT to {user.name} "
                    #     f"- Status: {res.status_code}"
                    # )
                    #logging.info(f"Push notification sent successfully to user {user.name} ({user.id}), status: {res.status_code}")
                    sent_any = True
                    break  # Stop after sending 1 notification to prevent duplicate popups
                except WebPushException as exc:
                    logging.error(f"Push failed for user {user.name} ({user.id}): {exc}")
                    if exc.response is not None and exc.response.status_code in [404, 410]:
                        db.delete(sub)
                        db.commit()

            # Log reminder dispatch in notifications_log only if push was delivered
            if sent_any:
                # print(f"📝 Creating NotificationLog for {user.name}")
                db.add(NotificationLog(
                    user_id=user.id,
                    type="checkin_reminder",
                    status="sent",
                    sent_at=now,
                    payload={"preferred_time": pref_str}
                ))
                db.commit()

                # print(f"✅ NotificationLog saved for {user.name}")
    finally:
        db.close()


def auto_close_unclosed_attendance():
    """
    Runs at end-of-day (23:50 IST).
    Finds employees who checked in today (or earlier) but forgot to check out,
    and automatically marks them as 'half_day' with 5.0 hours.
    """
    today = today_ist()
    db: Session = SessionLocal()
    try:
        unclosed_logs = db.query(AttendanceLog).filter(
            AttendanceLog.date <= today,
            AttendanceLog.checkin_time != None,
            AttendanceLog.checkout_time == None,
            AttendanceLog.day_status == "present"
        ).all()

        now = now_ist()
        for log in unclosed_logs:
            log.day_status = "half_day"
            log.total_hours = 5.0
            log.checkout_status = "missing_checkout"

            # Close any open session interval
            active_intervals = db.query(AttendanceInterval).filter(
                AttendanceInterval.attendance_log_id == log.id,
                AttendanceInterval.checkout_time == None
            ).all()
            for interval in active_intervals:
                interval.checkout_time = now
                interval.duration_hours = 5.0

        if unclosed_logs:
            db.commit()
    finally:
        db.close()