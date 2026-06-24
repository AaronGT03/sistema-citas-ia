from app.models import Cita, Servicio
from datetime import datetime, timedelta


def fecha_ya_paso(fecha: str):
    fecha_cita = datetime.strptime(fecha, "%d/%m/%Y").date()
    hoy = datetime.now().date()

    return fecha_cita < hoy


def hora_ya_paso(fecha: str, hora: str):
    fecha_hora_cita = datetime.strptime(
        f"{fecha} {hora}",
        "%d/%m/%Y %H:%M"
    )

    ahora = datetime.now()

    return fecha_hora_cita <= ahora


def existe_cita_en_horario(db, empresa_id: int, fecha: str, hora: str):
    return (
        db.query(Cita)
        .filter(Cita.empresa_id == empresa_id)
        .filter(Cita.fecha == fecha)
        .filter(Cita.hora == hora)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

def horario_disponible(db, empresa_id: int, fecha: str, hora: str):
    cita_existente = (
        db.query(Cita)
        .filter(
            Cita.empresa_id == empresa_id,
            Cita.fecha == fecha,
            Cita.hora == hora,
            Cita.status == "AGENDADA",
        )
        .first()
    )

    return cita_existente is None


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

def obtener_horarios_disponibles(db, empresa, fecha: str):
    horarios = []

    hora_inicio = int(empresa.horario_inicio.split(":")[0])
    hora_fin = int(empresa.horario_fin.split(":")[0])

    for h in range(hora_inicio, hora_fin + 1):
        hora = f"{h:02d}:00"

        ocupada = existe_cita_en_horario(
            db=db,
            empresa_id=empresa.id,
            fecha=fecha,
            hora=hora,
        )

        if not ocupada:
            horarios.append(hora)

    return horarios

def horario_choca_con_duracion(
    db,
    empresa_id: int,
    fecha: str,
    hora: str,
    servicio_id: int,
    cita_ignorar_id: int | None = None,
):
    servicio_nuevo = db.query(Servicio).filter(Servicio.id == servicio_id).first()

    duracion_nueva = servicio_nuevo.duracion_minutos if servicio_nuevo else 60

    inicio_nueva = datetime.strptime(f"{fecha} {hora}", "%d/%m/%Y %H:%M")
    fin_nueva = inicio_nueva + timedelta(minutes=duracion_nueva)

    citas = (
        db.query(Cita)
        .filter(Cita.empresa_id == empresa_id)
        .filter(Cita.fecha == fecha)
        .filter(Cita.status == "AGENDADA")
        .all()
    )

    for cita in citas:
        if cita_ignorar_id and cita.id == cita_ignorar_id:
            continue

        servicio_existente = (
            db.query(Servicio)
            .filter(Servicio.id == cita.servicio_id)
            .first()
        )

        duracion_existente = (
            servicio_existente.duracion_minutos
            if servicio_existente
            else 60
        )

        inicio_existente = datetime.strptime(
            f"{cita.fecha} {cita.hora}",
            "%d/%m/%Y %H:%M"
        )
        fin_existente = inicio_existente + timedelta(minutes=duracion_existente)

        if inicio_nueva < fin_existente and fin_nueva > inicio_existente:
            return cita

    return None