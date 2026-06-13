from sqlalchemy import Column, ForeignKey, Integer, String, DateTime
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
    empresa_id = Column(Integer, ForeignKey("empresas.id"))
    servicio_id = Column(Integer, ForeignKey("servicios.id"), nullable=True)
