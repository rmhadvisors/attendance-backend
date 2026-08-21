import base64
import hashlib
import hmac
import json
import os
from datetime import datetime
from urllib import error, request


class AwsRekognitionError(Exception):
    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


def _get_config():
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    region = os.getenv("AWS_REGION", "ap-south-1").strip()
    session_token = os.getenv("AWS_SESSION_TOKEN", "").strip()

    if not access_key or not secret_key:
        raise AwsRekognitionError(
            "AWS Rekognition is not configured. Add AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY to backend/.env."
        )

    return access_key, secret_key, region, session_token


def _image_base64(image_data: str):
    if not image_data:
        raise AwsRekognitionError("Image data is required.")

    if image_data.startswith("data:"):
        _, encoded = image_data.split(",", 1)
        return encoded

    if image_data.startswith("http://") or image_data.startswith("https://"):
        try:
            req = request.Request(
                image_data,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with request.urlopen(req, timeout=15) as response:
                return base64.b64encode(response.read()).decode("utf-8")
        except Exception as exc:
            raise AwsRekognitionError(f"Failed to fetch image from URL: {exc}")

    try:
        with open(image_data, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    except OSError:
        return image_data


def _sign(key, message):
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signature_key(secret_key, date_stamp, region, service):
    date_key = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    region_key = _sign(date_key, region)
    service_key = _sign(region_key, service)
    return _sign(service_key, "aws4_request")


def _aws_json_request(target, payload):
    access_key, secret_key, region, session_token = _get_config()

    service = "rekognition"
    host = f"rekognition.{region}.amazonaws.com"
    endpoint = f"https://{host}/"

    now = datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_text = json.dumps(payload, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()

    headers = {
        "content-type": "application/x-amz-json-1.1",
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-target": f"RekognitionService.{target}"
    }

    if session_token:
        headers["x-amz-security-token"] = session_token

    signed_header_names = sorted(headers)
    canonical_headers = "".join(
        f"{name}:{headers[name]}\n"
        for name in signed_header_names
    )
    signed_headers = ";".join(signed_header_names)

    canonical_request = "\n".join([
        "POST",
        "/",
        "",
        canonical_headers,
        signed_headers,
        payload_hash
    ])

    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        algorithm,
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    ])

    signing_key = _signature_key(secret_key, date_stamp, region, service)
    signature = hmac.new(
        signing_key,
        string_to_sign.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    authorization_header = (
        f"{algorithm} "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    request_headers = {
        "Content-Type": headers["content-type"],
        "Host": headers["host"],
        "X-Amz-Date": headers["x-amz-date"],
        "X-Amz-Target": headers["x-amz-target"],
        "Authorization": authorization_header
    }

    if session_token:
        request_headers["X-Amz-Security-Token"] = session_token

    req = request.Request(
        endpoint,
        data=payload_text.encode("utf-8"),
        headers=request_headers,
        method="POST"
    )

    try:
        with request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8")
        try:
            error_payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            error_payload = {"raw": raw_body}

        message = (
            error_payload.get("message") or
            error_payload.get("Message") or
            raw_body or
            "AWS Rekognition request failed."
        )
        raise AwsRekognitionError(message, status_code=exc.code, payload=error_payload) from exc
    except error.URLError as exc:
        raise AwsRekognitionError(f"Could not reach AWS Rekognition: {exc.reason}") from exc


def compare_faces(source_image: str, target_image: str, threshold=None):
    if threshold is None:
        threshold = float(os.getenv("FACE_MATCH_THRESHOLD", 85))

    result = _aws_json_request(
        "CompareFaces",
        {
            "SourceImage": {"Bytes": _image_base64(source_image)},
            "TargetImage": {"Bytes": _image_base64(target_image)},
            "SimilarityThreshold": float(threshold)
        }
    )

    matches = result.get("FaceMatches", [])
    best_match = matches[0] if matches else None
    similarity = best_match["Similarity"] if best_match else 0

    return {
        "is_match": bool(best_match),
        "similarity": similarity,
        "threshold": float(threshold),
        "face_matches": matches,
        "unmatched_faces": result.get("UnmatchedFaces", []),
        "source_image_face": result.get("SourceImageFace")
    }
