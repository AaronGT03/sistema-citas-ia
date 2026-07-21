from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Empresa, Cita
from app.dependencies import obtener_usuario_actual


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
    usa_prestadores: bool = False,
    permite_citas_sin_hora: bool = False,
    giro: str | None = None,
    prompt_base: str | None = None,
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
        usa_prestadores=usa_prestadores,
        permite_citas_sin_hora=permite_citas_sin_hora,
        giro=giro,
        prompt_base=prompt_base,
    )

    db.add(empresa)
    db.commit()
    db.refresh(empresa)

    return empresa


@router.get("/empresas/{empresa_id}")
def obtener_empresa(
    empresa_id: int,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    validar_acceso_empresa(empresa_id, usuario_actual)

    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

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

    citas_pendientes_hora = (
        db.query(Cita)
        .filter(Cita.empresa_id == empresa_id)
        .filter(Cita.status == "PENDIENTE_HORA")
        .count()
    )

    return {
        "empresa": empresa.nombre,
        "empresa_id": empresa.id,
        "total_citas": total_citas,
        "citas_activas": citas_activas,
        "citas_canceladas": citas_canceladas,
        "citas_pendientes_hora": citas_pendientes_hora,
    }


@router.put("/empresas/{empresa_id}")
def editar_empresa(
    empresa_id: int,
    nombre: str,
    telefono_twilio: str,
    horario_inicio: str = "09:00",
    horario_fin: str = "18:00",
    activa: bool = True,
    usa_prestadores: bool | None = None,
    permite_citas_sin_hora: bool | None = None,
    giro: str | None = None,
    prompt_base: str | None = None,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    validar_admin(usuario_actual)

    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    existe_numero = (
        db.query(Empresa)
        .filter(Empresa.telefono_twilio == telefono_twilio)
        .filter(Empresa.id != empresa_id)
        .first()
    )

    if existe_numero:
        raise HTTPException(
            status_code=400, detail="Ya existe una empresa con ese número de Twilio"
        )

    empresa.nombre = nombre
    empresa.telefono_twilio = telefono_twilio
    empresa.horario_inicio = horario_inicio
    empresa.horario_fin = horario_fin
    empresa.activa = activa

    # usa_prestadores/permite_citas_sin_hora/giro/prompt_base son opcionales:
    # si no se envían (ej. desde el formulario actual del dashboard, que no
    # los conoce), se conserva el valor que ya tenía la empresa en vez de
    # resetearlo a su default.
    if usa_prestadores is not None:
        empresa.usa_prestadores = usa_prestadores

    if permite_citas_sin_hora is not None:
        empresa.permite_citas_sin_hora = permite_citas_sin_hora

    if giro is not None:
        empresa.giro = giro

    if prompt_base is not None:
        empresa.prompt_base = prompt_base

    db.commit()
    db.refresh(empresa)

    return empresa
@router.delete("/empresas/{empresa_id}")
def eliminar_empresa(
    empresa_id: int,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual)
):
    validar_admin(usuario_actual)

    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    db.delete(empresa)
    db.commit()

    return {"mensaje": "Empresa eliminada correctamente"}