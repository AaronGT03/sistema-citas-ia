from sqlalchemy import Column, Integer, String
from app.database import Base

class Conversacion(Base):
    __tablename__ = "conversaciones"

    id = Column(Integer, primary_key=True, index=True)
    telefono = Column(String, unique=True)
    paso = Column(String)
    nombre = Column(String)
    fecha = Column(String)
    hora = Column(String)