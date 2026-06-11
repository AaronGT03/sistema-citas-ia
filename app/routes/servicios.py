from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.servicio import Servicio
from app.schemas.servicio import ServicioCreate, ServicioUpdate, ServicioResponse


router = APIRouter(prefix="/servicios", tags=["Servicios"])


@router.post("/", response_model=ServicioResponse)
def crear_servicio(servicio: ServicioCreate, db: Session = Depends(get_db)):
    nuevo_servicio = Servicio(**servicio.dict())

    db.add(nuevo_servicio)
    db.commit()
    db.refresh(nuevo_servicio)

    return nuevo_servicio


@router.get("/empresa/{empresa_id}", response_model=list[ServicioResponse])
def listar_servicios_empresa(empresa_id: int, db: Session = Depends(get_db)):
    return db.query(Servicio).filter(Servicio.empresa_id == empresa_id).all()


@router.put("/{servicio_id}", response_model=ServicioResponse)
def actualizar_servicio(
    servicio_id: int,
    datos: ServicioUpdate,
    db: Session = Depends(get_db)
):
    servicio = db.query(Servicio).filter(Servicio.id == servicio_id).first()

    if not servicio:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")

    for campo, valor in datos.dict(exclude_unset=True).items():
        setattr(servicio, campo, valor)

    db.commit()
    db.refresh(servicio)

    return servicio


@router.delete("/{servicio_id}")
def eliminar_servicio(servicio_id: int, db: Session = Depends(get_db)):
    servicio = db.query(Servicio).filter(Servicio.id == servicio_id).first()

    if not servicio:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")

    db.delete(servicio)
    db.commit()

    return {"mensaje": "Servicio eliminado correctamente"}