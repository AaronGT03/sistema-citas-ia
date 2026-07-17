from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Prestador, Empresa, Servicio, Cita
from app.schemas.prestador import PrestadorCreate, PrestadorUpdate, PrestadorResponse
from app.dependencies import obtener_usuario_actual


router = APIRouter(prefix="/prestadores", tags=["Prestadores"])


def validar_acceso_empresa(empresa_id: int, usuario_actual: dict):
    if usuario_actual["rol"] == "ADMIN":
        return

    if usuario_actual["empresa_id"] != empresa_id:
        raise HTTPException(
            status_code=403,
            detail="No tienes permisos para acceder a esta empresa",
        )


def obtener_prestador_o_404(db: Session, prestador_id: int) -> Prestador:
    prestador = db.query(Prestador).filter(Prestador.id == prestador_id).first()

    if not prestador:
        raise HTTPException(status_code=404, detail="Prestador no encontrado")

    return prestador


def asignar_servicios(db: Session, prestador: Prestador, servicio_ids: list[int]):
    if not servicio_ids:
        prestador.servicios = []
        return

    servicios = (
        db.query(Servicio)
        .filter(
            Servicio.id.in_(servicio_ids),
            Servicio.empresa_id == prestador.empresa_id,
        )
        .all()
    )

    if len(servicios) != len(set(servicio_ids)):
        raise HTTPException(
            status_code=400,
            detail="Uno o más servicios no pertenecen a la empresa del prestador",
        )

    prestador.servicios = servicios


@router.post("/", response_model=PrestadorResponse)
def crear_prestador(
    datos: PrestadorCreate,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    validar_acceso_empresa(datos.empresa_id, usuario_actual)

    empresa = db.query(Empresa).filter(Empresa.id == datos.empresa_id).first()

    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    nuevo_prestador = Prestador(
        nombre=datos.nombre,
        empresa_id=datos.empresa_id,
        activo=datos.activo,
        telefono=datos.telefono,
        correo=datos.correo,
        descripcion=datos.descripcion,
        hora_inicio=datos.hora_inicio,
        hora_fin=datos.hora_fin,
    )

    db.add(nuevo_prestador)
    db.flush()

    asignar_servicios(db, nuevo_prestador, datos.servicio_ids)

    db.commit()
    db.refresh(nuevo_prestador)

    return nuevo_prestador


@router.get("/empresa/{empresa_id}", response_model=list[PrestadorResponse])
def listar_prestadores_empresa(
    empresa_id: int,
    activo: bool | None = None,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    validar_acceso_empresa(empresa_id, usuario_actual)

    query = db.query(Prestador).filter(Prestador.empresa_id == empresa_id)

    if activo is not None:
        query = query.filter(Prestador.activo == activo)

    return query.all()


@router.get("/{prestador_id}", response_model=PrestadorResponse)
def obtener_prestador(
    prestador_id: int,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    prestador = obtener_prestador_o_404(db, prestador_id)

    validar_acceso_empresa(prestador.empresa_id, usuario_actual)

    return prestador


@router.put("/{prestador_id}", response_model=PrestadorResponse)
def actualizar_prestador(
    prestador_id: int,
    datos: PrestadorUpdate,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    prestador = obtener_prestador_o_404(db, prestador_id)

    validar_acceso_empresa(prestador.empresa_id, usuario_actual)

    datos_dict = datos.dict(exclude_unset=True)
    servicio_ids = datos_dict.pop("servicio_ids", None)

    for campo, valor in datos_dict.items():
        setattr(prestador, campo, valor)

    if servicio_ids is not None:
        asignar_servicios(db, prestador, servicio_ids)

    db.commit()
    db.refresh(prestador)

    return prestador


@router.get("/{prestador_id}/citas")
def listar_citas_prestador(
    prestador_id: int,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    prestador = obtener_prestador_o_404(db, prestador_id)

    validar_acceso_empresa(prestador.empresa_id, usuario_actual)

    resultados = (
        db.query(Cita, Servicio)
        .outerjoin(Servicio, Cita.servicio_id == Servicio.id)
        .filter(Cita.prestador_id == prestador_id)
        .all()
    )

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
                "canal": cita.canal,
                "servicio_id": cita.servicio_id,
                "servicio_nombre": servicio.nombre if servicio else "Sin servicio",
                "duracion_minutos": servicio.duracion_minutos if servicio else None,
            }
        )

    return citas


@router.get("/{prestador_id}/disponibilidad")
def obtener_disponibilidad_prestador(
    prestador_id: int,
    fecha: str = Query(..., description="Fecha en formato DD/MM/YYYY"),
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    prestador = obtener_prestador_o_404(db, prestador_id)

    validar_acceso_empresa(prestador.empresa_id, usuario_actual)

    resultados = (
        db.query(Cita, Servicio)
        .outerjoin(Servicio, Cita.servicio_id == Servicio.id)
        .filter(
            Cita.prestador_id == prestador_id,
            Cita.fecha == fecha,
            Cita.status == "AGENDADA",
        )
        .all()
    )

    ocupado = []

    for cita, servicio in resultados:
        ocupado.append(
            {
                "cita_id": cita.id,
                "cliente": cita.nombre,
                "hora": cita.hora,
                "duracion_minutos": servicio.duracion_minutos if servicio else 60,
                "servicio_nombre": servicio.nombre if servicio else "Sin servicio",
            }
        )

    return {"prestador_id": prestador_id, "fecha": fecha, "ocupado": ocupado}
