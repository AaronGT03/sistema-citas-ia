from fastapi import APIRouter, Request, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import NumeroWhatsApp, Empresa, Conversacion, Servicio, Cita, Prestador
from app.services.whatsapp_service import (
    enviar_mensaje_whatsapp,
    enviar_botones_whatsapp,
    enviar_lista_whatsapp,
)
from app.services.citas_service import (
    crear_cita,
    existe_cita_en_horario,
    cancelar_cita,
    reprogramar_cita,
    obtener_horarios_disponibles,
    fecha_ya_paso,
    hora_ya_paso,
    horario_choca_con_duracion,
    obtener_prestadores_compatibles,
    seleccionar_prestador_automaticamente,
    crear_solicitud_sin_hora,
    construir_contexto_empresa,
    resolver_por_nombre,
    vocabulario_por_giro,
)
from app.services.openai_service import interpretar_mensaje, responder_pregunta_empresa
from app.utils import (
    normalizar_fecha,
    normalizar_hora,
    normalizar_telefono_mexico,
    normalizar_fecha_iso,
    normalizar_hora_valida,
)
from app.core.config import META_VERIFY_TOKEN

router = APIRouter(tags=["WhatsApp"])

_SIN_VALOR = object()


def _titulo_boton(preferido: str, respaldo: str) -> str:
    # WhatsApp limita los títulos de botón a 20 caracteres; si el término
    # del giro hace que el título se pase, se usa el respaldo corto.
    return preferido if len(preferido) <= 20 else respaldo


@router.get("/webhook-whatsapp")
def verificar_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == META_VERIFY_TOKEN:
        return int(hub_challenge)

    raise HTTPException(status_code=403, detail="Token inválido")


def obtener_flujo_activo(db: Session, empresa_id: int, telefono_cliente: str):
    return (
        db.query(Conversacion)
        .filter(
            Conversacion.empresa_id == empresa_id,
            Conversacion.telefono == telefono_cliente,
            Conversacion.canal == "WHATSAPP",
            Conversacion.paso != None,
        )
        .order_by(Conversacion.id.desc())
        .first()
    )


def limpiar_flujos_activos(db: Session, empresa_id: int, telefono_cliente: str):
    flujo = (
        db.query(Conversacion)
        .filter(
            Conversacion.empresa_id == empresa_id,
            Conversacion.telefono == telefono_cliente,
            Conversacion.canal == "WHATSAPP",
        )
        .order_by(Conversacion.id.desc())
        .first()
    )

    if flujo:
        flujo.paso = None
        flujo.nombre = None
        flujo.fecha = None
        flujo.hora = None
        flujo.servicio_id = None
        flujo.prestador_id = None
        flujo.asignacion_automatica = False
        flujo.mensaje = None
        flujo.respuesta = None
        db.commit()


def guardar_conversacion(
    db: Session,
    empresa_id: int,
    telefono_cliente: str,
    mensaje: str,
    respuesta: str,
    paso: str | None = None,
    nombre: str | None = None,
    fecha: str | None = None,
    hora: str | None = None,
    servicio_id: int | None = None,
    prestador_id=_SIN_VALOR,
    asignacion_automatica=_SIN_VALOR,
):
    flujo = (
        db.query(Conversacion)
        .filter(
            Conversacion.empresa_id == empresa_id,
            Conversacion.telefono == telefono_cliente,
            Conversacion.canal == "WHATSAPP",
        )
        .order_by(Conversacion.id.desc())
        .first()
    )

    # prestador_id/asignacion_automatica no se piden en cada paso del flujo
    # (a diferencia de nombre/fecha/hora/servicio_id); si no se pasan
    # explícitamente se conserva lo que ya tenía la conversación.
    if prestador_id is _SIN_VALOR:
        prestador_id = flujo.prestador_id if flujo else None

    if asignacion_automatica is _SIN_VALOR:
        asignacion_automatica = flujo.asignacion_automatica if flujo else False

    if flujo:
        flujo.paso = paso
        flujo.nombre = nombre
        flujo.fecha = fecha
        flujo.hora = hora
        flujo.servicio_id = servicio_id
        flujo.prestador_id = prestador_id
        flujo.asignacion_automatica = asignacion_automatica

        # Ya no guardamos mensajes ni respuestas
        flujo.mensaje = None
        flujo.respuesta = None

        db.commit()
        db.refresh(flujo)

        return flujo

    flujo = Conversacion(
        empresa_id=empresa_id,
        telefono=telefono_cliente,
        canal="WHATSAPP",

        # Ya no guardamos mensajes ni respuestas
        mensaje=None,
        respuesta=None,

        paso=paso,
        nombre=nombre,
        fecha=fecha,
        hora=hora,
        servicio_id=servicio_id,
        prestador_id=prestador_id,
        asignacion_automatica=asignacion_automatica,
    )

    db.add(flujo)
    db.commit()
    db.refresh(flujo)

    return flujo


def enviar_respuesta(numero, telefono_cliente: str, respuesta: str):
    enviar_mensaje_whatsapp(
        phone_number_id=numero.phone_number_id,
        token=numero.token,
        telefono_cliente=telefono_cliente,
        mensaje=respuesta,
    )


def enviar_menu_principal(
    db,
    empresa,
    numero,
    telefono_cliente,
):
    cita_activa = (
        db.query(Cita)
        .filter(
            Cita.empresa_id == empresa.id,
            Cita.telefono == telefono_cliente,
            Cita.status == "AGENDADA",
        )
        .first()
    )

    if cita_activa:
        botones = [
            {"id": "CONSULTAR_CITA", "title": "Mi cita"},
            {"id": "CANCELAR_CITA", "title": "Cancelar"},
            {"id": "REPROGRAMAR_CITA", "title": "Reprogramar"},
        ]

        texto = "Hola 👋\n\nYa tienes una cita registrada.\n¿Qué deseas hacer?"

    else:
        botones = [{"id": "AGENDAR_CITA", "title": "Agendar cita"}]

        texto = f"Hola 👋\n\nBienvenido a {empresa.nombre}\n\n¿Qué deseas hacer?"

    enviar_botones_whatsapp(
        phone_number_id=numero.phone_number_id,
        token=numero.token,
        telefono_cliente=telefono_cliente,
        texto=texto,
        botones=botones,
    )


# =====================================================================
# Arranque del flujo de agendado a partir de datos ya extraídos por
# OpenAI (mensaje libre inicial tipo "quiero un corte con Jeremy hoy a
# las 4"). Reutiliza exactamente las mismas funciones de validación de
# app/services/citas_service.py que usa el flujo determinístico paso a
# paso — nunca crea una cita sin pasar por esas validaciones. Si algún
# dato no se puede resolver con certeza contra los datos reales de la
# empresa, se retoma el flujo normal preguntando lo que falte, en el
# mismo orden que sigue el flujo determinístico existente.
# =====================================================================

_resolver_por_nombre = resolver_por_nombre


def _continuar_agendado_ia(
    db: Session,
    empresa,
    numero,
    telefono_cliente: str,
    flujo,
    respuesta_usuario: str | None,
):
    """Procesa la siguiente respuesta del cliente dentro del paso
    "AGENDAR_IA" y decide qué falta a continuación. `flujo.mensaje` se
    reutiliza como marcador interno de qué sub-pregunta está pendiente
    (nunca se muestra al cliente ni se usa para nada más, ya que el resto
    del sistema ya no guarda mensajes ahí). Nunca crea/reprograma una cita
    sin pasar por las mismas validaciones de citas_service.py que usa el
    flujo determinístico."""

    servicio = db.query(Servicio).filter(Servicio.id == flujo.servicio_id).first()
    marcador = flujo.mensaje
    vocab = vocabulario_por_giro(empresa.giro)

    if marcador == "ESPERANDO_SERVICIO":
        servicios = (
            db.query(Servicio)
            .filter(Servicio.empresa_id == empresa.id, Servicio.activo == True)
            .order_by(Servicio.id)
            .all()
        )

        servicio_elegido = None

        if respuesta_usuario and respuesta_usuario.startswith("SERVICIO_"):
            try:
                servicio_id_elegido = int(respuesta_usuario.replace("SERVICIO_", ""))
            except ValueError:
                servicio_id_elegido = None

            servicio_elegido = next((s for s in servicios if s.id == servicio_id_elegido), None)

        if not servicio_elegido:
            filas = [{"id": f"SERVICIO_{s.id}", "title": s.nombre} for s in servicios]
            enviar_lista_whatsapp(
                phone_number_id=numero.phone_number_id,
                token=numero.token,
                telefono_cliente=telefono_cliente,
                texto="No reconocí esa opción. Por favor selecciona un servicio de la lista:",
                boton_texto="Ver servicios",
                filas=filas,
            )
            return

        flujo.servicio_id = servicio_elegido.id

        if flujo.prestador_id and empresa.usa_prestadores:
            compatibles_ids = {
                p.id for p in obtener_prestadores_compatibles(db, empresa.id, servicio_elegido.id)
            }

            if flujo.prestador_id not in compatibles_ids:
                flujo.prestador_id = None

        flujo.mensaje = None
        db.commit()

        servicio = servicio_elegido

    elif marcador == "ESPERANDO_TIPO_PRESTADOR":
        if respuesta_usuario == "CUALQUIER_PRESTADOR":
            flujo.asignacion_automatica = True
            flujo.mensaje = None
            db.commit()
        elif respuesta_usuario == "ELEGIR_PRESTADOR":
            prestadores = obtener_prestadores_compatibles(db, empresa.id, servicio.id)

            if not prestadores:
                flujo.asignacion_automatica = True
                flujo.mensaje = None
                db.commit()
            else:
                flujo.mensaje = "ESPERANDO_PRESTADOR_ESPECIFICO"
                db.commit()

                filas = [{"id": f"PRESTADOR_{p.id}", "title": p.nombre} for p in prestadores]
                enviar_lista_whatsapp(
                    phone_number_id=numero.phone_number_id,
                    token=numero.token,
                    telefono_cliente=telefono_cliente,
                    texto=f"Selecciona el {vocab['prestador']} con el que deseas atenderte:",
                    boton_texto=_titulo_boton(f"Ver {vocab['prestador_plural']}", "Ver opciones"),
                    filas=filas,
                )
                return
        else:
            enviar_botones_whatsapp(
                phone_number_id=numero.phone_number_id,
                token=numero.token,
                telefono_cliente=telefono_cliente,
                texto="Por favor selecciona una opción.",
                botones=[
                    {"id": "ELEGIR_PRESTADOR", "title": _titulo_boton(f"Elegir {vocab['prestador']}", "Elegir")},
                    {"id": "CUALQUIER_PRESTADOR", "title": "Cualquiera"},
                ],
            )
            return

    elif marcador == "ESPERANDO_PRESTADOR_ESPECIFICO":
        prestadores = obtener_prestadores_compatibles(db, empresa.id, servicio.id)
        prestador = None

        if respuesta_usuario and respuesta_usuario.startswith("PRESTADOR_"):
            try:
                prestador_id_elegido = int(respuesta_usuario.replace("PRESTADOR_", ""))
            except ValueError:
                prestador_id_elegido = None

            prestador = next((p for p in prestadores if p.id == prestador_id_elegido), None)

        if not prestador:
            filas = [{"id": f"PRESTADOR_{p.id}", "title": p.nombre} for p in prestadores]
            enviar_lista_whatsapp(
                phone_number_id=numero.phone_number_id,
                token=numero.token,
                telefono_cliente=telefono_cliente,
                texto=f"No reconocí esa opción. Por favor selecciona un {vocab['prestador']} de la lista:",
                boton_texto=_titulo_boton(f"Ver {vocab['prestador_plural']}", "Ver opciones"),
                filas=filas,
            )
            return

        flujo.prestador_id = prestador.id
        flujo.asignacion_automatica = False
        flujo.mensaje = None
        db.commit()

    elif marcador == "ESPERANDO_NOMBRE":
        nombre_limpio = (respuesta_usuario or "").strip()

        if not nombre_limpio:
            enviar_respuesta(numero, telefono_cliente, "¿Cuál es tu nombre completo?")
            return

        flujo.nombre = nombre_limpio
        flujo.mensaje = None
        db.commit()

    elif marcador == "ESPERANDO_FECHA":
        fecha = normalizar_fecha((respuesta_usuario or "").strip())

        if fecha is None:
            enviar_respuesta(
                numero,
                telefono_cliente,
                "No entendí la fecha.\nPor favor escribe una fecha como: 25 de junio o 25/06/2026.",
            )
            return

        if fecha_ya_paso(fecha):
            enviar_respuesta(
                numero, telefono_cliente, "Esa fecha ya pasó.\nPor favor indica una fecha futura."
            )
            return

        flujo.fecha = fecha
        flujo.mensaje = None
        db.commit()

    elif marcador == "ESPERANDO_TIPO_HORA":
        if respuesta_usuario == "SIN_HORA_ESPECIFICA":
            flujo.sin_hora_especifica = True
            flujo.mensaje = None
            db.commit()
        elif respuesta_usuario == "HORA_ESPECIFICA":
            flujo.mensaje = "ESPERANDO_HORA"
            db.commit()

            enviar_respuesta(numero, telefono_cliente, "¿A qué hora deseas tu cita?")
            return
        else:
            enviar_botones_whatsapp(
                phone_number_id=numero.phone_number_id,
                token=numero.token,
                telefono_cliente=telefono_cliente,
                texto="Por favor selecciona una opción.",
                botones=[
                    {"id": "HORA_ESPECIFICA", "title": "Hora específica"},
                    {"id": "SIN_HORA_ESPECIFICA", "title": "Sin preferencia"},
                ],
            )
            return

    elif marcador == "ESPERANDO_HORA":
        hora = normalizar_hora((respuesta_usuario or "").strip())

        if hora == "AMBIGUA":
            enviar_respuesta(
                numero,
                telefono_cliente,
                f"¿Te refieres a las {(respuesta_usuario or '').strip()} de la mañana o de la "
                "tarde? Por favor indica la hora completa, ej. 4 de la tarde.",
            )
            return

        if hora is None:
            enviar_respuesta(
                numero,
                telefono_cliente,
                "No entendí la hora.\nPor favor escribe una hora como: 10:00, 3 pm o 5 de la tarde.",
            )
            return

        if hora_ya_paso(flujo.fecha, hora):
            enviar_respuesta(
                numero, telefono_cliente, "Esa hora ya pasó.\nPor favor indica una hora futura."
            )
            return

        if hora < empresa.horario_inicio or hora > empresa.horario_fin:
            enviar_respuesta(
                numero,
                telefono_cliente,
                f"Lo siento, nuestro horario de atención es de {empresa.horario_inicio} a "
                f"{empresa.horario_fin}.\n\nPor favor indica otra hora.",
            )
            return

        flujo.hora = hora
        flujo.mensaje = None
        db.commit()

    # ---- Con el estado ya actualizado, decide qué falta a continuación ----

    if not flujo.servicio_id:
        flujo.mensaje = "ESPERANDO_SERVICIO"
        db.commit()

        servicios_activos = (
            db.query(Servicio)
            .filter(Servicio.empresa_id == empresa.id, Servicio.activo == True)
            .order_by(Servicio.id)
            .all()
        )

        filas = [{"id": f"SERVICIO_{s.id}", "title": s.nombre} for s in servicios_activos]
        enviar_lista_whatsapp(
            phone_number_id=numero.phone_number_id,
            token=numero.token,
            telefono_cliente=telefono_cliente,
            texto="¿Qué servicio te gustaría agendar?",
            boton_texto="Ver servicios",
            filas=filas,
        )
        return

    if empresa.usa_prestadores and not flujo.prestador_id and not flujo.asignacion_automatica:
        flujo.mensaje = "ESPERANDO_TIPO_PRESTADOR"
        db.commit()

        enviar_botones_whatsapp(
            phone_number_id=numero.phone_number_id,
            token=numero.token,
            telefono_cliente=telefono_cliente,
            texto=(
                f"Para tu {servicio.nombre}, ¿deseas atenderte con un {vocab['prestador']} "
                "específico o con cualquiera disponible?"
            ),
            botones=[
                {"id": "ELEGIR_PRESTADOR", "title": _titulo_boton(f"Elegir {vocab['prestador']}", "Elegir")},
                {"id": "CUALQUIER_PRESTADOR", "title": "Cualquiera"},
            ],
        )
        return

    if not flujo.nombre:
        flujo.mensaje = "ESPERANDO_NOMBRE"
        db.commit()

        enviar_respuesta(numero, telefono_cliente, "¡Perfecto! ¿Cuál es tu nombre completo?")
        return

    if not flujo.fecha:
        flujo.mensaje = "ESPERANDO_FECHA"
        db.commit()

        enviar_respuesta(
            numero,
            telefono_cliente,
            f"Gracias {flujo.nombre}. ¿Qué fecha deseas para tu cita?\n"
            "Ejemplo: 25 de junio o 25/06/2026",
        )
        return

    if not flujo.hora and not flujo.sin_hora_especifica:
        if empresa.permite_citas_sin_hora:
            flujo.mensaje = "ESPERANDO_TIPO_HORA"
            db.commit()

            enviar_botones_whatsapp(
                phone_number_id=numero.phone_number_id,
                token=numero.token,
                telefono_cliente=telefono_cliente,
                texto=(
                    f"Registré la fecha {flujo.fecha}.\n\n"
                    "¿Deseas elegir una hora específica o agendar sin preferencia de horario?"
                ),
                botones=[
                    {"id": "HORA_ESPECIFICA", "title": "Hora específica"},
                    {"id": "SIN_HORA_ESPECIFICA", "title": "Sin preferencia"},
                ],
            )
        else:
            flujo.mensaje = "ESPERANDO_HORA"
            db.commit()

            enviar_respuesta(
                numero,
                telefono_cliente,
                f"Perfecto. Registré la fecha {flujo.fecha}.\n\n¿A qué hora deseas tu cita?",
            )
        return

    # ---- Ya se conoce todo lo necesario: crear la cita ----

    if flujo.sin_hora_especifica:
        try:
            crear_solicitud_sin_hora(
                db=db,
                empresa=empresa,
                servicio=servicio,
                fecha=flujo.fecha,
                nombre=flujo.nombre,
                telefono=telefono_cliente,
                canal="WHATSAPP",
                prestador_id=flujo.prestador_id,
            )
        except ValueError as error:
            enviar_respuesta(
                numero, telefono_cliente, f"No fue posible registrar tu solicitud: {error}."
            )
            return

        enviar_respuesta(
            numero,
            telefono_cliente,
            f"✅ Registré tu solicitud para el {flujo.fecha} sin hora específica.\n\n"
            "La empresa te confirmará el horario más adelante.",
        )
        enviar_menu_principal(db=db, empresa=empresa, numero=numero, telefono_cliente=telefono_cliente)

        db.delete(flujo)
        db.commit()
        return

    prestador_final = flujo.prestador_id
    horarios_alternativos = None
    cita_ocupada = False

    if empresa.usa_prestadores:
        if flujo.prestador_id:
            cita_ocupada = horario_choca_con_duracion(
                db=db,
                empresa_id=empresa.id,
                fecha=flujo.fecha,
                hora=flujo.hora,
                servicio_id=servicio.id,
                prestador_id=flujo.prestador_id,
            )

            if cita_ocupada:
                horarios_alternativos = obtener_horarios_disponibles(
                    db=db,
                    empresa=empresa,
                    fecha=flujo.fecha,
                    servicio_id=servicio.id,
                    prestador_id=flujo.prestador_id,
                )
        else:
            prestador_seleccionado = seleccionar_prestador_automaticamente(
                db=db,
                empresa_id=empresa.id,
                servicio_id=servicio.id,
                fecha=flujo.fecha,
                hora=flujo.hora,
            )

            if not prestador_seleccionado:
                cita_ocupada = True
                horarios_alternativos = obtener_horarios_disponibles(
                    db=db, empresa=empresa, fecha=flujo.fecha, servicio_id=servicio.id,
                )
            else:
                prestador_final = prestador_seleccionado.id
    else:
        cita_ocupada = horario_choca_con_duracion(
            db=db,
            empresa_id=empresa.id,
            fecha=flujo.fecha,
            hora=flujo.hora,
            servicio_id=servicio.id,
        )

        if cita_ocupada:
            horarios_alternativos = obtener_horarios_disponibles(
                db=db, empresa=empresa, fecha=flujo.fecha
            )

    if cita_ocupada:
        if horarios_alternativos:
            lista_horarios = "\n".join([f"- {h}" for h in horarios_alternativos[:5]])

            flujo.hora = None
            flujo.mensaje = "ESPERANDO_HORA"
            db.commit()

            respuesta = (
                "Esa hora ya está ocupada.\n\n"
                "Horarios disponibles para esa fecha:\n"
                f"{lista_horarios}\n\n"
                "Por favor escribe uno de esos horarios."
            )
        else:
            flujo.fecha = None
            flujo.hora = None
            flujo.mensaje = "ESPERANDO_FECHA"
            db.commit()

            respuesta = "Ese día ya no tiene más horarios disponibles.\nPor favor indica otra fecha."

        enviar_respuesta(numero, telefono_cliente, respuesta)
        return

    cita = crear_cita(
        db=db,
        nombre=flujo.nombre,
        telefono=telefono_cliente,
        fecha=flujo.fecha,
        hora=flujo.hora,
        empresa_id=empresa.id,
        servicio_id=servicio.id,
        canal="WHATSAPP",
        prestador_id=prestador_final,
    )

    respuesta = (
        "✅ Cita agendada correctamente\n\n"
        f"Servicio: {servicio.nombre}\n"
        f"Nombre: {cita.nombre}\n"
        f"Fecha: {cita.fecha}\n"
        f"Hora: {cita.hora}"
    )

    enviar_respuesta(numero, telefono_cliente, respuesta)
    enviar_menu_principal(db=db, empresa=empresa, numero=numero, telefono_cliente=telefono_cliente)

    db.delete(flujo)
    db.commit()


def iniciar_agendado_desde_ia(
    db: Session,
    empresa,
    numero,
    telefono_cliente: str,
    resultado_ia,
    mensaje_original: str,
) -> bool:
    """Punto de entrada: intenta arrancar el agendado con los datos que
    OpenAI ya extrajo del mensaje libre del cliente. Devuelve False si no
    se pudo resolver ni siquiera el servicio contra el catálogo real de la
    empresa, para que el llamador use el mensaje genérico de "escribe
    agendar cita". Si devuelve True, ya le respondió al cliente (con la
    siguiente pregunta que falte, o con la cita ya creada)."""

    servicios_activos = (
        db.query(Servicio)
        .filter(Servicio.empresa_id == empresa.id, Servicio.activo == True)
        .all()
    )

    servicio = _resolver_por_nombre(resultado_ia.servicio, servicios_activos)

    nombre = resultado_ia.nombre.strip() if resultado_ia.nombre else None

    fecha = None
    if resultado_ia.fecha:
        fecha_candidata = normalizar_fecha_iso(resultado_ia.fecha) or normalizar_fecha(
            resultado_ia.fecha
        )
        if fecha_candidata and not fecha_ya_paso(fecha_candidata):
            fecha = fecha_candidata

    sin_hora = bool(resultado_ia.sin_hora_especifica and empresa.permite_citas_sin_hora)

    hora = None
    if not sin_hora and resultado_ia.hora:
        hora = normalizar_hora_valida(resultado_ia.hora)

    prestador_id = None
    asignacion_automatica = False

    if empresa.usa_prestadores:
        if resultado_ia.cualquier_prestador:
            asignacion_automatica = True
        elif resultado_ia.prestador:
            # Si ya sabemos el servicio, solo cuentan los prestadores que lo
            # realizan; si aún no se resolvió el servicio, se busca contra
            # todos los prestadores activos de la empresa (se revalida la
            # compatibilidad en cuanto se conozca el servicio).
            candidatos = (
                obtener_prestadores_compatibles(db, empresa.id, servicio.id)
                if servicio
                else db.query(Prestador)
                .filter(Prestador.empresa_id == empresa.id, Prestador.activo == True)
                .all()
            )
            prestador = _resolver_por_nombre(resultado_ia.prestador, candidatos)

            if prestador:
                prestador_id = prestador.id

    datos_utiles = bool(
        servicio or nombre or fecha or hora or prestador_id or asignacion_automatica
    )

    if not datos_utiles:
        return False

    limpiar_flujos_activos(db, empresa.id, telefono_cliente)

    flujo = guardar_conversacion(
        db=db,
        empresa_id=empresa.id,
        telefono_cliente=telefono_cliente,
        mensaje=mensaje_original,
        respuesta="",
        paso="AGENDAR_IA",
        nombre=nombre,
        fecha=fecha,
        hora=hora,
        servicio_id=servicio.id if servicio else None,
        prestador_id=prestador_id,
        asignacion_automatica=asignacion_automatica,
    )
    flujo.sin_hora_especifica = sin_hora
    db.commit()

    _continuar_agendado_ia(
        db=db,
        empresa=empresa,
        numero=numero,
        telefono_cliente=telefono_cliente,
        flujo=flujo,
        respuesta_usuario=None,
    )

    return True


@router.post("/webhook-whatsapp")
async def recibir_webhook(request: Request, db: Session = Depends(get_db)):
    data = await request.json()

    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            print("Evento de estado ignorado")
            return {"status": "evento ignorado"}

        metadata = value["metadata"]
        numero_receptor = metadata["display_phone_number"]

        mensaje_data = value["messages"][0]
        telefono_cliente = mensaje_data["from"]
        telefono_cliente = normalizar_telefono_mexico(telefono_cliente)

        tipo_mensaje = mensaje_data.get("type")

        if tipo_mensaje == "interactive":
            interactive = mensaje_data["interactive"]

            if "button_reply" in interactive:
                mensaje = interactive["button_reply"]["id"]
            elif "list_reply" in interactive:
                mensaje = interactive["list_reply"]["id"]
            else:
                mensaje = ""

        else:
            mensaje = mensaje_data["text"]["body"]

        mensaje_lower = mensaje.lower().strip()

        numero = (
            db.query(NumeroWhatsApp)
            .filter(NumeroWhatsApp.telefono == numero_receptor)
            .first()
        )

        if not numero:
            print("Número no registrado:", numero_receptor)
            return {"status": "error", "mensaje": "Número no registrado"}

        empresa = db.query(Empresa).filter(Empresa.id == numero.empresa_id).first()

        if not empresa:
            print("Empresa no encontrada")
            return {"status": "error", "mensaje": "Empresa no encontrada"}

        print("\n=== MENSAJE RECIBIDO ===")
        print("EMPRESA:", empresa.nombre)
        print("CLIENTE:", telefono_cliente)
        print("MENSAJE:", mensaje)

        # Términos conversacionales según el giro de la empresa (solo texto
        # mostrado al cliente; la lógica no cambia).
        vocab = vocabulario_por_giro(empresa.giro)

        flujo = obtener_flujo_activo(
            db=db,
            empresa_id=empresa.id,
            telefono_cliente=telefono_cliente,
        )

        if flujo:
            print("FLUJO ACTUAL:", flujo.paso)
        else:
            print("SIN FLUJO ACTIVO")

        # =========================
        # CONVERTIR BOTONES A ACCIONES
        # =========================
        if mensaje == "AGENDAR_CITA":
            mensaje_lower = "agendar cita"

        if mensaje == "CONSULTAR_CITA":
            mensaje_lower = "consultar cita"

        if mensaje == "CANCELAR_CITA":
            mensaje_lower = "cancelar cita"

        if mensaje == "REPROGRAMAR_CITA":
            mensaje_lower = "reprogramar cita"

        # =========================
        # MENU PRINCIPAL
        # =========================
        if mensaje_lower in [
            "hola",
            "buenas",
            "buenos dias",
            "buen día",
            "menu",
            ".",
        ]:
            limpiar_flujos_activos(db, empresa.id, telefono_cliente)

            enviar_menu_principal(
                db=db,
                empresa=empresa,
                numero=numero,
                telefono_cliente=telefono_cliente,
            )

            return {"status": "MENU"}
        # =========================
        # CONSULTAR CITA
        # =========================

        if "consultar" in mensaje_lower and "cita" in mensaje_lower:
            cita = (
                db.query(Cita)
                .filter(
                    Cita.empresa_id == empresa.id,
                    Cita.telefono == telefono_cliente,
                    Cita.status == "AGENDADA",
                )
                .first()
            )

            if not cita:
                respuesta = "No encontré ninguna cita activa."

            else:
                servicio = (
                    db.query(Servicio).filter(Servicio.id == cita.servicio_id).first()
                )

                nombre_servicio = servicio.nombre if servicio else "Servicio"

                respuesta = (
                    "📅 Tu cita actual\n\n"
                    f"Servicio: {nombre_servicio}\n"
                    f"Fecha: {cita.fecha}\n"
                    f"Hora: {cita.hora}"
                )

            enviar_respuesta(
                numero,
                telefono_cliente,
                respuesta,
            )
            enviar_menu_principal(
                db=db,
                empresa=empresa,
                numero=numero,
                telefono_cliente=telefono_cliente,
            )

            return {"status": "CONSULTAR_CITA"}
        # =========================
        # CANCELAR CITA
        # =========================
        if "cancelar" in mensaje_lower and "cita" in mensaje_lower:
            limpiar_flujos_activos(db, empresa.id, telefono_cliente)

            cita = (
                db.query(Cita)
                .filter(
                    Cita.empresa_id == empresa.id,
                    Cita.telefono == telefono_cliente,
                    Cita.status == "AGENDADA",
                )
                .first()
            )

            if not cita:
                respuesta = "No encontré ninguna cita activa."
            else:
                respuesta = (
                    "Encontré esta cita:\n\n"
                    f"Fecha: {cita.fecha}\n"
                    f"Hora: {cita.hora}\n\n"
                    "¿Deseas cancelarla?\n"
                    "Responde SI o NO."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="CONFIRMAR_CANCELACION",
                )

            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "CONFIRMAR_CANCELACION"}

        # =========================
        # REPROGRAMAR CITA
        # =========================
        if "reprogramar" in mensaje_lower and "cita" in mensaje_lower:
            limpiar_flujos_activos(db, empresa.id, telefono_cliente)

            cita = (
                db.query(Cita)
                .filter(
                    Cita.empresa_id == empresa.id,
                    Cita.telefono == telefono_cliente,
                    Cita.status == "AGENDADA",
                )
                .first()
            )

            if not cita:
                respuesta = "No encontré ninguna cita activa para reprogramar."

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "REPROGRAMAR_FECHA"}

            if empresa.usa_prestadores:
                if cita.prestador_id:
                    respuesta = (
                        "Encontré tu cita actual:\n\n"
                        f"Fecha: {cita.fecha}\n"
                        f"Hora: {cita.hora}\n\n"
                        f"¿Deseas mantener a tu mismo {vocab['prestador']} o elegir otro?"
                    )
                    botones = [
                        {"id": "MISMO_PRESTADOR", "title": f"Mismo {vocab['prestador']}"},
                        {"id": "ELEGIR_PRESTADOR", "title": "Elegir otro"},
                    ]
                else:
                    respuesta = (
                        "Encontré tu cita actual:\n\n"
                        f"Fecha: {cita.fecha}\n"
                        f"Hora: {cita.hora}\n\n"
                        f"¿Deseas atenderte con un {vocab['prestador']} específico o con "
                        "cualquiera disponible?"
                    )
                    botones = [
                        {"id": "ELEGIR_PRESTADOR", "title": _titulo_boton(f"Elegir {vocab['prestador']}", "Elegir")},
                        {"id": "CUALQUIER_PRESTADOR", "title": _titulo_boton(f"Cualquier {vocab['prestador']}", "Cualquiera")},
                    ]

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_TIPO_PRESTADOR",
                    nombre=cita.nombre,
                    fecha=cita.fecha,
                    hora=cita.hora,
                    servicio_id=cita.servicio_id,
                    prestador_id=cita.prestador_id,
                )

                enviar_botones_whatsapp(
                    phone_number_id=numero.phone_number_id,
                    token=numero.token,
                    telefono_cliente=telefono_cliente,
                    texto=respuesta,
                    botones=botones,
                )

                return {"status": "REPROGRAMAR_TIPO_PRESTADOR"}

            respuesta = (
                "Encontré tu cita actual:\n\n"
                f"Fecha: {cita.fecha}\n"
                f"Hora: {cita.hora}\n\n"
                "¿Qué nueva fecha deseas?"
            )

            guardar_conversacion(
                db=db,
                empresa_id=empresa.id,
                telefono_cliente=telefono_cliente,
                mensaje=mensaje,
                respuesta=respuesta,
                paso="REPROGRAMAR_FECHA",
                nombre=cita.nombre,
                fecha=cita.fecha,
                hora=cita.hora,
                servicio_id=cita.servicio_id,
            )

            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "REPROGRAMAR_FECHA"}

        # =========================
        # INICIAR AGENDADO
        # =========================
        def _iniciar_flujo_determinista_servicio():
            limpiar_flujos_activos(db, empresa.id, telefono_cliente)

            servicios = (
                db.query(Servicio)
                .filter(
                    Servicio.empresa_id == empresa.id,
                    Servicio.activo == True,
                )
                .all()
            )

            if not servicios:
                respuesta = "Por el momento no hay servicios disponibles para agendar."

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "sin_servicios"}

            lista_servicios = "\n".join(
                [f"{i + 1}. {servicio.nombre}" for i, servicio in enumerate(servicios)]
            )

            respuesta = (
                "Claro. ¿Para qué servicio deseas agendar?\n\n"
                f"{lista_servicios}\n\n"
                "Responde con el número del servicio."
            )

            guardar_conversacion(
                db=db,
                empresa_id=empresa.id,
                telefono_cliente=telefono_cliente,
                mensaje=mensaje,
                respuesta=respuesta,
                paso="PEDIR_SERVICIO",
            )

            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "PEDIR_SERVICIO"}

        coincide_agendar = "agendar" in mensaje_lower and "cita" in mensaje_lower

        if not flujo and mensaje.strip():
            # La IA tiene prioridad sobre el disparador por palabra clave:
            # se intenta primero interpretar el mensaje completo con
            # OpenAI (igual que ya se hace en llamadas.py). El disparador
            # determinístico ("agendar"+"cita") queda como respaldo
            # únicamente para cuando OpenAI no está disponible o no
            # devolvió nada — así nunca se depende por completo de un
            # servicio externo.
            contexto_ia_agendar = construir_contexto_empresa(db, empresa)
            resultado_ia_agendar = interpretar_mensaje(
                empresa=empresa,
                contexto=contexto_ia_agendar,
                paso_actual=None,
                mensaje_usuario=mensaje.strip(),
            )

            if (
                resultado_ia_agendar
                and resultado_ia_agendar.dentro_del_dominio
                and resultado_ia_agendar.intencion == "AGENDAR"
            ):
                iniciado = iniciar_agendado_desde_ia(
                    db=db,
                    empresa=empresa,
                    numero=numero,
                    telefono_cliente=telefono_cliente,
                    resultado_ia=resultado_ia_agendar,
                    mensaje_original=mensaje.strip(),
                )

                if iniciado:
                    return {"status": "AGENDAR_DESDE_IA"}

                # La IA detectó intención de agendar pero no logró
                # reconocer ningún servicio real — continúa con el flujo
                # clásico de elegir servicio por número.
                return _iniciar_flujo_determinista_servicio()

            if resultado_ia_agendar is None and coincide_agendar:
                return _iniciar_flujo_determinista_servicio()

        elif coincide_agendar:
            return _iniciar_flujo_determinista_servicio()

        flujo = obtener_flujo_activo(
            db=db,
            empresa_id=empresa.id,
            telefono_cliente=telefono_cliente,
        )

        # =========================
        # CONFIRMAR CANCELACIÓN
        # =========================
        if flujo and flujo.paso == "CONFIRMAR_CANCELACION":
            if mensaje_lower in ["si", "sí"]:
                cita = (
                    db.query(Cita)
                    .filter(
                        Cita.empresa_id == empresa.id,
                        Cita.telefono == telefono_cliente,
                        Cita.status == "AGENDADA",
                    )
                    .first()
                )

                if cita:
                    cancelar_cita(db, cita)
                    respuesta = "✅ Tu cita ha sido cancelada correctamente."
                else:
                    respuesta = "No encontré una cita activa."
            else:
                respuesta = "Perfecto, tu cita continúa programada."

            guardar_conversacion(
                db=db,
                empresa_id=empresa.id,
                telefono_cliente=telefono_cliente,
                mensaje=mensaje,
                respuesta=respuesta,
            )


            enviar_respuesta(numero, telefono_cliente, respuesta)
            enviar_menu_principal(
                db=db,
                empresa=empresa,
                numero=numero,
                telefono_cliente=telefono_cliente,
            )
            return {"status": "CANCELACION_PROCESADA"}

        # =========================
        # REPROGRAMAR TIPO PRESTADOR
        # =========================
        if flujo and flujo.paso == "REPROGRAMAR_TIPO_PRESTADOR":
            if mensaje == "MISMO_PRESTADOR" and flujo.prestador_id:
                respuesta = "Perfecto. ¿Para qué nueva fecha deseas reprogramar tu cita?"

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_FECHA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    hora=flujo.hora,
                    servicio_id=flujo.servicio_id,
                    prestador_id=flujo.prestador_id,
                    asignacion_automatica=False,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "REPROGRAMAR_FECHA"}

            if mensaje == "CUALQUIER_PRESTADOR" and not flujo.prestador_id:
                respuesta = (
                    f"Perfecto, te atenderá cualquier {vocab['prestador']} disponible.\n\n"
                    "¿Para qué nueva fecha deseas reprogramar tu cita?"
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_FECHA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    hora=flujo.hora,
                    servicio_id=flujo.servicio_id,
                    prestador_id=None,
                    asignacion_automatica=True,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "REPROGRAMAR_FECHA"}

            if mensaje == "ELEGIR_PRESTADOR":
                prestadores = obtener_prestadores_compatibles(
                    db, empresa.id, flujo.servicio_id
                )

                if not prestadores:
                    respuesta = (
                        f"Por el momento no hay {vocab['prestador_plural']} específicos disponibles "
                        f"para ese servicio. Te atenderá cualquier {vocab['prestador']} "
                        "disponible.\n\n¿Para qué nueva fecha deseas reprogramar "
                        "tu cita?"
                    )

                    guardar_conversacion(
                        db=db,
                        empresa_id=empresa.id,
                        telefono_cliente=telefono_cliente,
                        mensaje=mensaje,
                        respuesta=respuesta,
                        paso="REPROGRAMAR_FECHA",
                        nombre=flujo.nombre,
                        fecha=flujo.fecha,
                        hora=flujo.hora,
                        servicio_id=flujo.servicio_id,
                        prestador_id=None,
                        asignacion_automatica=True,
                    )

                    enviar_respuesta(numero, telefono_cliente, respuesta)
                    return {"status": "REPROGRAMAR_FECHA"}

                filas = [
                    {"id": f"PRESTADOR_{prestador.id}", "title": prestador.nombre}
                    for prestador in prestadores
                ]

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=f"Selecciona un {vocab['prestador']}",
                    paso="REPROGRAMAR_PRESTADOR",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    hora=flujo.hora,
                    servicio_id=flujo.servicio_id,
                )

                enviar_lista_whatsapp(
                    phone_number_id=numero.phone_number_id,
                    token=numero.token,
                    telefono_cliente=telefono_cliente,
                    texto=f"Selecciona el {vocab['prestador']} con el que deseas atenderte:",
                    boton_texto=_titulo_boton(f"Ver {vocab['prestador_plural']}", "Ver opciones"),
                    filas=filas,
                )

                return {"status": "REPROGRAMAR_PRESTADOR"}

            respuesta = "No entendí tu respuesta. Por favor selecciona una opción."

            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "REPROGRAMAR_TIPO_PRESTADOR"}

        # =========================
        # REPROGRAMAR PRESTADOR
        # =========================
        if flujo and flujo.paso == "REPROGRAMAR_PRESTADOR":
            prestadores = obtener_prestadores_compatibles(
                db, empresa.id, flujo.servicio_id
            )

            prestador_seleccionado = None

            if mensaje.startswith("PRESTADOR_"):
                try:
                    prestador_id_elegido = int(mensaje.replace("PRESTADOR_", ""))
                except ValueError:
                    prestador_id_elegido = None

                prestador_seleccionado = next(
                    (p for p in prestadores if p.id == prestador_id_elegido), None
                )

            if not prestador_seleccionado:
                respuesta = f"No reconocí ese {vocab['prestador']}. Por favor selecciónalo de la lista."

                filas = [
                    {"id": f"PRESTADOR_{prestador.id}", "title": prestador.nombre}
                    for prestador in prestadores
                ]

                enviar_lista_whatsapp(
                    phone_number_id=numero.phone_number_id,
                    token=numero.token,
                    telefono_cliente=telefono_cliente,
                    texto=respuesta,
                    boton_texto=_titulo_boton(f"Ver {vocab['prestador_plural']}", "Ver opciones"),
                    filas=filas,
                )

                return {"status": "REPROGRAMAR_PRESTADOR"}

            respuesta = (
                f"Perfecto, te atenderá {prestador_seleccionado.nombre}.\n\n"
                "¿Para qué nueva fecha deseas reprogramar tu cita?"
            )

            guardar_conversacion(
                db=db,
                empresa_id=empresa.id,
                telefono_cliente=telefono_cliente,
                mensaje=mensaje,
                respuesta=respuesta,
                paso="REPROGRAMAR_FECHA",
                nombre=flujo.nombre,
                fecha=flujo.fecha,
                hora=flujo.hora,
                servicio_id=flujo.servicio_id,
                prestador_id=prestador_seleccionado.id,
                asignacion_automatica=False,
            )

            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "REPROGRAMAR_FECHA"}

        # =========================
        # REPROGRAMAR FECHA
        # =========================
        if flujo and flujo.paso == "REPROGRAMAR_FECHA":
            nueva_fecha = normalizar_fecha(mensaje.strip())

            if nueva_fecha is None:
                respuesta = (
                    "No entendí la fecha. Escribe algo como 28 de junio o 28/06/2026."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_FECHA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    hora=flujo.hora,
                    servicio_id=flujo.servicio_id,
                )
                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "REPROGRAMAR_FECHA"}

            elif fecha_ya_paso(nueva_fecha):
                respuesta = (
                    "La fecha que elegiste ya pasó.\nPor favor indica una fecha futura."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_FECHA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    hora=flujo.hora,
                    servicio_id=flujo.servicio_id,
                )
                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "REPROGRAMAR_FECHA"}

            respuesta = (
                f"Perfecto. Nueva fecha: {nueva_fecha}.\n\n"
                "¿A qué nueva hora deseas la cita?"
            )

            guardar_conversacion(
                db=db,
                empresa_id=empresa.id,
                telefono_cliente=telefono_cliente,
                mensaje=mensaje,
                respuesta=respuesta,
                paso="REPROGRAMAR_HORA",
                nombre=flujo.nombre,
                fecha=nueva_fecha,
                hora=flujo.hora,
                servicio_id=flujo.servicio_id,
            )


            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "REPROGRAMAR_HORA"}

        # =========================
        # REPROGRAMAR HORA
        # =========================
        if flujo and flujo.paso == "REPROGRAMAR_HORA":
            nueva_hora = normalizar_hora(mensaje.strip())

            if nueva_hora == "AMBIGUA":
                respuesta = (
                    f"¿Te refieres a las {mensaje.strip()} de la mañana o de la tarde?"
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_ACLARAR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    hora=mensaje.strip(),
                    servicio_id=flujo.servicio_id,
                )


                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "REPROGRAMAR_ACLARAR_HORA"}

            if nueva_hora is None:
                respuesta = (
                    "No entendí la hora. Escribe algo como 10:00, 3 pm o 5 de la tarde."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "REPROGRAMAR_HORA"}

            if hora_ya_paso(flujo.fecha, nueva_hora):
                respuesta = "Esa hora ya pasó.\nPor favor indica una hora futura."

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "HORA_YA_PASO"}

            cita_activa = (
                db.query(Cita)
                .filter(
                    Cita.empresa_id == empresa.id,
                    Cita.telefono == telefono_cliente,
                    Cita.status == "AGENDADA",
                )
                .first()
            )

            if not cita_activa:
                respuesta = "No encontré ninguna cita activa para reprogramar."

            elif (
                nueva_hora < empresa.horario_inicio or nueva_hora > empresa.horario_fin
            ):
                respuesta = (
                    f"Lo siento, nuestro horario de atención es de "
                    f"{empresa.horario_inicio} a {empresa.horario_fin}.\n\n"
                    "Por favor indica otra hora."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "HORARIO_FUERA_DE_RANGO"}

            prestador_final = flujo.prestador_id
            horarios_alternativos = None

            if empresa.usa_prestadores and flujo.asignacion_automatica:
                prestador_seleccionado = seleccionar_prestador_automaticamente(
                    db=db,
                    empresa_id=empresa.id,
                    servicio_id=flujo.servicio_id,
                    fecha=flujo.fecha,
                    hora=nueva_hora,
                    cita_ignorar_id=cita_activa.id,
                )

                if not prestador_seleccionado:
                    horarios_alternativos = obtener_horarios_disponibles(
                        db=db,
                        empresa=empresa,
                        fecha=flujo.fecha,
                        servicio_id=flujo.servicio_id,
                        cita_ignorar_id=cita_activa.id,
                    )
                    cita_ocupada = True
                else:
                    prestador_final = prestador_seleccionado.id
                    cita_ocupada = False
            else:
                cita_ocupada = horario_choca_con_duracion(
                    db=db,
                    empresa_id=empresa.id,
                    fecha=flujo.fecha,
                    hora=nueva_hora,
                    servicio_id=flujo.servicio_id,
                    prestador_id=prestador_final,
                    cita_ignorar_id=cita_activa.id,
                )

                if cita_ocupada:
                    horarios_alternativos = obtener_horarios_disponibles(
                        db=db,
                        empresa=empresa,
                        fecha=flujo.fecha,
                        servicio_id=flujo.servicio_id,
                        prestador_id=prestador_final,
                        cita_ignorar_id=cita_activa.id,
                    )

            if cita_ocupada:
                if horarios_alternativos:
                    lista_horarios = "\n".join(
                        [f"- {h}" for h in horarios_alternativos[:5]]
                    )

                    respuesta = (
                        "Ese horario ya está ocupado.\n\n"
                        "Horarios disponibles para esa fecha:\n"
                        f"{lista_horarios}\n\n"
                        "Por favor escribe uno de esos horarios."
                    )
                else:
                    respuesta = (
                        "Ese día ya no tiene horarios disponibles.\n"
                        "Por favor indica otra fecha."
                    )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "HORARIO_OCUPADO"}

            else:
                nueva_cita = reprogramar_cita(
                    db=db,
                    cita_anterior=cita_activa,
                    nueva_fecha=flujo.fecha,
                    nueva_hora=nueva_hora,
                    canal="WHATSAPP",
                    prestador_id=prestador_final,
                )

                respuesta = (
                    "✅ Tu cita ha sido reprogramada correctamente\n\n"
                    f"Fecha: {nueva_cita.fecha}\n"
                    f"Hora: {nueva_cita.hora}"
                )

            guardar_conversacion(
                db=db,
                empresa_id=empresa.id,
                telefono_cliente=telefono_cliente,
                mensaje=mensaje,
                respuesta=respuesta,
            )


            enviar_respuesta(numero, telefono_cliente, respuesta)
            enviar_menu_principal(
                db=db,
                empresa=empresa,
                numero=numero,
                telefono_cliente=telefono_cliente,
            )
            return {"status": "CITA_REPROGRAMADA"}

        # =========================
        # REPROGRAMAR ACLARAR HORA
        # =========================
        if flujo and flujo.paso == "REPROGRAMAR_ACLARAR_HORA":
            hora_original = flujo.hora
            respuesta_usuario = mensaje_lower

            try:
                numero_hora = int("".join(filter(str.isdigit, hora_original)))
            except Exception:
                respuesta = (
                    "No pude identificar la hora. Por favor escribe la hora nuevamente."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "REPROGRAMAR_HORA"}

            if (
                "tarde" in respuesta_usuario
                or "noche" in respuesta_usuario
                or "pm" in respuesta_usuario
            ):
                nueva_hora = f"{numero_hora + 12:02d}:00"
            else:
                nueva_hora = f"{numero_hora:02d}:00"

            if hora_ya_paso(flujo.fecha, nueva_hora):
                respuesta = "Esa hora ya pasó.\nPor favor indica una hora futura."

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "HORA_YA_PASO"}

            if nueva_hora < empresa.horario_inicio or nueva_hora > empresa.horario_fin:
                respuesta = (
                    f"Lo siento, nuestro horario de atención es de "
                    f"{empresa.horario_inicio} a {empresa.horario_fin}.\n\n"
                    "Por favor indica otra hora."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "HORARIO_FUERA_DE_RANGO"}

            cita_activa = (
                db.query(Cita)
                .filter(
                    Cita.empresa_id == empresa.id,
                    Cita.telefono == telefono_cliente,
                    Cita.status == "AGENDADA",
                )
                .first()
            )

            if not cita_activa:
                respuesta = "No encontré ninguna cita activa para reprogramar."

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="REPROGRAMAR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "SIN_CITA_ACTIVA"}

            else:
                prestador_final = flujo.prestador_id
                horarios_alternativos = None

                if empresa.usa_prestadores and flujo.asignacion_automatica:
                    prestador_seleccionado = seleccionar_prestador_automaticamente(
                        db=db,
                        empresa_id=empresa.id,
                        servicio_id=flujo.servicio_id,
                        fecha=flujo.fecha,
                        hora=nueva_hora,
                        cita_ignorar_id=cita_activa.id,
                    )

                    if not prestador_seleccionado:
                        horarios_alternativos = obtener_horarios_disponibles(
                            db=db,
                            empresa=empresa,
                            fecha=flujo.fecha,
                            servicio_id=flujo.servicio_id,
                            cita_ignorar_id=cita_activa.id,
                        )
                        cita_ocupada = True
                    else:
                        prestador_final = prestador_seleccionado.id
                        cita_ocupada = False
                else:
                    cita_ocupada = horario_choca_con_duracion(
                        db=db,
                        empresa_id=empresa.id,
                        fecha=flujo.fecha,
                        hora=nueva_hora,
                        servicio_id=flujo.servicio_id,
                        prestador_id=prestador_final,
                        cita_ignorar_id=cita_activa.id,
                    )

                    if cita_ocupada:
                        horarios_alternativos = obtener_horarios_disponibles(
                            db=db,
                            empresa=empresa,
                            fecha=flujo.fecha,
                            servicio_id=flujo.servicio_id,
                            prestador_id=prestador_final,
                            cita_ignorar_id=cita_activa.id,
                        )

                if cita_ocupada:
                    if horarios_alternativos:
                        lista_horarios = "\n".join(
                            [f"- {h}" for h in horarios_alternativos[:5]]
                        )

                        respuesta = (
                            "Ese horario ya está ocupado.\n\n"
                            "Horarios disponibles para esa fecha:\n"
                            f"{lista_horarios}\n\n"
                            "Por favor escribe uno de esos horarios."
                        )
                    else:
                        respuesta = (
                            "Ese día ya no tiene horarios disponibles.\n"
                            "Por favor indica otra fecha."
                        )

                    guardar_conversacion(
                        db=db,
                        empresa_id=empresa.id,
                        telefono_cliente=telefono_cliente,
                        mensaje=mensaje,
                        respuesta=respuesta,
                        paso="REPROGRAMAR_HORA",
                        nombre=flujo.nombre,
                        fecha=flujo.fecha,
                        servicio_id=flujo.servicio_id,
                    )

                    enviar_respuesta(numero, telefono_cliente, respuesta)
                    return {"status": "HORARIO_OCUPADO"}

                nueva_cita = reprogramar_cita(
                    db=db,
                    cita_anterior=cita_activa,
                    nueva_fecha=flujo.fecha,
                    nueva_hora=nueva_hora,
                    canal="WHATSAPP",
                    prestador_id=prestador_final,
                )

                respuesta = (
                    "✅ Tu cita ha sido reprogramada correctamente\n\n"
                    f"Fecha: {nueva_cita.fecha}\n"
                    f"Hora: {nueva_cita.hora}"
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                )


                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "CITA_REPROGRAMADA"}

        # =========================
        # AGENDAR (arrancado por IA desde un mensaje libre) — continuar
        # =========================
        if flujo and flujo.paso == "AGENDAR_IA":
            _continuar_agendado_ia(
                db=db,
                empresa=empresa,
                numero=numero,
                telefono_cliente=telefono_cliente,
                flujo=flujo,
                respuesta_usuario=mensaje,
            )
            return {"status": "AGENDAR_IA"}

        # =========================
        # PEDIR SERVICIO
        # =========================
        if flujo and flujo.paso == "PEDIR_SERVICIO":
            servicios = (
                db.query(Servicio)
                .filter(
                    Servicio.empresa_id == empresa.id,
                    Servicio.activo == True,
                )
                .all()
            )

            servicio_seleccionado = None

            if mensaje_lower.isdigit():
                indice = int(mensaje_lower) - 1

                if 0 <= indice < len(servicios):
                    servicio_seleccionado = servicios[indice]

            if not servicio_seleccionado:
                respuesta = "No encontré ese servicio. Responde con un número válido."

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_SERVICIO",
                )
            elif empresa.usa_prestadores:
                respuesta = (
                    f"Perfecto, seleccionaste {servicio_seleccionado.nombre}.\n\n"
                    f"¿Deseas atenderte con un {vocab['prestador']} específico o con cualquiera "
                    "disponible?"
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_TIPO_PRESTADOR",
                    servicio_id=servicio_seleccionado.id,
                    prestador_id=None,
                    asignacion_automatica=False,
                )

                enviar_botones_whatsapp(
                    phone_number_id=numero.phone_number_id,
                    token=numero.token,
                    telefono_cliente=telefono_cliente,
                    texto=respuesta,
                    botones=[
                        {"id": "ELEGIR_PRESTADOR", "title": _titulo_boton(f"Elegir {vocab['prestador']}", "Elegir")},
                        {"id": "CUALQUIER_PRESTADOR", "title": _titulo_boton(f"Cualquier {vocab['prestador']}", "Cualquiera")},
                    ],
                )

                return {"status": "PEDIR_TIPO_PRESTADOR"}
            else:
                respuesta = (
                    f"Perfecto, seleccionaste {servicio_seleccionado.nombre}.\n\n"
                    "¿Cuál es tu nombre completo?"
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_NOMBRE",
                    servicio_id=servicio_seleccionado.id,
                )


            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "PEDIR_NOMBRE"}

        # =========================
        # PEDIR TIPO PRESTADOR
        # =========================
        if flujo and flujo.paso == "PEDIR_TIPO_PRESTADOR":
            if mensaje == "CUALQUIER_PRESTADOR":
                respuesta = (
                    f"Perfecto, te atenderá cualquier {vocab['prestador']} disponible.\n\n"
                    "¿Cuál es tu nombre completo?"
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_NOMBRE",
                    servicio_id=flujo.servicio_id,
                    prestador_id=None,
                    asignacion_automatica=True,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "PEDIR_NOMBRE"}

            if mensaje == "ELEGIR_PRESTADOR":
                prestadores = obtener_prestadores_compatibles(
                    db, empresa.id, flujo.servicio_id
                )

                if not prestadores:
                    respuesta = (
                        f"Por el momento no hay {vocab['prestador_plural']} específicos disponibles "
                        f"para ese servicio. Te atenderá cualquier {vocab['prestador']} "
                        "disponible.\n\n¿Cuál es tu nombre completo?"
                    )

                    guardar_conversacion(
                        db=db,
                        empresa_id=empresa.id,
                        telefono_cliente=telefono_cliente,
                        mensaje=mensaje,
                        respuesta=respuesta,
                        paso="PEDIR_NOMBRE",
                        servicio_id=flujo.servicio_id,
                        prestador_id=None,
                        asignacion_automatica=True,
                    )

                    enviar_respuesta(numero, telefono_cliente, respuesta)
                    return {"status": "PEDIR_NOMBRE"}

                filas = [
                    {"id": f"PRESTADOR_{prestador.id}", "title": prestador.nombre}
                    for prestador in prestadores
                ]

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=f"Selecciona un {vocab['prestador']}",
                    paso="PEDIR_PRESTADOR",
                    servicio_id=flujo.servicio_id,
                )

                enviar_lista_whatsapp(
                    phone_number_id=numero.phone_number_id,
                    token=numero.token,
                    telefono_cliente=telefono_cliente,
                    texto=f"Selecciona el {vocab['prestador']} con el que deseas atenderte:",
                    boton_texto=_titulo_boton(f"Ver {vocab['prestador_plural']}", "Ver opciones"),
                    filas=filas,
                )

                return {"status": "PEDIR_PRESTADOR"}

            respuesta = (
                f"¿Deseas atenderte con un {vocab['prestador']} específico o con cualquiera "
                "disponible?"
            )

            enviar_botones_whatsapp(
                phone_number_id=numero.phone_number_id,
                token=numero.token,
                telefono_cliente=telefono_cliente,
                texto=respuesta,
                botones=[
                    {"id": "ELEGIR_PRESTADOR", "title": _titulo_boton(f"Elegir {vocab['prestador']}", "Elegir")},
                    {"id": "CUALQUIER_PRESTADOR", "title": _titulo_boton(f"Cualquier {vocab['prestador']}", "Cualquiera")},
                ],
            )

            return {"status": "PEDIR_TIPO_PRESTADOR"}

        # =========================
        # PEDIR PRESTADOR
        # =========================
        if flujo and flujo.paso == "PEDIR_PRESTADOR":
            prestadores = obtener_prestadores_compatibles(
                db, empresa.id, flujo.servicio_id
            )

            prestador_seleccionado = None

            if mensaje.startswith("PRESTADOR_"):
                try:
                    prestador_id_elegido = int(mensaje.replace("PRESTADOR_", ""))
                except ValueError:
                    prestador_id_elegido = None

                prestador_seleccionado = next(
                    (p for p in prestadores if p.id == prestador_id_elegido), None
                )

            if not prestador_seleccionado:
                respuesta = f"No reconocí ese {vocab['prestador']}. Por favor selecciónalo de la lista."

                filas = [
                    {"id": f"PRESTADOR_{prestador.id}", "title": prestador.nombre}
                    for prestador in prestadores
                ]

                enviar_lista_whatsapp(
                    phone_number_id=numero.phone_number_id,
                    token=numero.token,
                    telefono_cliente=telefono_cliente,
                    texto=respuesta,
                    boton_texto=_titulo_boton(f"Ver {vocab['prestador_plural']}", "Ver opciones"),
                    filas=filas,
                )

                return {"status": "PEDIR_PRESTADOR"}

            respuesta = (
                f"Perfecto, te atenderá {prestador_seleccionado.nombre}.\n\n"
                "¿Cuál es tu nombre completo?"
            )

            guardar_conversacion(
                db=db,
                empresa_id=empresa.id,
                telefono_cliente=telefono_cliente,
                mensaje=mensaje,
                respuesta=respuesta,
                paso="PEDIR_NOMBRE",
                servicio_id=flujo.servicio_id,
                prestador_id=prestador_seleccionado.id,
                asignacion_automatica=False,
            )

            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "PEDIR_NOMBRE"}

        # =========================
        # PEDIR NOMBRE
        # =========================
        if flujo and flujo.paso == "PEDIR_NOMBRE":
            nombre = mensaje.strip()

            respuesta = (
                f"Gracias, {nombre}.\n\n"
                "¿Qué fecha deseas para tu cita?\n"
                "Ejemplo: 25 de junio o 25/06/2026"
            )

            guardar_conversacion(
                db=db,
                empresa_id=empresa.id,
                telefono_cliente=telefono_cliente,
                mensaje=mensaje,
                respuesta=respuesta,
                paso="PEDIR_FECHA",
                nombre=nombre,
                servicio_id=flujo.servicio_id,
            )


            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "PEDIR_FECHA"}

        # =========================
        # PEDIR FECHA
        # =========================
        if flujo and flujo.paso == "PEDIR_FECHA":
            fecha = normalizar_fecha(mensaje.strip())

            if fecha is None:
                contexto_ia = construir_contexto_empresa(db, empresa)
                resultado_ia = interpretar_mensaje(
                    empresa=empresa,
                    contexto=contexto_ia,
                    paso_actual="PEDIR_FECHA",
                    mensaje_usuario=mensaje.strip(),
                )

                if resultado_ia and resultado_ia.fecha:
                    fecha_ia = normalizar_fecha_iso(resultado_ia.fecha) or normalizar_fecha(
                        resultado_ia.fecha
                    )

                    if fecha_ia and not fecha_ya_paso(fecha_ia):
                        fecha = fecha_ia

            if fecha is None:
                respuesta = (
                    "No entendí la fecha.\n"
                    "Por favor escribe una fecha como: 25 de junio o 25/06/2026."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_FECHA",
                    nombre=flujo.nombre,
                    servicio_id=flujo.servicio_id,
                )
                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "PEDIR_FECHA"}

            elif fecha_ya_paso(fecha):
                respuesta = (
                    "La fecha que elegiste ya pasó.\nPor favor indica una fecha futura."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_FECHA",
                    nombre=flujo.nombre,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "PEDIR_FECHA"}

            if empresa.permite_citas_sin_hora:
                respuesta = (
                    f"Perfecto. Registré la fecha {fecha}.\n\n"
                    "¿Deseas elegir una hora específica o agendar sin preferencia de horario?"
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_TIPO_HORA",
                    nombre=flujo.nombre,
                    fecha=fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_botones_whatsapp(
                    phone_number_id=numero.phone_number_id,
                    token=numero.token,
                    telefono_cliente=telefono_cliente,
                    texto=respuesta,
                    botones=[
                        {"id": "HORA_ESPECIFICA", "title": "Hora específica"},
                        {"id": "SIN_HORA_ESPECIFICA", "title": "Sin preferencia"},
                    ],
                )

                return {"status": "PEDIR_TIPO_HORA"}

            respuesta = (
                f"Perfecto. Registré la fecha {fecha}.\n\n¿A qué hora deseas tu cita?"
            )

            guardar_conversacion(
                db=db,
                empresa_id=empresa.id,
                telefono_cliente=telefono_cliente,
                mensaje=mensaje,
                respuesta=respuesta,
                paso="PEDIR_HORA",
                nombre=flujo.nombre,
                fecha=fecha,
                servicio_id=flujo.servicio_id,
            )


            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "PEDIR_HORA"}

        # =========================
        # PEDIR TIPO DE HORA (sin hora específica)
        # =========================
        if flujo and flujo.paso == "PEDIR_TIPO_HORA":
            if mensaje == "SIN_HORA_ESPECIFICA":
                servicio = (
                    db.query(Servicio).filter(Servicio.id == flujo.servicio_id).first()
                )

                try:
                    crear_solicitud_sin_hora(
                        db=db,
                        empresa=empresa,
                        servicio=servicio,
                        fecha=flujo.fecha,
                        nombre=flujo.nombre,
                        telefono=telefono_cliente,
                        canal="WHATSAPP",
                        prestador_id=flujo.prestador_id,
                    )
                except ValueError as error:
                    respuesta = f"No fue posible registrar tu solicitud: {error}."

                    enviar_respuesta(numero, telefono_cliente, respuesta)
                    return {"status": "ERROR_SIN_HORA"}

                respuesta = (
                    f"✅ Registré tu solicitud para el {flujo.fecha} sin hora específica.\n\n"
                    "La empresa te confirmará el horario más adelante."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                enviar_menu_principal(
                    db=db,
                    empresa=empresa,
                    numero=numero,
                    telefono_cliente=telefono_cliente,
                )
                return {"status": "SOLICITUD_SIN_HORA"}

            if mensaje == "HORA_ESPECIFICA":
                respuesta = "¿A qué hora deseas tu cita?"

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "PEDIR_HORA"}

            respuesta = "Por favor selecciona una de las opciones."

            enviar_botones_whatsapp(
                phone_number_id=numero.phone_number_id,
                token=numero.token,
                telefono_cliente=telefono_cliente,
                texto=respuesta,
                botones=[
                    {"id": "HORA_ESPECIFICA", "title": "Hora específica"},
                    {"id": "SIN_HORA_ESPECIFICA", "title": "Sin preferencia"},
                ],
            )

            return {"status": "PEDIR_TIPO_HORA"}

        # =========================
        # PEDIR HORA
        # =========================
        if flujo and flujo.paso == "PEDIR_HORA":
            hora = normalizar_hora(mensaje.strip())

            if hora == "AMBIGUA":
                respuesta = (
                    f"¿Te refieres a las {mensaje.strip()} de la mañana o de la tarde?"
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="ACLARAR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    hora=mensaje.strip(),
                    servicio_id=flujo.servicio_id,
                )


                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "ACLARAR_HORA"}

            if hora is None:
                respuesta = (
                    "No entendí la hora.\n"
                    "Por favor escribe una hora como: 10:00, 3 pm o 5 de la tarde."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "PEDIR_HORA"}

            if hora_ya_paso(flujo.fecha, hora):
                respuesta = "Esa hora ya pasó.\nPor favor indica una hora futura."

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "HORA_YA_PASO"}

            if hora < empresa.horario_inicio or hora > empresa.horario_fin:
                respuesta = (
                    f"Lo siento, nuestro horario de atención es de "
                    f"{empresa.horario_inicio} a {empresa.horario_fin}.\n\n"
                    "Por favor indica otra hora."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "HORARIO_FUERA_DE_RANGO"}

            print("VALIDANDO HORARIO")
            print("EMPRESA:", empresa.id)
            print("FECHA:", flujo.fecha)
            print("HORA:", hora)

            prestador_final = None
            horarios_alternativos = None

            if empresa.usa_prestadores:
                if flujo.prestador_id:
                    cita_ocupada = horario_choca_con_duracion(
                        db=db,
                        empresa_id=empresa.id,
                        fecha=flujo.fecha,
                        hora=hora,
                        servicio_id=flujo.servicio_id,
                        prestador_id=flujo.prestador_id,
                    )

                    if cita_ocupada:
                        horarios_alternativos = obtener_horarios_disponibles(
                            db=db,
                            empresa=empresa,
                            fecha=flujo.fecha,
                            servicio_id=flujo.servicio_id,
                            prestador_id=flujo.prestador_id,
                        )
                    else:
                        prestador_final = flujo.prestador_id
                else:
                    prestador_seleccionado = seleccionar_prestador_automaticamente(
                        db=db,
                        empresa_id=empresa.id,
                        servicio_id=flujo.servicio_id,
                        fecha=flujo.fecha,
                        hora=hora,
                    )

                    if not prestador_seleccionado:
                        horarios_alternativos = obtener_horarios_disponibles(
                            db=db,
                            empresa=empresa,
                            fecha=flujo.fecha,
                            servicio_id=flujo.servicio_id,
                        )
                        cita_ocupada = True
                    else:
                        prestador_final = prestador_seleccionado.id
                        cita_ocupada = False
            else:
                cita_ocupada = horario_choca_con_duracion(
                    db=db,
                    empresa_id=empresa.id,
                    fecha=flujo.fecha,
                    hora=hora,
                    servicio_id=flujo.servicio_id,
                )

                if cita_ocupada:
                    horarios_alternativos = obtener_horarios_disponibles(
                        db=db,
                        empresa=empresa,
                        fecha=flujo.fecha,
                    )

            if cita_ocupada:
                if horarios_alternativos:
                    lista_horarios = "\n".join(
                        [f"- {h}" for h in horarios_alternativos[:5]]
                    )

                    respuesta = (
                        "Ese horario ya está ocupado.\n\n"
                        "Horarios disponibles para esa fecha:\n"
                        f"{lista_horarios}\n\n"
                        "Por favor escribe uno de esos horarios."
                    )
                else:
                    respuesta = (
                        "Ese día ya no tiene horarios disponibles.\n"
                        "Por favor indica otra fecha."
                    )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "HORARIO_OCUPADO"}

            cita = crear_cita(
                db=db,
                nombre=flujo.nombre,
                telefono=telefono_cliente,
                fecha=flujo.fecha,
                hora=hora,
                empresa_id=empresa.id,
                servicio_id=flujo.servicio_id,
                canal="WHATSAPP",
                prestador_id=prestador_final,
            )

            servicio = (
                db.query(Servicio).filter(Servicio.id == flujo.servicio_id).first()
            )
            nombre_servicio = servicio.nombre if servicio else "Servicio"

            respuesta = (
                "✅ Cita agendada correctamente\n\n"
                f"Servicio: {nombre_servicio}\n"
                f"Nombre: {cita.nombre}\n"
                f"Fecha: {cita.fecha}\n"
                f"Hora: {cita.hora}"
            )

            guardar_conversacion(
                db=db,
                empresa_id=empresa.id,
                telefono_cliente=telefono_cliente,
                mensaje=mensaje,
                respuesta=respuesta,
                paso=None,
                nombre=cita.nombre,
                fecha=cita.fecha,
                hora=cita.hora,
                servicio_id=cita.servicio_id,
            )


            enviar_respuesta(numero, telefono_cliente, respuesta)
            enviar_menu_principal(
                db=db,
                empresa=empresa,
                numero=numero,
                telefono_cliente=telefono_cliente,
            )
            return {"status": "CITA_AGENDADA", "cita_id": cita.id}

        # =========================
        # ACLARAR HORA
        # =========================
        if flujo and flujo.paso == "ACLARAR_HORA":
            hora_original = flujo.hora
            respuesta_usuario = mensaje_lower

            try:
                numero_hora = int("".join(filter(str.isdigit, hora_original)))
            except Exception:
                respuesta = (
                    "No pude identificar la hora. Por favor escribe la hora nuevamente."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "PEDIR_HORA"}

            if (
                "tarde" in respuesta_usuario
                or "noche" in respuesta_usuario
                or "pm" in respuesta_usuario
            ):
                hora = f"{numero_hora + 12:02d}:00"
            else:
                hora = f"{numero_hora:02d}:00"

            if hora_ya_paso(flujo.fecha, hora):
                respuesta = "Esa hora ya pasó.\nPor favor indica una hora futura."

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "HORA_YA_PASO"}

            if hora < empresa.horario_inicio or hora > empresa.horario_fin:
                respuesta = (
                    f"Lo siento, nuestro horario de atención es de "
                    f"{empresa.horario_inicio} a {empresa.horario_fin}.\n\n"
                    "Por favor indica otra hora."
                )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "HORARIO_FUERA_DE_RANGO"}

            prestador_final = None
            horarios_alternativos = None

            if empresa.usa_prestadores:
                if flujo.prestador_id:
                    cita_ocupada = horario_choca_con_duracion(
                        db=db,
                        empresa_id=empresa.id,
                        fecha=flujo.fecha,
                        hora=hora,
                        servicio_id=flujo.servicio_id,
                        prestador_id=flujo.prestador_id,
                    )

                    if cita_ocupada:
                        horarios_alternativos = obtener_horarios_disponibles(
                            db=db,
                            empresa=empresa,
                            fecha=flujo.fecha,
                            servicio_id=flujo.servicio_id,
                            prestador_id=flujo.prestador_id,
                        )
                    else:
                        prestador_final = flujo.prestador_id
                else:
                    prestador_seleccionado = seleccionar_prestador_automaticamente(
                        db=db,
                        empresa_id=empresa.id,
                        servicio_id=flujo.servicio_id,
                        fecha=flujo.fecha,
                        hora=hora,
                    )

                    if not prestador_seleccionado:
                        horarios_alternativos = obtener_horarios_disponibles(
                            db=db,
                            empresa=empresa,
                            fecha=flujo.fecha,
                            servicio_id=flujo.servicio_id,
                        )
                        cita_ocupada = True
                    else:
                        prestador_final = prestador_seleccionado.id
                        cita_ocupada = False
            else:
                cita_ocupada = horario_choca_con_duracion(
                    db=db,
                    empresa_id=empresa.id,
                    fecha=flujo.fecha,
                    hora=hora,
                    servicio_id=flujo.servicio_id,
                )

                if cita_ocupada:
                    horarios_alternativos = obtener_horarios_disponibles(
                        db=db,
                        empresa=empresa,
                        fecha=flujo.fecha,
                    )

            if cita_ocupada:
                if horarios_alternativos:
                    lista_horarios = "\n".join(
                        [f"- {h}" for h in horarios_alternativos[:5]]
                    )

                    respuesta = (
                        "Ese horario ya está ocupado.\n\n"
                        "Horarios disponibles para esa fecha:\n"
                        f"{lista_horarios}\n\n"
                        "Por favor escribe uno de esos horarios."
                    )
                else:
                    respuesta = (
                        "Ese día ya no tiene horarios disponibles.\n"
                        "Por favor indica otra fecha."
                    )

                guardar_conversacion(
                    db=db,
                    empresa_id=empresa.id,
                    telefono_cliente=telefono_cliente,
                    mensaje=mensaje,
                    respuesta=respuesta,
                    paso="PEDIR_HORA",
                    nombre=flujo.nombre,
                    fecha=flujo.fecha,
                    servicio_id=flujo.servicio_id,
                )

                enviar_respuesta(numero, telefono_cliente, respuesta)
                return {"status": "HORARIO_OCUPADO"}

            cita = crear_cita(
                db=db,
                nombre=flujo.nombre,
                telefono=telefono_cliente,
                fecha=flujo.fecha,
                hora=hora,
                empresa_id=empresa.id,
                servicio_id=flujo.servicio_id,
                canal="WHATSAPP",
                prestador_id=prestador_final,
            )

            respuesta = (
                "✅ Cita agendada correctamente\n\n"
                f"Nombre: {cita.nombre}\n"
                f"Fecha: {cita.fecha}\n"
                f"Hora: {cita.hora}"
            )

            guardar_conversacion(
                db=db,
                empresa_id=empresa.id,
                telefono_cliente=telefono_cliente,
                mensaje=mensaje,
                respuesta=respuesta,
                paso=None,
                nombre=cita.nombre,
                fecha=cita.fecha,
                hora=cita.hora,
                servicio_id=cita.servicio_id,
            )


            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "CITA_AGENDADA", "cita_id": cita.id}

        # =========================
        # OPENAI NORMAL
        # =========================
        contexto_ia = construir_contexto_empresa(db, empresa)
        resultado_ia = (
            interpretar_mensaje(
                empresa=empresa,
                contexto=contexto_ia,
                paso_actual=None,
                mensaje_usuario=mensaje.strip(),
            )
            if tipo_mensaje == "text" and mensaje.strip()
            else None
        )

        if resultado_ia and not resultado_ia.dentro_del_dominio:
            respuesta = resultado_ia.mensaje_respuesta or (
                f"Solo puedo ayudarte con información, servicios y citas de {empresa.nombre}."
            )

            enviar_respuesta(numero, telefono_cliente, respuesta)

            return {"status": "FUERA_DE_CONTEXTO"}

        if resultado_ia and resultado_ia.intencion == "AGENDAR":
            iniciado = iniciar_agendado_desde_ia(
                db=db,
                empresa=empresa,
                numero=numero,
                telefono_cliente=telefono_cliente,
                resultado_ia=resultado_ia,
                mensaje_original=mensaje,
            )

            if iniciado:
                return {"status": "AGENDAR_DESDE_IA"}

            respuesta = (
                "¡Con gusto! Para comenzar a agendar tu cita escribe "
                '"agendar cita" o usa el botón del menú.'
            )

            enviar_respuesta(numero, telefono_cliente, respuesta)
            enviar_menu_principal(
                db=db,
                empresa=empresa,
                numero=numero,
                telefono_cliente=telefono_cliente,
            )

            return {"status": "SUGERIR_AGENDAR"}

        if (
            resultado_ia
            and resultado_ia.dentro_del_dominio
            and resultado_ia.intencion != "NO_ENTENDIDO"
        ):
            respuesta = responder_pregunta_empresa(
                empresa=empresa,
                contexto=contexto_ia,
                mensaje_usuario=mensaje.strip(),
            )

            enviar_respuesta(numero, telefono_cliente, respuesta)
            enviar_menu_principal(
                db=db,
                empresa=empresa,
                numero=numero,
                telefono_cliente=telefono_cliente,
            )

            return {"status": "RESPUESTA_IA"}

        # RESPUESTA DEFAULT SIN OPENAI (o si OpenAI no resolvió nada útil)
        respuesta = (
            "No entendí tu mensaje.\n\n"
            "Por favor usa el menú para continuar."
        )

        enviar_respuesta(numero, telefono_cliente, respuesta)

        enviar_menu_principal(
            db=db,
            empresa=empresa,
            numero=numero,
            telefono_cliente=telefono_cliente,
        )

        return {"status": "MENSAJE_NO_ENTENDIDO"}

    except Exception as e:
        print("Evento ignorado:", str(e))
        return {"status": "evento ignorado"}
