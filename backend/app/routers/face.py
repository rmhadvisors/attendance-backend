from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_admin_or_superadmin
from app.models.user import User
from app.services.azure_face_service import AzureFaceError, verify_faces
from app.services.face_storage_service import FaceStorageError, save_enrolled_face

router = APIRouter(
    prefix="/face",
    tags=["Face Verification"]
)


@router.get("/status")
def face_status(
    current_user: User = Depends(get_current_user)
):

    return {
        "face_enrolled": current_user.face_enrolled
    }


class FaceEnrollRequest(BaseModel):
    photo: str


class AzureFaceVerifyTestRequest(BaseModel):
    enrolled_photo: str
    candidate_photo: str

@router.post("/enroll")
def enroll_face(
    data: FaceEnrollRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        face_image_url = save_enrolled_face(data.photo, current_user.id)
    except FaceStorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    current_user.face_enrolled = True
    current_user.face_image_url = face_image_url
    
    db.commit()

    return {
        "message": "Face enrolled successfully",
        "face_enrolled": True,
        "face_image_url": current_user.face_image_url
    }


@router.post("/test-azure-verify")
def test_azure_verify(
    data: AzureFaceVerifyTestRequest,
    current_user: User = Depends(require_admin_or_superadmin)
):
    try:
        result = verify_faces(data.enrolled_photo, data.candidate_photo)
    except AzureFaceError as exc:
        raise HTTPException(
            status_code=exc.status_code or 400,
            detail={
                "message": str(exc),
                "azure_payload": exc.payload
            }
        )

    return {
        "message": "Azure Face verification call completed",
        **result
    }
