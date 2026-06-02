from sqlalchemy import Column, Integer, String
from app.database import Base


class Empresa(Base):
    __tablename__ = "empresas"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    telefono_twilio = Column(String, unique=True)
    mensaje_bienvenida = Column(String)