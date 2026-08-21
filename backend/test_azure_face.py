import base64
import sys

from dotenv import load_dotenv

from app.services.azure_face_service import AzureFaceError, verify_faces


def encode_image(path):
    with open(path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def main():
    load_dotenv()

    if len(sys.argv) != 3:
        print("Usage: python test_azure_face.py enrolled_photo.jpg candidate_photo.jpg")
        return 2

    enrolled_photo = encode_image(sys.argv[1])
    candidate_photo = encode_image(sys.argv[2])

    try:
        result = verify_faces(enrolled_photo, candidate_photo)
    except AzureFaceError as exc:
        print("Azure Face test failed:")
        print(str(exc))
        if exc.payload:
            print(exc.payload)
        return 1

    print("Azure Face test completed:")
    print(f"Identical: {result['is_identical']}")
    print(f"Confidence: {result['confidence']}")
    print(f"Enrolled recognition model: {result['enrolled_face']['recognition_model']}")
    print(f"Candidate recognition model: {result['candidate_face']['recognition_model']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
