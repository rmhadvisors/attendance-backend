from typing import Optional
from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    id: Optional[str] = None
    token_type: str
    role: str
    name: str
    employee_id: str
    face_enrolled: bool
    saturday_policy: Optional[str] = None
    allow_remote_checkin: Optional[bool] = False


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str