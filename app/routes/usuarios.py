from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Usuario, Empresa
from app.utils.security import generar_hash
from app.dependencies.auth import obtener_usuario_actual

router = APIRouter()


def validar_admin(usuario_actual: dict):
    if usuario_actual["rol"] != "ADMIN":
        raise HTTPException(status_code=403, detail="No tienes permisos")


@router.post("/usuarios")
def crear_usuario(
    nombre: str,
    email: str,
    password: str,
    rol: str,
    empresa_id: int | None = None,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    validar_admin(usuario_actual)

    existe = db.query(Usuario).filter(Usuario.email == email).first()

    if existe:
        raise HTTPException(status_code=400, detail="El correo ya existe")

    usuario = Usuario(
        nombre=nombre,
        email=email,
        password_hash=generar_hash(password),
        rol=rol,
        empresa_id=empresa_id,
    )

    db.add(usuario)
    db.commit()
    db.refresh(usuario)

    return {"mensaje": "Usuario creado correctamente", "usuario_id": usuario.id}


@router.get("/usuarios")
def listar_usuarios(
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    validar_admin(usuario_actual)

    usuarios = db.query(Usuario).all()

    resultado = []

    for usuario in usuarios:
        empresa_nombre = None

        if usuario.empresa_id:
            empresa = db.query(Empresa).filter(Empresa.id == usuario.empresa_id).first()

            if empresa:
                empresa_nombre = empresa.nombre

        resultado.append(
            {
                "id": usuario.id,
                "nombre": usuario.nombre,
                "email": usuario.email,
                "rol": usuario.rol,
                "empresa_id": usuario.empresa_id,
                "empresa_nombre": empresa_nombre,
                "activo": usuario.activo,
            }
        )

    return resultado


@router.delete("/usuarios/{usuario_id}")
def eliminar_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    validar_admin(usuario_actual)

    usuario = db.query(Usuario).filter(Usuario.id == usuario_id).first()

    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if usuario.id == usuario_actual["usuario_id"]:
        raise HTTPException(
            status_code=400, detail="No puedes eliminar tu propio usuario"
        )

    db.delete(usuario)
    db.commit()

    return {"mensaje": "Usuario eliminado correctamente"}
@router.put("/usuarios/{usuario_id}")
def editar_usuario(
    usuario_id: int,
    nombre: str,
    email: str,
    empresa_id: int | None = None,
    password: str | None = None,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual)
):
    validar_admin(usuario_actual)

    usuario = (
        db.query(Usuario)
        .filter(Usuario.id == usuario_id)
        .first()
    )

    if not usuario:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado"
        )

    existe_email = (
        db.query(Usuario)
        .filter(Usuario.email == email)
        .filter(Usuario.id != usuario_id)
        .first()
    )

    if existe_email:
        raise HTTPException(
            status_code=400,
            detail="El correo ya está en uso"
        )

    usuario.nombre = nombre
    usuario.email = email
    usuario.empresa_id = empresa_id

    if password:
        usuario.password_hash = generar_hash(password)

    db.commit()
    db.refresh(usuario)

    return {
        "mensaje": "Usuario actualizado correctamente",
        "usuario_id": usuario.id
    }