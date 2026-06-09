from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Empresa, Cita
from app.dependencies.auth import obtener_usuario_actual


router = APIRouter()


def validar_admin(usuario_actual: dict):
    if usuario_actual["rol"] != "ADMIN":
        raise HTTPException(
            status_code=403, detail="No tienes permisos para realizar esta acción"
        )


def validar_acceso_empresa(empresa_id: int, usuario_actual: dict):
    if usuario_actual["rol"] == "ADMIN":
        return

    if usuario_actual["empresa_id"] != empresa_id:
        raise HTTPException(
            status_code=403, detail="No tienes permisos para acceder a esta empresa"
        )


@router.get("/empresas")
def listar_empresas(
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    validar_admin(usuario_actual)

    empresas = db.query(Empresa).all()

    return empresas


@router.post("/empresas")
def crear_empresa(
    nombre: str,
    telefono_twilio: str,
    horario_inicio: str = "09:00",
    horario_fin: str = "18:00",
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    validar_admin(usuario_actual)

    empresa_existente = (
        db.query(Empresa).filter(Empresa.telefono_twilio == telefono_twilio).first()
    )

    if empresa_existente:
        raise HTTPException(
            status_code=400, detail="Ya existe una empresa con ese número de Twilio"
        )

    empresa = Empresa(
        nombre=nombre,
        telefono_twilio=telefono_twilio,
        horario_inicio=horario_inicio,
        horario_fin=horario_fin,
    )

    db.add(empresa)
    db.commit()
    db.refresh(empresa)

    return empresa


@router.get("/empresas/{empresa_id}/citas")
def obtener_citas_empresa(
    empresa_id: int,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    validar_acceso_empresa(empresa_id, usuario_actual)

    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

    if not empresa:
        return {"error": "Empresa no encontrada"}

    citas = db.query(Cita).filter(Cita.empresa_id == empresa_id).all()

    return {"empresa": empresa.nombre, "total_citas": len(citas), "citas": citas}


@router.get("/empresas/{empresa_id}/resumen")
def obtener_resumen_empresa(
    empresa_id: int,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    validar_acceso_empresa(empresa_id, usuario_actual)

    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

    if not empresa:
        return {"error": "Empresa no encontrada"}

    total_citas = db.query(Cita).filter(Cita.empresa_id == empresa_id).count()

    citas_activas = (
        db.query(Cita)
        .filter(Cita.empresa_id == empresa_id)
        .filter(Cita.status == "AGENDADA")
        .count()
    )

    citas_canceladas = (
        db.query(Cita)
        .filter(Cita.empresa_id == empresa_id)
        .filter(Cita.status == "CANCELADA")
        .count()
    )

    return {
        "empresa": empresa.nombre,
        "empresa_id": empresa.id,
        "total_citas": total_citas,
        "citas_activas": citas_activas,
        "citas_canceladas": citas_canceladas,
    }
