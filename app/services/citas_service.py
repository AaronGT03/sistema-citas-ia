from app.models import Cita


def existe_cita_en_horario(db, empresa_id: int, fecha: str, hora: str):
    return (
        db.query(Cita)
        .filter(Cita.empresa_id == empresa_id)
        .filter(Cita.fecha == fecha)
        .filter(Cita.hora == hora)
        .filter(Cita.status == "AGENDADA")
        .first()
    )


def crear_cita(
    db,
    nombre: str,
    telefono: str,
    fecha: str,
    hora: str,
    empresa_id: int,
    servicio_id: int | None = None,
    canal: str = "LLAMADA",
):
    nueva_cita = Cita(
        nombre=nombre,
        telefono=telefono,
        fecha=fecha,
        hora=hora,
        status="AGENDADA",
        empresa_id=empresa_id,
        servicio_id=servicio_id,
        canal=canal,
    )

    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)

    return nueva_cita


def cancelar_cita(db, cita: Cita):
    cita.status = "CANCELADA"
    db.commit()
    db.refresh(cita)

    return cita


def reprogramar_cita(
    db,
    cita_anterior: Cita,
    nueva_fecha: str,
    nueva_hora: str,
    canal: str = "LLAMADA",
):
    cita_anterior.status = "CANCELADA"

    nueva_cita = Cita(
        nombre=cita_anterior.nombre,
        telefono=cita_anterior.telefono,
        fecha=nueva_fecha,
        hora=nueva_hora,
        status="AGENDADA",
        empresa_id=cita_anterior.empresa_id,
        servicio_id=cita_anterior.servicio_id,
        canal=canal,
    )

    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)

    return nueva_cita