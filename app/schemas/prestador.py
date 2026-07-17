from pydantic import BaseModel
from typing import Optional


class PrestadorCreate(BaseModel):
    nombre: str
    empresa_id: int
    activo: bool = True
    telefono: Optional[str] = None
    correo: Optional[str] = None
    descripcion: Optional[str] = None
    hora_inicio: Optional[str] = None
    hora_fin: Optional[str] = None
    servicio_ids: list[int] = []


class PrestadorUpdate(BaseModel):
    nombre: Optional[str] = None
    activo: Optional[bool] = None
    telefono: Optional[str] = None
    correo: Optional[str] = None
    descripcion: Optional[str] = None
    hora_inicio: Optional[str] = None
    hora_fin: Optional[str] = None
    servicio_ids: Optional[list[int]] = None


class ServicioResumen(BaseModel):
    id: int
    nombre: str

    class Config:
        from_attributes = True


class PrestadorResponse(BaseModel):
    id: int
    nombre: str
    empresa_id: int
    activo: bool
    telefono: Optional[str] = None
    correo: Optional[str] = None
    descripcion: Optional[str] = None
    hora_inicio: Optional[str] = None
    hora_fin: Optional[str] = None
    servicios: list[ServicioResumen] = []

    class Config:
        from_attributes = True
