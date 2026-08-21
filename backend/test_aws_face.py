from dotenv import load_dotenv
import sys

from app.services.aws_rekognition_service import AwsRekognitionError, compare_faces


def main():
    load_dotenv()

    if len(sys.argv) != 3:
        print("Usage: python test_aws_face.py enrolled_photo.jpg candidate_photo.jpg")
        return 2

    try:
        result = compare_faces(sys.argv[1], sys.argv[2])
    except AwsRekognitionError as exc:
        print("AWS Rekognition test failed:")
        print(str(exc))
        if exc.payload:
            print(exc.payload)
        return 1

    print("AWS Rekognition test completed:")
    print(f"Match: {result['is_match']}")
    print(f"Similarity: {result['similarity']}")
    print(f"Threshold: {result['threshold']}")
    print(f"Matches returned: {len(result['face_matches'])}")
    print(f"Unmatched faces: {len(result['unmatched_faces'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
