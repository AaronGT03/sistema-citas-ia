from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Cita, Servicio, Empresa, Prestador
from app.dependencies import obtener_usuario_actual
from app.services.citas_service import (
    horario_choca_con_duracion,
    seleccionar_prestador_automaticamente,
    crear_solicitud_sin_hora,
    reprogramar_cita as reprogramar_cita_service,
)

router = APIRouter()


@router.post("/citas")
def crear_cita(
    nombre: str,
    telefono: str,
    fecha: str,
    servicio_id: int,
    hora: str | None = None,
    sin_hora_especifica: bool = False,
    prestador_id: int | None = None,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    empresa_id = usuario_actual["empresa_id"]

    if empresa_id is None:
        raise HTTPException(
            status_code=403,
            detail="El usuario ADMIN no puede crear citas directamente. Usa un usuario EMPRESA.",
        )

    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    servicio = (
        db.query(Servicio)
        .filter(Servicio.id == servicio_id, Servicio.empresa_id == empresa_id)
        .first()
    )

    if not servicio:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")

    if sin_hora_especifica:
        try:
            nueva_cita_sin_hora = crear_solicitud_sin_hora(
                db=db,
                empresa=empresa,
                servicio=servicio,
                fecha=fecha,
                nombre=nombre,
                telefono=telefono,
                canal="DASHBOARD",
                prestador_id=prestador_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

        return {
            "mensaje": "Solicitud registrada sin hora específica",
            "cita": nueva_cita_sin_hora,
        }

    if hora is None:
        raise HTTPException(
            status_code=400,
            detail="Debe indicar una hora o marcar sin_hora_especifica",
        )

    prestador_final = None

    if empresa.usa_prestadores:
        if prestador_id:
            prestador = (
                db.query(Prestador)
                .filter(
                    Prestador.id == prestador_id,
                    Prestador.empresa_id == empresa_id,
                )
                .first()
            )

            if not prestador or not prestador.activo:
                raise HTTPException(
                    status_code=404,
                    detail="Prestador no encontrado o inactivo",
                )

            if servicio not in prestador.servicios:
                raise HTTPException(
                    status_code=400,
                    detail="El prestador seleccionado no realiza ese servicio",
                )

            ocupado = horario_choca_con_duracion(
                db=db,
                empresa_id=empresa_id,
                fecha=fecha,
                hora=hora,
                servicio_id=servicio_id,
                prestador_id=prestador_id,
            )

            if ocupado:
                raise HTTPException(
                    status_code=400,
                    detail="Ese prestador ya tiene una cita en ese horario",
                )

            prestador_final = prestador_id
        else:
            prestador_seleccionado = seleccionar_prestador_automaticamente(
                db=db,
                empresa_id=empresa_id,
                servicio_id=servicio_id,
                fecha=fecha,
                hora=hora,
            )

            if not prestador_seleccionado:
                raise HTTPException(
                    status_code=400,
                    detail="No hay prestadores disponibles en ese horario",
                )

            prestador_final = prestador_seleccionado.id
    else:
        ocupado = horario_choca_con_duracion(
            db=db,
            empresa_id=empresa_id,
            fecha=fecha,
            hora=hora,
            servicio_id=servicio_id,
        )

        if ocupado:
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
        prestador_id=prestador_final,
        canal="DASHBOARD",
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
    prestador_id: int | None = None,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    query = (
        db.query(Cita, Servicio, Prestador)
        .outerjoin(Servicio, Cita.servicio_id == Servicio.id)
        .outerjoin(Prestador, Cita.prestador_id == Prestador.id)
    )

    if usuario_actual["rol"] != "ADMIN":
        query = query.filter(Cita.empresa_id == usuario_actual["empresa_id"])

    if prestador_id is not None:
        query = query.filter(Cita.prestador_id == prestador_id)

    resultados = query.all()

    citas = []

    for cita, servicio, prestador in resultados:
        citas.append(
            {
                "id": cita.id,
                "nombre": cita.nombre,
                "telefono": cita.telefono,
                "fecha": cita.fecha,
                "hora": cita.hora,
                "sin_hora_especifica": cita.sin_hora_especifica,
                "status": cita.status,
                "canal": cita.canal,
                "empresa_id": cita.empresa_id,
                "servicio_id": cita.servicio_id,
                "servicio_nombre": servicio.nombre if servicio else "Sin servicio",
                "duracion_minutos": servicio.duracion_minutos if servicio else None,
                "prestador_id": cita.prestador_id,
                "prestador_nombre": prestador.nombre if prestador else None,
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
    nueva_hora: str | None = None,
    sin_hora_especifica: bool = False,
    nuevo_prestador_id: int | None = None,
    db: Session = Depends(get_db),
    usuario_actual: dict = Depends(obtener_usuario_actual),
):
    cita_anterior = db.query(Cita).filter(Cita.id == cita_id).first()

    if not cita_anterior:
        return {"error": "Cita no encontrada"}

    if cita_anterior.status == "CANCELADA":
        return {"error": "No se puede reprogramar una cita cancelada"}

    prestador_id_final = (
        nuevo_prestador_id
        if nuevo_prestador_id is not None
        else cita_anterior.prestador_id
    )

    if nuevo_prestador_id is not None:
        prestador = (
            db.query(Prestador)
            .filter(
                Prestador.id == nuevo_prestador_id,
                Prestador.empresa_id == cita_anterior.empresa_id,
            )
            .first()
        )

        if not prestador or not prestador.activo:
            return {"error": "Prestador no encontrado o inactivo"}

    if sin_hora_especifica:
        empresa = (
            db.query(Empresa).filter(Empresa.id == cita_anterior.empresa_id).first()
        )

        if not empresa or not empresa.permite_citas_sin_hora:
            return {"error": "Esta empresa no permite citas sin hora específica"}

        nueva_cita_sin_hora = reprogramar_cita_service(
            db=db,
            cita_anterior=cita_anterior,
            nueva_fecha=nueva_fecha,
            nueva_hora=None,
            canal=cita_anterior.canal,
            prestador_id=prestador_id_final,
            sin_hora_especifica=True,
        )

        return {
            "mensaje": "Cita reprogramada sin hora específica",
            "cita_cancelada_id": cita_anterior.id,
            "nueva_cita": nueva_cita_sin_hora,
        }

    if nueva_hora is None:
        return {"error": "Debe indicar una nueva hora o marcar sin_hora_especifica"}

    ocupado = horario_choca_con_duracion(
        db=db,
        empresa_id=cita_anterior.empresa_id,
        fecha=nueva_fecha,
        hora=nueva_hora,
        servicio_id=cita_anterior.servicio_id,
        prestador_id=prestador_id_final,
        cita_ignorar_id=cita_anterior.id,
    )

    if ocupado:
        mensaje = (
            "Ese prestador no está disponible en ese horario"
            if prestador_id_final
            else "Ya existe una cita agendada en esa fecha y hora"
        )
        return {"error": mensaje}

    cita_anterior.status = "CANCELADA"

    nueva_cita = Cita(
        nombre=cita_anterior.nombre,
        telefono=cita_anterior.telefono,
        fecha=nueva_fecha,
        hora=nueva_hora,
        status="AGENDADA",
        empresa_id=cita_anterior.empresa_id,
        servicio_id=cita_anterior.servicio_id,
        prestador_id=prestador_id_final,
        canal=cita_anterior.canal,
    )
    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)

    return {
        "mensaje": "Cita reprogramada correctamente",
        "cita_cancelada_id": cita_anterior.id,
        "nueva_cita": nueva_cita,
    }
