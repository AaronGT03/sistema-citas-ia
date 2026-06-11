from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from app.database import Base


class Servicio(Base):
    __tablename__ = "servicios"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    descripcion = Column(String, nullable=True)
    duracion_minutos = Column(Integer, nullable=True)
    precio = Column(Integer, nullable=True)
    activo = Column(Boolean, default=True)

    empresa_id = Column(Integer, ForeignKey("empresas.id"), nullable=False)