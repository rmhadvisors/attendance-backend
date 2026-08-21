from sqlalchemy import Column, Integer, String, Date, Time, Float, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    employee_code = Column(String, unique=True, index=True)
    name = Column(String)
    department = Column(String)
    role = Column(String)
    face_image = Column(String, nullable=True)

    attendance = relationship("Attendance", back_populates="employee")


class Attendance(Base):
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"))
    date = Column(Date)
    in_time = Column(Time, nullable=True)
    out_time = Column(Time, nullable=True)
    total_hours = Column(Float, nullable=True)
    status = Column(String, default="Present")

    employee = relationship("Employee", back_populates="attendance")