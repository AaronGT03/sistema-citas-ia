from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Empresa, Cita


router = APIRouter()


@router.post("/empresas")
def crear_empresa(nombre: str, telefono_twilio: str, db: Session = Depends(get_db)):
    empresa = Empresa(nombre=nombre, telefono_twilio=telefono_twilio)

    db.add(empresa)
    db.commit()
    db.refresh(empresa)

    return empresa


@router.get("/empresas")
def listar_empresas(db: Session = Depends(get_db)):
    return db.query(Empresa).all()


@router.get("/empresas/{empresa_id}/citas")
def obtener_citas_empresa(empresa_id: int, db: Session = Depends(get_db)):
    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

    if not empresa:
        return {"error": "Empresa no encontrada"}

    citas = db.query(Cita).filter(Cita.empresa_id == empresa_id).all()

    return {"empresa": empresa.nombre, "total_citas": len(citas), "citas": citas}


@router.get("/empresas/{empresa_id}/resumen")
def obtener_resumen_empresa(empresa_id: int, db: Session = Depends(get_db)):
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
