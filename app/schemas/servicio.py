from pydantic import BaseModel
from typing import Optional


class ServicioCreate(BaseModel):
    nombre: str
    descripcion: Optional[str] = None
    duracion_minutos: Optional[int] = None
    precio: Optional[int] = None
    empresa_id: int


class ServicioUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    duracion_minutos: Optional[int] = None
    precio: Optional[int] = None
    activo: Optional[bool] = None


class ServicioResponse(BaseModel):
    id: int
    nombre: str
    descripcion: Optional[str] = None
    duracion_minutos: Optional[int] = None
    precio: Optional[int] = None
    activo: bool
    empresa_id: int

    class Config:
        from_attributes = True