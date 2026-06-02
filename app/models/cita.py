from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from app.database import Base

class Cita(Base):
    __tablename__ = "citas"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String)
    telefono = Column(String)
    fecha = Column(String)
    hora = Column(String)
    status = Column(String, default="AGENDADA")
    created_at = Column(DateTime, default=datetime.utcnow)