from pydantic import BaseModel
from uuid import UUID


class LocationCreate(BaseModel):
    name: str
    latitude: float
    longitude: float
    radius_meters: int = 100


class LocationResponse(BaseModel):
    id: UUID
    name: str
    latitude: float
    longitude: float
    radius_meters: int
    is_active: bool

    class Config:
        from_attributes = True
