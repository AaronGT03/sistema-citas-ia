from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Cita
from app.dependencies.auth import obtener_usuario_actual

router = APIRouter()


@router.get("/citas")
def listar_citas(
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    if usuario_actual["rol"] == "ADMIN":
        return db.query(Cita).all()

    return db.query(Cita).filter(Cita.empresa_id == usuario_actual["empresa_id"]).all()


@router.get("/citas/activa/{telefono}")
def obtener_cita_activa(
    telefono: str,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    cita = (
        db.query(Cita)
        .filter(Cita.telefono == telefono)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

    if not cita:
        return {"tiene_cita": False, "mensaje": "No hay citas activas"}

    return {"tiene_cita": True, "cita": cita}


@router.get("/citas/{cita_id}")
def obtener_cita(
    cita_id: int,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    cita = db.query(Cita).filter(Cita.id == cita_id).first()

    if not cita:
        return {"error": "Cita no encontrada"}

    return cita


@router.put("/citas/{cita_id}/cancelar")
def cancelar_cita(
    cita_id: int,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    cita = db.query(Cita).filter(Cita.id == cita_id).first()

    if not cita:
        return {"error": "Cita no encontrada"}

    if cita.status == "CANCELADA":
        return {"error": "La cita ya está cancelada"}

    cita.status = "CANCELADA"

    db.commit()
    db.refresh(cita)

    return {"mensaje": "Cita cancelada correctamente", "cita": cita}


@router.post("/citas/{cita_id}/reprogramar")
def reprogramar_cita(
    cita_id: int,
    nueva_fecha: str,
    nueva_hora: str,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    cita_anterior = db.query(Cita).filter(Cita.id == cita_id).first()

    if not cita_anterior:
        return {"error": "Cita no encontrada"}

    if cita_anterior.status == "CANCELADA":
        return {"error": "No se puede reprogramar una cita cancelada"}

    cita_anterior.status = "CANCELADA"

    nueva_cita = Cita(
        nombre=cita_anterior.nombre,
        telefono=cita_anterior.telefono,
        fecha=nueva_fecha,
        hora=nueva_hora,
        status="AGENDADA",
    )

    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)

    return {
        "mensaje": "Cita reprogramada correctamente",
        "cita_cancelada_id": cita_anterior.id,
        "nueva_cita": nueva_cita,
    }
