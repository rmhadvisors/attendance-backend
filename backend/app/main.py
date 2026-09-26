from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from apscheduler.schedulers.background import BackgroundScheduler
from app.services.reminder_scheduler import check_and_send_reminders, auto_close_unclosed_attendance

from app.core.database import engine, Base

# ── Import ALL models so create_all registers every table ──────────────────
from app.models.user import User  # noqa: F401
from app.models.attendance import AttendanceLog, AttendanceInterval  # noqa: F401
from app.models.company import Company, Location  # noqa: F401
from app.models.holiday import Holiday  # noqa: F401
from app.models.working_days import WorkingDaysConfig  # noqa: F401
from app.models.comp_off import CompOffBalance, CompOffTransaction  # noqa: F401
from app.models.leave import LeaveRequest  # noqa: F401
from app.models.payroll import MonthlySalary  # noqa: F401
from app.models.notification import (  # noqa: F401
    NotificationLog,
    PushSubscription,
    FaceVerificationFailure
)

# ── Create all tables (Already created, bypassed for near-instant startup) ──
Base.metadata.create_all(bind=engine)

# ── Safe Database Column Migrations (Bypassed for near-instant startup) ─────
# from sqlalchemy import text
# with engine.connect() as conn:
#     try:
#         conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS base_salary FLOAT DEFAULT 0.0;"))
#         conn.commit()
#     except Exception as e:
#         print("Safe migration skipped or error:", e)

from contextlib import asynccontextmanager
from app.utils.timezone import IST

# Check-in window configuration (actual check-in 8am-12pm + 30m buffer -> 8:00-12:55 IST Mon-Sat)
REMINDER_WINDOW_DAYS = os.getenv("REMINDER_WINDOW_DAYS", "mon-sat")
REMINDER_WINDOW_HOURS = os.getenv("REMINDER_WINDOW_HOURS", "8-12")
REMINDER_INTERVAL_MINUTES = os.getenv("REMINDER_INTERVAL_MINUTES", "*/5")

scheduler = BackgroundScheduler(timezone=IST)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not scheduler.running:
        scheduler.add_job(
            check_and_send_reminders,
            'cron',
            day_of_week=REMINDER_WINDOW_DAYS,
            hour=REMINDER_WINDOW_HOURS,
            minute=REMINDER_INTERVAL_MINUTES,
            id="checkin_reminders",
            replace_existing=True,
            max_instances=1,
            timezone=IST
        )
        scheduler.add_job(
            auto_close_unclosed_attendance,
            'cron',
            hour=23,
            minute=50,
            id="auto_close_attendance",
            replace_existing=True,
            max_instances=1,
            timezone=IST
        )
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown()

# ── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="GLR Attendance", lifespan=lifespan)

# ── CORS Middleware ──────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"https://.*\.vercel\.app|https://.*\.taxplanadvisor\.in|https://.*\.glrattendance\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ─────────────────────────────────────────────────────────────────
from app.routers import auth, employees, attendance, face, location, dashboard, export, company, leave, payroll, push  # noqa: E402

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(attendance.router)
app.include_router(face.router)
app.include_router(location.router)
app.include_router(export.router)
app.include_router(employees.router)
app.include_router(company.router)
app.include_router(leave.router)
app.include_router(payroll.router)
app.include_router(push.router)



@app.get("/")
def home():
    return {"message": "GLR Attendance Running"}

