import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.user import User


def main():
    email = os.getenv("ADMIN_EMAIL", "admin@glrattendance.com")
    password = os.getenv("ADMIN_PASSWORD")
    name = os.getenv("ADMIN_NAME", "GLR Admin")
    employee_id = os.getenv("ADMIN_EMPLOYEE_ID", "ADMIN-001")

    if not password:
        raise RuntimeError("ADMIN_PASSWORD is required.")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()

        if user:
            user.password_hash = hash_password(password)
            user.role = "admin"
            user.is_active = True
            user.face_enrolled = True
            user.employee_id = user.employee_id or employee_id
            user.name = user.name or name
            message = "Admin user already existed; password reset."
        else:
            db.add(User(
                employee_id=employee_id,
                name=name,
                email=email,
                password_hash=hash_password(password),
                phone="",
                role="admin",
                face_enrolled=True,
                is_active=True
            ))
            message = "Admin user created."

        db.commit()
        print(message)
        print(f"Email: {email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
