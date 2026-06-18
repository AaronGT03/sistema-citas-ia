from sqlalchemy import Column, ForeignKey, Integer, String, DateTime, Boolean
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
    canal = Column(String, default="LLAMADA")

    recordatorio_enviado = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    empresa_id = Column(Integer, ForeignKey("empresas.id"))
    servicio_id = Column(Integer, ForeignKey("servicios.id"), nullable=True)
