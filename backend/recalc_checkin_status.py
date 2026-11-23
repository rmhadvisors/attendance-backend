"""
One-time script: recalculate checkin_status (late / early_bird / on_time) for all existing
attendance records using the current rule (admin-set shift_start only; no shift_start => on_time).

Dry run (default):  python recalc_checkin_status.py
Apply changes:      python recalc_checkin_status.py --apply
"""
import sys
from collections import Counter

from app.core.database import SessionLocal
from app.models.attendance import AttendanceLog
from app.models.user import User
from app.routers.attendance import compute_checkin_status

apply = "--apply" in sys.argv

db = SessionLocal()
try:
    shift_by_user = {u.id: u.shift_start for u in db.query(User).all()}
    logs = db.query(AttendanceLog).filter(AttendanceLog.checkin_time.isnot(None)).all()

    changes = Counter()
    changed = 0
    for log in logs:
        new_status = compute_checkin_status(log.checkin_time, shift_by_user.get(log.user_id))
        if new_status != log.checkin_status:
            changes[(log.checkin_status, new_status)] += 1
            changed += 1
            if apply:
                log.checkin_status = new_status

    print(f"Records with check-in: {len(logs)}")
    print(f"Records that change:   {changed}")
    for (old, new), n in sorted(changes.items(), key=lambda x: -x[1]):
        print(f"  {old} -> {new}: {n}")

    if apply:
        db.commit()
        print("Applied.")
    else:
        db.rollback()
        print("Dry run only. Re-run with --apply to save.")
finally:
    db.close()
