from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from database import Base

class Cita(Base):
    __tablename__ = "citas"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    telefono = Column(String, nullable=False, index=True)
    fecha = Column(String, nullable=False)
    hora = Column(String, nullable=False)
    status = Column(String, nullable=False, default="AGENDADA")
    created_at = Column(DateTime, default=datetime.utcnow)


class Conversacion(Base):
    __tablename__ = "conversaciones"

    id = Column(Integer, primary_key=True, index=True)
    telefono = Column(String, unique=True, index=True)
    paso = Column(String, nullable=False)
    nombre = Column(String, nullable=True)
    fecha = Column(String, nullable=True)
    hora = Column(String, nullable=True)