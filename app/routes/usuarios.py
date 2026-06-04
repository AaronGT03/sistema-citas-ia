from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Usuario
from app.utils.security import generar_hash
from app.dependencies.auth import obtener_usuario_actual

router = APIRouter()


def validar_admin(usuario_actual: dict):
    if usuario_actual["rol"] != "ADMIN":
        raise HTTPException(
            status_code=403,
            detail="No tienes permisos"
        )


@router.post("/usuarios")
def crear_usuario(
    nombre: str,
    email: str,
    password: str,
    rol: str,
    empresa_id: int | None = None,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual)
):
    validar_admin(usuario_actual)

    existe = (
        db.query(Usuario)
        .filter(Usuario.email == email)
        .first()
    )

    if existe:
        raise HTTPException(
            status_code=400,
            detail="El correo ya existe"
        )

    usuario = Usuario(
        nombre=nombre,
        email=email,
        password_hash=generar_hash(password),
        rol=rol,
        empresa_id=empresa_id
    )

    db.add(usuario)
    db.commit()
    db.refresh(usuario)

    return {
        "mensaje": "Usuario creado correctamente",
        "usuario_id": usuario.id
    }


@router.get("/usuarios")
def listar_usuarios(
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual)
):
    validar_admin(usuario_actual)

    return db.query(Usuario).all()