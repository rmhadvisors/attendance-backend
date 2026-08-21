import base64
import os
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
FACE_DATA_DIR = Path(os.getenv("FACE_DATA_DIR", BACKEND_ROOT / "face_data")).resolve()

# Attempt to import boto3 for optional S3 support
try:
    import boto3
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


class FaceStorageError(Exception):
    pass


def _decode_image(image_data: str):
    if not image_data:
        raise FaceStorageError("Face photo is required.")

    if image_data.startswith("data:"):
        _, encoded = image_data.split(",", 1)
    else:
        encoded = image_data

    try:
        return base64.b64decode(encoded)
    except Exception as exc:
        raise FaceStorageError("Invalid face photo data.") from exc


def _get_s3_client():
    if not BOTO3_AVAILABLE:
        return None
    bucket_name = os.getenv("AWS_S3_BUCKET", "").strip()
    if not bucket_name:
        return None

    access_key = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    region = os.getenv("AWS_REGION", "ap-south-1").strip()
    session_token = os.getenv("AWS_SESSION_TOKEN", "").strip()

    try:
        session_args = {}
        if access_key and secret_key:
            session_args["aws_access_key_id"] = access_key
            session_args["aws_secret_access_key"] = secret_key
            if session_token:
                session_args["aws_session_token"] = session_token
        if region:
            session_args["region_name"] = region

        s3 = boto3.client("s3", **session_args)
        return s3, bucket_name, region
    except Exception as exc:
        print(f"[S3 STORAGE] Failed to initialize S3 client: {exc}")
        return None


def _upload_to_s3(image_bytes: bytes, s3_key: str) -> str:
    s3_info = _get_s3_client()
    if not s3_info:
        raise FaceStorageError("S3 client is not configured but S3 upload was requested.")
    s3_client, bucket_name, region = s3_info
    try:
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=image_bytes,
            ContentType="image/jpeg"
        )
        url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{s3_key}"
        return url
    except Exception as exc:
        raise FaceStorageError(f"Failed to upload image to S3: {exc}")


def save_enrolled_face(image_data: str, user_id):
    """
    If S3 is configured, uploads the reference face as a JPEG to S3.
    Otherwise, falls back to storing the Base64 string directly in the database.
    """
    if not image_data:
        raise FaceStorageError("Face photo is required.")

    s3_info = _get_s3_client()
    if s3_info:
        image_bytes = _decode_image(image_data)
        s3_key = f"enrolled_faces/{user_id}.jpg"
        url = _upload_to_s3(image_bytes, s3_key)
        return url

    return image_data


def save_attendance_face(image_data: str, user_id, action_type: str):
    """
    If S3 is configured, uploads the attendance selfie to S3.
    Otherwise, saves it locally on the server disk.
    """
    image_bytes = _decode_image(image_data)

    s3_info = _get_s3_client()
    if s3_info:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        s3_key = f"attendance_logs/{user_id}/{timestamp}_{action_type}.jpg"
        url = _upload_to_s3(image_bytes, s3_key)
        return url

    directory = FACE_DATA_DIR / "attendance" / str(user_id)
    directory.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    path = directory / f"{timestamp}_{action_type}.jpg"
    path.write_bytes(image_bytes)

    return str(path)


def get_face_path(relative_path: str):
    if not relative_path:
        raise FaceStorageError("No enrolled face image found.")

    # If it's a base64 string or an HTTP/HTTPS URL, return it directly
    if relative_path.startswith("data:") or relative_path.startswith("http://") or relative_path.startswith("https://"):
        return relative_path

    path = Path(relative_path)
    if not path.is_absolute():
        path = BACKEND_ROOT / relative_path

    if not path.exists():
        raise FaceStorageError("Enrolled face image file is missing. Please enroll again.")

    return str(path)
