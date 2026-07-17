from app.models import Cita, Servicio, Prestador
from app.models.prestador import prestadores_servicios
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
    prestador_id: int | None = None,
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
        prestador_id=prestador_id,
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
    prestador_id: int | None = None,
):
    cita_anterior.status = "CANCELADA"

    prestador_final = (
        prestador_id if prestador_id is not None else cita_anterior.prestador_id
    )

    nueva_cita = Cita(
        nombre=cita_anterior.nombre,
        telefono=cita_anterior.telefono,
        fecha=nueva_fecha,
        hora=nueva_hora,
        status="AGENDADA",
        empresa_id=cita_anterior.empresa_id,
        servicio_id=cita_anterior.servicio_id,
        canal=canal,
        prestador_id=prestador_final,
    )

    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)

    return nueva_cita

def obtener_horarios_disponibles(
    db,
    empresa,
    fecha: str,
    servicio_id: int | None = None,
    prestador_id: int | None = None,
    cita_ignorar_id: int | None = None,
):
    horarios = []

    hora_inicio = int(empresa.horario_inicio.split(":")[0])
    hora_fin = int(empresa.horario_fin.split(":")[0])

    for h in range(hora_inicio, hora_fin + 1):
        hora = f"{h:02d}:00"

        if servicio_id is None:
            ocupada = existe_cita_en_horario(
                db=db,
                empresa_id=empresa.id,
                fecha=fecha,
                hora=hora,
            )
            disponible = ocupada is None
        elif empresa.usa_prestadores and not prestador_id:
            disponible = len(
                obtener_prestadores_disponibles(
                    db=db,
                    empresa_id=empresa.id,
                    servicio_id=servicio_id,
                    fecha=fecha,
                    hora=hora,
                    cita_ignorar_id=cita_ignorar_id,
                )
            ) > 0
        else:
            ocupada = horario_choca_con_duracion(
                db=db,
                empresa_id=empresa.id,
                fecha=fecha,
                hora=hora,
                servicio_id=servicio_id,
                prestador_id=prestador_id,
                cita_ignorar_id=cita_ignorar_id,
            )
            disponible = ocupada is None

        if disponible:
            horarios.append(hora)

    return horarios

def horario_choca_con_duracion(
    db,
    empresa_id: int,
    fecha: str,
    hora: str,
    servicio_id: int,
    prestador_id: int | None = None,
    cita_ignorar_id: int | None = None,
):
    servicio_nuevo = db.query(Servicio).filter(Servicio.id == servicio_id).first()

    duracion_nueva = servicio_nuevo.duracion_minutos if servicio_nuevo else 60

    inicio_nueva = datetime.strptime(f"{fecha} {hora}", "%d/%m/%Y %H:%M")
    fin_nueva = inicio_nueva + timedelta(minutes=duracion_nueva)

    query = (
        db.query(Cita)
        .filter(Cita.empresa_id == empresa_id)
        .filter(Cita.fecha == fecha)
        .filter(Cita.status == "AGENDADA")
    )

    if prestador_id is not None:
        query = query.filter(Cita.prestador_id == prestador_id)

    citas = query.all()

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


def contar_citas_del_dia(db, prestador_id: int, fecha: str):
    return (
        db.query(Cita)
        .filter(
            Cita.prestador_id == prestador_id,
            Cita.fecha == fecha,
            Cita.status == "AGENDADA",
        )
        .count()
    )


def obtener_prestadores_compatibles(db, empresa_id: int, servicio_id: int):
    return (
        db.query(Prestador)
        .join(
            prestadores_servicios,
            Prestador.id == prestadores_servicios.c.prestador_id,
        )
        .filter(
            Prestador.empresa_id == empresa_id,
            Prestador.activo == True,
            prestadores_servicios.c.servicio_id == servicio_id,
        )
        .order_by(Prestador.id)
        .all()
    )


def obtener_prestadores_disponibles(
    db,
    empresa_id: int,
    servicio_id: int,
    fecha: str,
    hora: str,
    cita_ignorar_id: int | None = None,
):
    prestadores = obtener_prestadores_compatibles(db, empresa_id, servicio_id)

    disponibles = []

    for prestador in prestadores:
        ocupado = horario_choca_con_duracion(
            db=db,
            empresa_id=empresa_id,
            fecha=fecha,
            hora=hora,
            servicio_id=servicio_id,
            prestador_id=prestador.id,
            cita_ignorar_id=cita_ignorar_id,
        )

        if not ocupado:
            disponibles.append(prestador)

    return disponibles


def seleccionar_prestador_automaticamente(
    db,
    empresa_id: int,
    servicio_id: int,
    fecha: str,
    hora: str,
    cita_ignorar_id: int | None = None,
):
    prestadores_disponibles = obtener_prestadores_disponibles(
        db=db,
        empresa_id=empresa_id,
        servicio_id=servicio_id,
        fecha=fecha,
        hora=hora,
        cita_ignorar_id=cita_ignorar_id,
    )

    if not prestadores_disponibles:
        return None

    prestadores_disponibles.sort(
        key=lambda prestador: (
            contar_citas_del_dia(db=db, prestador_id=prestador.id, fecha=fecha),
            prestador.id,
        )
    )

    return prestadores_disponibles[0]