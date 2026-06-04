from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    ForeignKey
)

from app.database import Base


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    nombre = Column(
        String,
        nullable=False
    )

    email = Column(
        String,
        unique=True,
        nullable=False
    )

    password_hash = Column(
        String,
        nullable=False
    )

    rol = Column(
        String,
        nullable=False
    )

    empresa_id = Column(
        Integer,
        ForeignKey("empresas.id"),
        nullable=True
    )

    activo = Column(
        Boolean,
        default=True
    )