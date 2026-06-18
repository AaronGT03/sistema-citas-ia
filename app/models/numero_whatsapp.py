from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from app.database import Base


class NumeroWhatsApp(Base):
    __tablename__ = "numeros_whatsapp"

    id = Column(Integer, primary_key=True, index=True)

    empresa_id = Column(Integer, ForeignKey("empresas.id"), nullable=False)

    telefono = Column(String, unique=True, nullable=False)
    phone_number_id = Column(String, nullable=False)
    token = Column(String, nullable=False)

    activo = Column(Boolean, default=True)