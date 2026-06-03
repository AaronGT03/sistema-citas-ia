from sqlalchemy import Boolean, Column, Integer, String
from app.database import Base


class Empresa(Base):
    __tablename__ = "empresas"

    id = Column(Integer, primary_key=True)
    nombre = Column(String)
    telefono_twilio = Column(String, unique=True)
    activa = Column(Boolean, default=True)