import uuid
from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_admin_or_superadmin
from app.models.letter import GeneratedLetter
from app.models.user import User
from app.services import letter_storage
from app.services.letters_docx import DOCX_MIME, LETTER_TYPES, build_letter_docx

router = APIRouter(prefix="/letters", tags=["Letters"])


# ── Request models (one per letter type) ─────────────────────────────────────

class ExperienceLetterRequest(BaseModel):
    date: date
    employee_name: str = Field(min_length=1, max_length=200)
    designation: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=200)
    tenure_from: date
    tenure_to: date
    tasks: str = Field(min_length=1, max_length=1000)
    manager_name: str = Field(min_length=1, max_length=200)
    gender: Literal["male", "female"] = "male"


class RelievingLetterRequest(BaseModel):
    date: date
    employee_name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=500)
    designation: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=200)
    tenure_from: date
    tenure_to: date
    relieving_date: date
    subject: str = Field(default="", max_length=200)
    manager_name: str = Field(min_length=1, max_length=200)
    tasks: str = Field(min_length=1, max_length=1000)


class OfferLetterRequest(BaseModel):
    date: date
    employee_name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=500)
    designation: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=200)
    joining_date: date
    probation_months: int = Field(ge=1, le=24)
    notice_months: int = Field(ge=1, le=24)
    compensation: float = Field(gt=0, lt=100000000)
    tds_percent: float = Field(ge=0, le=100)
    subject: str = Field(default="", max_length=200)
    manager_name: str = Field(min_length=1, max_length=200)


class BondRequest(BaseModel):
    date: date
    title: Literal["Mr.", "Ms.", "Mrs."]
    employee_name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=500)
    designation: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=200)
    effective_date: date
    bond_from: date
    bond_to: date
    penalty_months: int = Field(ge=1, le=60)
    notice_months: int = Field(ge=1, le=60)
    signatory_name: str = Field(min_length=1, max_length=200)
    signatory_designation: str = Field(min_length=1, max_length=200)


# ── Generation ───────────────────────────────────────────────────────────────

OPTIONAL_FIELDS = {"subject"}  # may be left blank; the letter then uses its standard wording


def _safe_name(name: str) -> str:
    return "".join(ch for ch in name if ch.isalnum() or ch in " -_").strip() or "Employee"


def _render(letter_type: str, data: BaseModel, db: Session, admin: User) -> Response:
    """Build the Word file, archive it + log it in History, and return it for download."""
    values = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in data.model_dump().items()}
    if any(isinstance(v, str) and not v.strip() for k, v in values.items() if k not in OPTIONAL_FIELDS):
        raise HTTPException(status_code=422, detail="All fields are required")
    try:
        doc_bytes = build_letter_docx(letter_type, values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    label = LETTER_TYPES[letter_type]["label"]
    employee_name = values["employee_name"]
    filename = f"{label} - {_safe_name(employee_name)}.docx"

    # History is best effort: a storage/database problem must never lose the letter the admin asked for.
    try:
        letter_id = uuid.uuid4()
        now = datetime.utcnow()
        storage = key = None
        try:
            key = f"letters/{now:%Y/%m}/{letter_id}.docx"
            storage, key = letter_storage.save_file(key, doc_bytes)
        except letter_storage.LetterStorageError as exc:
            print(f"[LETTERS] Could not archive letter file: {exc}")
            storage = key = None
        db.add(GeneratedLetter(
            id=letter_id, letter_type=letter_type, letter_label=label, employee_name=employee_name,
            filename=filename, storage=storage, storage_key=key, form_values=values,
            generated_by=admin.id, generated_by_name=admin.name, created_at=now,
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[LETTERS] Could not record letter history: {exc}")

    return _file_response(doc_bytes, filename)


def _file_response(content: bytes, filename: str) -> Response:
    media_type = "application/pdf" if filename.lower().endswith(".pdf") else DOCX_MIME  # older history rows are PDFs
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/experience")
def generate_experience_letter(
    data: ExperienceLetterRequest, db: Session = Depends(get_db),
    admin: User = Depends(require_admin_or_superadmin),
):
    return _render("experience", data, db, admin)


@router.post("/relieving")
def generate_relieving_letter(
    data: RelievingLetterRequest, db: Session = Depends(get_db),
    admin: User = Depends(require_admin_or_superadmin),
):
    return _render("relieving", data, db, admin)


@router.post("/offer")
def generate_offer_letter(
    data: OfferLetterRequest, db: Session = Depends(get_db),
    admin: User = Depends(require_admin_or_superadmin),
):
    return _render("offer", data, db, admin)


@router.post("/bond")
def generate_bond(
    data: BondRequest, db: Session = Depends(get_db),
    admin: User = Depends(require_admin_or_superadmin),
):
    return _render("bond", data, db, admin)


# ── History ──────────────────────────────────────────────────────────────────

def _history_item(row: GeneratedLetter) -> dict:
    return {
        "id": str(row.id),
        "letter_type": row.letter_type,
        "letter_label": row.letter_label,
        "filename": row.filename,
        "employee_name": row.employee_name,
        "generated_by_name": row.generated_by_name,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "downloadable": bool(row.storage and row.storage_key),
    }


@router.get("/history")
def list_history(
    letter_type: Optional[str] = None,
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_superadmin),
):
    query = db.query(GeneratedLetter)
    if letter_type:
        query = query.filter(GeneratedLetter.letter_type == letter_type)
    if q and q.strip():
        query = query.filter(GeneratedLetter.employee_name.ilike(f"%{q.strip()}%"))
    total = query.count()
    rows = query.order_by(GeneratedLetter.created_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [_history_item(r) for r in rows]}


def _get_row(db: Session, letter_id: UUID) -> GeneratedLetter:
    row = db.query(GeneratedLetter).filter(GeneratedLetter.id == letter_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Letter not found")
    return row


@router.get("/history/{letter_id}/download")
def download_history_letter(
    letter_id: UUID, db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_superadmin),
):
    row = _get_row(db, letter_id)
    if not row.storage or not row.storage_key:
        raise HTTPException(status_code=404, detail="The file for this letter was not saved")
    try:
        content = letter_storage.load_file(row.storage, row.storage_key)
    except letter_storage.LetterStorageError as exc:
        print(f"[LETTERS] Could not load letter {letter_id}: {exc}")
        raise HTTPException(status_code=404, detail="The saved file could not be found")
    return _file_response(content, row.filename)


@router.delete("/history/{letter_id}")
def delete_history_letter(
    letter_id: UUID, db: Session = Depends(get_db),
    _: User = Depends(require_admin_or_superadmin),
):
    row = _get_row(db, letter_id)
    letter_storage.delete_file(row.storage, row.storage_key)
    db.delete(row)
    db.commit()
    return {"message": "Letter removed from history"}
