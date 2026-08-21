from pydantic import BaseModel

class EmployeeCreate(BaseModel):
    employee_code: str
    name: str
    department: str
    role: str


class EmployeeResponse(BaseModel):
    id: int
    employee_code: str
    name: str
    department: str
    role: str

    class Config:
        from_attributes = True