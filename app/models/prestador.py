from sqlalchemy import Column, ForeignKey, Integer, String, Boolean, Table
from sqlalchemy.orm import relationship
from app.database import Base


prestadores_servicios = Table(
    "prestadores_servicios",
    Base.metadata,
    Column(
        "prestador_id",
        Integer,
        ForeignKey("prestadores.id"),
        primary_key=True,
    ),
    Column(
        "servicio_id",
        Integer,
        ForeignKey("servicios.id"),
        primary_key=True,
    ),
)


class Prestador(Base):
    __tablename__ = "prestadores"

    id = Column(Integer, primary_key=True, index=True)

    nombre = Column(String, nullable=False)
    empresa_id = Column(
        Integer,
        ForeignKey("empresas.id"),
        nullable=False,
        index=True,
    )
    activo = Column(Boolean, default=True, nullable=False)

    telefono = Column(String, nullable=True)
    correo = Column(String, nullable=True)
    descripcion = Column(String, nullable=True)
    hora_inicio = Column(String, nullable=True)
    hora_fin = Column(String, nullable=True)

    servicios = relationship(
        "Servicio",
        secondary=prestadores_servicios,
        backref="prestadores",
    )
