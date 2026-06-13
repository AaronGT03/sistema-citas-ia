from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Cita, Servicio, Servicio
from app.dependencies.auth import obtener_usuario_actual

router = APIRouter()


@router.post("/citas")
def crear_cita(
    nombre: str,
    telefono: str,
    fecha: str,
    hora: str,
    servicio_id: int,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    empresa_id = usuario_actual["empresa_id"]

    cita_existente = (
        db.query(Cita)
        .filter(Cita.empresa_id == empresa_id)
        .filter(Cita.fecha == fecha)
        .filter(Cita.hora == hora)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

    if cita_existente:
        raise HTTPException(
            status_code=400,
            detail="Ya existe una cita agendada en esa fecha y hora",
        )

    nueva_cita = Cita(
        nombre=nombre,
        telefono=telefono,
        fecha=fecha,
        hora=hora,
        status="AGENDADA",
        empresa_id=empresa_id,
        servicio_id=servicio_id,
    )

    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)

    return {
        "mensaje": "Cita creada correctamente",
        "cita": nueva_cita,
    }

@router.get("/citas")
def listar_citas(
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    query = db.query(Cita, Servicio).outerjoin(
        Servicio, Cita.servicio_id == Servicio.id
    )

    if usuario_actual["rol"] != "ADMIN":
        query = query.filter(Cita.empresa_id == usuario_actual["empresa_id"])

    resultados = query.all()

    citas = []

    for cita, servicio in resultados:
        citas.append(
            {
                "id": cita.id,
                "nombre": cita.nombre,
                "telefono": cita.telefono,
                "fecha": cita.fecha,
                "hora": cita.hora,
                "status": cita.status,
                "empresa_id": cita.empresa_id,
                "servicio_id": cita.servicio_id,
                "servicio_nombre": servicio.nombre if servicio else "Sin servicio",
            }
        )

    return citas


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
        empresa_id=cita_anterior.empresa_id,
        servicio_id=cita_anterior.servicio_id,
    )
    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)

    return {
        "mensaje": "Cita reprogramada correctamente",
        "cita_cancelada_id": cita_anterior.id,
        "nueva_cita": nueva_cita,
    }
