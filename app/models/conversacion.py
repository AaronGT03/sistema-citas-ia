from sqlalchemy import Column, Integer, String,ForeignKey
from app.database import Base


class Conversacion(Base):
    __tablename__ = "conversaciones"

    id = Column(Integer, primary_key=True, index=True)
    telefono = Column(String, unique=True, index=True)
    empresa_id = Column(Integer)
    paso = Column(String, nullable=False)
    nombre = Column(String, nullable=True)
    fecha = Column(String, nullable=True)
    hora = Column(String, nullable=True)
    servicio_id = Column(Integer, ForeignKey("servicios.id"), nullable=True)