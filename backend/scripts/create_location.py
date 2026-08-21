import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.models.company import Location


def _required_float(name):
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"{name} is required.")
    return float(value)


def main():
    name = os.getenv("OFFICE_LOCATION_NAME", "Main Office")
    latitude = _required_float("OFFICE_LOCATION_LATITUDE")
    longitude = _required_float("OFFICE_LOCATION_LONGITUDE")
    radius_meters = int(os.getenv("OFFICE_LOCATION_RADIUS_METERS", "100"))

    db = SessionLocal()
    try:
        location = db.query(Location).filter(Location.name == name).first()
        if location:
            location.latitude = latitude
            location.longitude = longitude
            location.radius_meters = radius_meters
            location.is_active = True
            message = "Office location already existed; updated and activated."
        else:
            db.add(Location(
                name=name,
                latitude=latitude,
                longitude=longitude,
                radius_meters=radius_meters,
                is_active=True
            ))
            message = "Office location created."

        db.commit()
        print(message)
        print(f"{name}: {latitude}, {longitude} within {radius_meters}m")
    finally:
        db.close()


if __name__ == "__main__":
    main()
