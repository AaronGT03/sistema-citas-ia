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
