from sqlalchemy import Column, Integer, String, Boolean
from app.database import Base


class Empresa(Base):
    __tablename__ = "empresas"

    id = Column(Integer, primary_key=True)
    nombre = Column(String, nullable=False)

    telefono_twilio = Column(
        String,
        unique=True,
        nullable=False
    )

    activa = Column(
        Boolean,
        default=True
    )

    horario_inicio = Column(
        String,
        default="09:00"
    )

    horario_fin = Column(
        String,
        default="18:00"
    )