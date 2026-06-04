from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Usuario
from app.utils.security import verificar_password, crear_token

router = APIRouter()


@router.post("/auth/login")
def login(email: str, password: str, db: Session = Depends(get_db)):
    usuario = db.query(Usuario).filter(Usuario.email == email).first()

    if not usuario:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    if not verificar_password(password, usuario.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = crear_token(
        {"usuario_id": usuario.id, "rol": usuario.rol, "empresa_id": usuario.empresa_id}
    )

    return {
        "mensaje": "Login correcto",
        "access_token": token,
        "token_type": "bearer",
        "usuario_id": usuario.id,
        "nombre": usuario.nombre,
        "rol": usuario.rol,
        "empresa_id": usuario.empresa_id,
    }
