"""Storage for generated letter files (Word documents).

Uses S3 when AWS_S3_BUCKET (and credentials) are configured — the same settings the face photos use —
and otherwise falls back to a folder on disk so History also works on a developer machine.
Objects are private: they are only ever served through the authenticated backend, never by public URL.
"""
import os
from pathlib import Path
from typing import Optional, Tuple

from app.services.face_storage_service import _get_s3_client

BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOCAL_DIR = Path(os.getenv("LETTER_ARCHIVE_DIR", BACKEND_ROOT / "letter_archive")).resolve()


class LetterStorageError(Exception):
    pass


def save_file(key: str, data: bytes) -> Tuple[str, str]:
    """Store the file and return (storage, key). Raises LetterStorageError on failure."""
    s3_info = _get_s3_client()
    try:
        if s3_info:
            client, bucket, _ = s3_info
            client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=_content_type(key))
            return "s3", key
        path = _local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return "local", key
    except Exception as exc:
        raise LetterStorageError(str(exc)) from exc


def load_file(storage: str, key: str) -> bytes:
    try:
        if storage == "s3":
            s3_info = _get_s3_client()
            if not s3_info:
                raise LetterStorageError("S3 is not configured")
            client, bucket, _ = s3_info
            return client.get_object(Bucket=bucket, Key=key)["Body"].read()
        return _local_path(key).read_bytes()
    except LetterStorageError:
        raise
    except Exception as exc:
        raise LetterStorageError(str(exc)) from exc


def delete_file(storage: Optional[str], key: Optional[str]) -> None:
    """Best effort — a missing file must not block deleting the history row."""
    if not storage or not key:
        return
    try:
        if storage == "s3":
            s3_info = _get_s3_client()
            if s3_info:
                client, bucket, _ = s3_info
                client.delete_object(Bucket=bucket, Key=key)
        else:
            _local_path(key).unlink(missing_ok=True)
    except Exception as exc:
        print(f"[LETTER STORAGE] Could not delete {storage}:{key}: {exc}")


def _content_type(key: str) -> str:
    if key.endswith(".pdf"):
        return "application/pdf"
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _local_path(key: str) -> Path:
    path = (LOCAL_DIR / key).resolve()
    if LOCAL_DIR not in path.parents:
        raise LetterStorageError("Invalid storage key")
    return path
