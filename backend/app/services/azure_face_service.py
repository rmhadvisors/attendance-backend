import base64
import json
import os
from urllib import error, parse, request


class AzureFaceError(Exception):
    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


def _get_config():
    endpoint = os.getenv("AZURE_FACE_ENDPOINT", "").strip().rstrip("/")
    key = os.getenv("AZURE_FACE_KEY", "").strip()

    if not endpoint or not key:
        raise AzureFaceError(
            "Azure Face is not configured. Add AZURE_FACE_ENDPOINT and AZURE_FACE_KEY to backend/.env."
        )

    return endpoint, key


def _image_bytes(image_data: str):
    if not image_data:
        raise AzureFaceError("Image data is required.")

    if image_data.startswith("data:"):
        _, encoded = image_data.split(",", 1)
    else:
        encoded = image_data

    try:
        return base64.b64decode(encoded)
    except Exception as exc:
        raise AzureFaceError("Invalid base64 image data.") from exc


def _read_json_response(response):
    body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def _azure_request(url, key, body, content_type):
    req = request.Request(
        url,
        data=body,
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": content_type
        },
        method="POST"
    )

    try:
        with request.urlopen(req, timeout=30) as response:
            return _read_json_response(response)
    except error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            payload = {"raw": raw_body}

        azure_error = payload.get("error", {})
        message = azure_error.get("message") or raw_body or "Azure Face API request failed."
        raise AzureFaceError(message, status_code=exc.code, payload=payload) from exc
    except error.URLError as exc:
        raise AzureFaceError(f"Could not reach Azure Face API: {exc.reason}") from exc


def detect_face(image_data: str):
    endpoint, key = _get_config()
    query = parse.urlencode({
        "returnFaceId": "true",
        "returnRecognitionModel": "true",
        "recognitionModel": "recognition_04",
        "detectionModel": "detection_03",
        "faceIdTimeToLive": 300
    })
    url = f"{endpoint}/face/v1.0/detect?{query}"
    faces = _azure_request(url, key, _image_bytes(image_data), "application/octet-stream")

    if not faces:
        raise AzureFaceError("No face detected in image.")

    if len(faces) > 1:
        raise AzureFaceError("Multiple faces detected. Use an image with one clear face.")

    face_id = faces[0].get("faceId")
    if not face_id:
        raise AzureFaceError("Azure did not return a faceId. Face verification may not be enabled for this resource.")

    return faces[0]


def verify_faces(enrolled_image: str, candidate_image: str):
    endpoint, key = _get_config()
    enrolled_face = detect_face(enrolled_image)
    candidate_face = detect_face(candidate_image)

    url = f"{endpoint}/face/v1.0/verify"
    body = json.dumps({
        "faceId1": enrolled_face["faceId"],
        "faceId2": candidate_face["faceId"]
    }).encode("utf-8")

    result = _azure_request(url, key, body, "application/json")

    return {
        "is_identical": result.get("isIdentical", False),
        "confidence": result.get("confidence", 0),
        "enrolled_face": {
            "face_id": enrolled_face["faceId"],
            "recognition_model": enrolled_face.get("recognitionModel")
        },
        "candidate_face": {
            "face_id": candidate_face["faceId"],
            "recognition_model": candidate_face.get("recognitionModel")
        }
    }
