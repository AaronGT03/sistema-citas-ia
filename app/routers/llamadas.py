from fastapi import APIRouter, Depends, Form
from sqlalchemy.orm import Session
from fastapi.responses import Response

from app.database import get_db
from app.models import Cita, Conversacion, Empresa, Servicio, Prestador
from app.utils import (
    normalizar_fecha,
    normalizar_hora,
    normalizar_telefono_mexico,
    normalizar_fecha_iso,
    normalizar_hora_valida,
)
from app.services.citas_service import (
    existe_cita_en_horario,
    crear_cita,
    cancelar_cita,
    reprogramar_cita,
    fecha_ya_paso,
    hora_ya_paso,
    horario_choca_con_duracion,
    obtener_horarios_disponibles,
    obtener_prestadores_compatibles,
    seleccionar_prestador_automaticamente,
    crear_solicitud_sin_hora,
    construir_contexto_empresa,
    resolver_por_nombre,
)
from app.services.openai_service import interpretar_mensaje, responder_pregunta_empresa
from app.services.voz_service import construir_bloque_voz, construir_bloques_voz

router = APIRouter()



def respuesta_horario_ocupado():
    voz_1 = construir_bloque_voz(
        "Ya existe una cita programada para esa fecha y hora. Por favor seleccione otro horario."
    )
    twiml = f"""
<Response>
    {voz_1}
</Response>
"""
    return Response(content=twiml, media_type="application/xml")


def respuesta_prestador_ocupado(alternativas=None):
    voz_alternativas = ""

    if alternativas:
        horas_texto = ", ".join(alternativas[:3])
        voz_alternativas = construir_bloque_voz(
            f"Los horarios disponibles más cercanos son: {horas_texto}. "
            "Por favor vuelva a llamar para agendar en uno de esos horarios."
        )

    voz_1 = construir_bloque_voz("Ese profesional no está disponible en ese horario.")

    twiml = f"""
<Response>
    {voz_1}
{voz_alternativas}
</Response>
"""
    return Response(content=twiml, media_type="application/xml")


def respuesta_sin_prestadores(alternativas=None):
    voz_alternativas = ""

    if alternativas:
        horas_texto = ", ".join(alternativas[:3])
        voz_alternativas = construir_bloque_voz(
            f"Los horarios disponibles más cercanos son: {horas_texto}. "
            "Por favor vuelva a llamar para agendar en uno de esos horarios."
        )

    voz_1 = construir_bloque_voz("No hay profesionales disponibles en ese horario.")

    twiml = f"""
<Response>
    {voz_1}
{voz_alternativas}
</Response>
"""
    return Response(content=twiml, media_type="application/xml")


# Intenciones que significan "el cliente está preguntando algo" en lugar de
# responder el dato que el flujo le pidió. FUERA_DE_CONTEXTO se incluye para
# responder con el mensaje de respaldo y retomar el flujo en vez de colgar.
INTENCIONES_CONSULTA = (
    "SALUDO",
    "INFORMACION_EMPRESA",
    "CONSULTAR_SERVICIOS",
    "CONSULTAR_PRECIOS",
    "CONSULTAR_HORARIOS",
    "CONSULTAR_UBICACION",
    "CONSULTAR_PROMOCIONES",
    "FUERA_DE_CONTEXTO",
)


def responder_consulta_en_flujo(
    db: Session,
    empresa: Empresa,
    mensaje_usuario: str,
    action: str,
    pregunta_pendiente: str,
    paso_actual: str | None = None,
    resultado_ia=None,
    contexto: dict | None = None,
):
    """Los clientes no conocen el flujo: a mitad de la captura pueden preguntar
    precios, servicios, prestadores, etc. Si el mensaje es una consulta y no la
    respuesta al paso pendiente, la contesta con la IA y repite la pregunta del
    paso en el mismo Gather (el estado de la conversación no se toca). Devuelve
    None si el mensaje no es una consulta, para que el llamador siga su flujo
    normal. `action` debe venir ya escapado para XML (usar &amp;)."""

    if not empresa or not mensaje_usuario or not mensaje_usuario.strip():
        return None

    if contexto is None:
        contexto = construir_contexto_empresa(db, empresa)

    if resultado_ia is None:
        resultado_ia = interpretar_mensaje(
            empresa=empresa,
            contexto=contexto,
            paso_actual=paso_actual,
            mensaje_usuario=mensaje_usuario,
        )

    if not resultado_ia or resultado_ia.intencion not in INTENCIONES_CONSULTA:
        return None

    if resultado_ia.dentro_del_dominio:
        texto = responder_pregunta_empresa(empresa, contexto, mensaje_usuario)
    else:
        texto = f"Solo puedo ayudarte con información, servicios y citas de {empresa.nombre}."

    voz_1, voz_2 = construir_bloques_voz(
        texto, f"Continuemos con su cita. {pregunta_pendiente}", empresa_id=empresa.id
    )

    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="{action}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

        {voz_2}

    </Gather>
</Response>
"""
    return Response(content=twiml, media_type="application/xml")


def respuesta_disponibilidad_en_flujo(
    db: Session,
    empresa: Empresa,
    fecha: str,
    servicio_id: int | None,
    prestador_id: int | None,
    action: str,
    pregunta_pendiente: str,
):
    """Cuando en el paso de la hora el cliente pregunta qué horarios hay
    ("¿a qué hora tienes disponible?"), responde con la disponibilidad real
    de la fecha ya capturada y vuelve a preguntar la hora sin perder el flujo."""

    horarios = obtener_horarios_disponibles(
        db=db,
        empresa=empresa,
        fecha=fecha,
        servicio_id=servicio_id,
        prestador_id=prestador_id,
    )

    if horarios:
        horas_texto = ", ".join(horarios[:6])
        texto = f"Para el día {fecha} tengo disponibles estos horarios: {horas_texto}."
    else:
        texto = (
            f"Por el momento no tengo horarios disponibles para el día {fecha}. "
            "Puede volver a llamar para intentar con otra fecha."
        )

    voz_1, voz_2 = construir_bloques_voz(texto, pregunta_pendiente, empresa_id=empresa.id)

    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="{action}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

        {voz_2}

    </Gather>
</Response>
"""
    return Response(content=twiml, media_type="application/xml")


# =====================================================================
# Arranque del flujo de agendado a partir de datos ya extraídos por
# OpenAI (frase natural completa tipo "quiero un corte con Jeremy hoy a
# las 4"). Reutiliza exactamente las mismas funciones de validación de
# app/services/citas_service.py que usa el flujo determinístico paso a
# paso — nunca crea una cita sin pasar por esas validaciones. El estado
# de qué falta se guarda en Conversacion.paso="AGENDAR_IA", usando
# Conversacion.mensaje (que este archivo no usa para nada más) como
# marcador interno de qué sub-pregunta está pendiente.
# =====================================================================

def _validar_y_guardar_hora_ia(db: Session, empresa: Empresa, flujo: Conversacion, hora: str, telefono: str):
    """Valida una hora ya normalizada contra hora_ya_paso y el horario de
    atención de la empresa. Si es válida, la guarda en flujo y devuelve
    None (el llamador continúa con el flujo). Si no, devuelve el TwiML de
    reintento correspondiente."""

    if hora_ya_paso(flujo.fecha, hora):
        voz_1 = construir_bloque_voz(
            "Esa hora ya pasó. Por favor indique una hora futura.", empresa_id=empresa.id
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST">

        {voz_1}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    if hora < empresa.horario_inicio or hora > empresa.horario_fin:
        voz_1 = construir_bloque_voz(
            f"Lo sentimos. El horario de atención es de {empresa.horario_inicio} a "
            f"{empresa.horario_fin}. Por favor indique otra hora.",
            empresa_id=empresa.id,
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    flujo.hora = hora
    flujo.mensaje = None
    db.commit()

    return None


def _preguntar_servicio_ia_llamada(db: Session, empresa: Empresa, telefono: str, flujo: Conversacion):
    """Pregunta por el servicio cuando la IA reconoció otros datos del
    mensaje inicial (ej. el prestador) pero no pudo resolver el servicio
    contra el catálogo real. Mantiene la conversación dentro del flujo
    AGENDAR_IA para no perder lo que ya se sabe."""

    flujo.mensaje = "ESPERANDO_SERVICIO"
    db.commit()

    servicios = (
        db.query(Servicio)
        .filter(Servicio.empresa_id == empresa.id, Servicio.activo == True)
        .order_by(Servicio.id)
        .all()
    )

    textos_servicios = [f"{i}. {servicio.nombre}" for i, servicio in enumerate(servicios, start=1)]

    voz_pregunta, voz_instruccion, *bloques_servicios = construir_bloques_voz(
        "¿Qué servicio desea agendar?",
        "Presione en su teléfono el número del servicio que desea.",
        *textos_servicios,
        empresa_id=empresa.id,
    )

    lista_servicios = "\n        ".join(bloques_servicios)

    twiml = f"""
<Response>
    <Gather
    input="dtmf"
    numDigits="1"
    action="/agendar-ia-continuar?telefono={telefono}"
    method="POST"
    timeout="10">

        {voz_pregunta}

        {lista_servicios}

        {voz_instruccion}

    </Gather>
</Response>
"""
    return Response(content=twiml, media_type="application/xml")


def _preguntar_siguiente_o_crear_ia(db: Session, empresa: Empresa, telefono: str, flujo: Conversacion, servicio: Servicio | None):
    """Con el estado actual de flujo (nombre/fecha/hora/prestador ya
    conocidos o no), decide qué preguntar a continuación en el mismo
    orden que el flujo determinístico, o crea la cita si ya se sabe todo."""

    if not flujo.servicio_id:
        return _preguntar_servicio_ia_llamada(db, empresa, telefono, flujo)

    if servicio is None:
        servicio = db.query(Servicio).filter(Servicio.id == flujo.servicio_id).first()

    if empresa.usa_prestadores and not flujo.prestador_id and not flujo.asignacion_automatica:
        flujo.mensaje = "ESPERANDO_TIPO_PRESTADOR"
        db.commit()

        voz_1 = construir_bloque_voz(
            f"Para su {servicio.nombre}, ¿desea atenderse con un profesional específico o con "
            "cualquiera disponible? Presione 1 para elegir un profesional. Presione 2 para "
            "atenderse con cualquier profesional disponible.",
            empresa_id=empresa.id,
        )
        twiml = f"""
<Response>
    <Gather
        input="dtmf"
        numDigits="1"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="10">

        {voz_1}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    if not flujo.nombre:
        flujo.mensaje = "ESPERANDO_NOMBRE"
        db.commit()

        voz_1 = construir_bloque_voz("¿Cuál es su nombre completo?", empresa_id=empresa.id)
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    if not flujo.fecha:
        flujo.mensaje = "ESPERANDO_FECHA"
        db.commit()

        voz_1 = construir_bloque_voz(
            f"Gracias {flujo.nombre}. ¿Qué fecha desea para su cita?", empresa_id=empresa.id
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    if not flujo.hora and not flujo.sin_hora_especifica:
        if empresa.permite_citas_sin_hora:
            flujo.mensaje = "ESPERANDO_TIPO_HORA"
            db.commit()

            voz_1 = construir_bloque_voz(
                "Puede elegir una hora específica o indicar que no tiene preferencia de horario. "
                "Presione 1 para elegir una hora específica. Presione 2 para agendar sin hora "
                "específica.",
                empresa_id=empresa.id,
            )
            twiml = f"""
<Response>
    <Gather
        input="dtmf"
        numDigits="1"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="10">

        {voz_1}

    </Gather>
</Response>
"""
        else:
            flujo.mensaje = "ESPERANDO_HORA"
            db.commit()

            voz_1 = construir_bloque_voz("Perfecto. ¿A qué hora desea la cita?", empresa_id=empresa.id)
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    # ---- Ya se conoce todo lo necesario: crear la cita ----

    if flujo.sin_hora_especifica:
        try:
            crear_solicitud_sin_hora(
                db=db,
                empresa=empresa,
                servicio=servicio,
                fecha=flujo.fecha,
                nombre=flujo.nombre,
                telefono=telefono,
                canal="LLAMADA",
                prestador_id=flujo.prestador_id,
            )
        except ValueError as error:
            db.delete(flujo)
            db.commit()

            voz_1 = construir_bloque_voz(
                f"No fue posible registrar su solicitud: {error}.", empresa_id=empresa.id
            )
            twiml = f"""
<Response>
    {voz_1}
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

        fecha_confirmada = flujo.fecha

        db.delete(flujo)
        db.commit()

        voz_1 = construir_bloque_voz(
            f"Perfecto. Registraré su solicitud para el día {fecha_confirmada} sin una hora "
            "específica. La empresa podrá asignar el horario posteriormente. Gracias por usar "
            "nuestro sistema de citas.",
            empresa_id=empresa.id,
        )
        twiml = f"""
<Response>
    {voz_1}
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

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
        if flujo.prestador_id:
            respuesta_final = respuesta_prestador_ocupado(horarios_alternativos)
        elif empresa.usa_prestadores:
            respuesta_final = respuesta_sin_prestadores(horarios_alternativos)
        else:
            respuesta_final = respuesta_horario_ocupado()

        db.delete(flujo)
        db.commit()

        return respuesta_final

    cita = crear_cita(
        db=db,
        nombre=flujo.nombre,
        telefono=telefono,
        fecha=flujo.fecha,
        hora=flujo.hora,
        empresa_id=empresa.id,
        servicio_id=servicio.id,
        canal="LLAMADA",
        prestador_id=prestador_final,
    )

    db.delete(flujo)
    db.commit()

    voz_1 = construir_bloque_voz(
        f"Perfecto {cita.nombre}. Su cita fue agendada para el día {cita.fecha} a las "
        f"{cita.hora} horas. Gracias por usar nuestro sistema de citas.",
        empresa_id=empresa.id,
    )
    twiml = f"""
<Response>
    {voz_1}
</Response>
"""
    return Response(content=twiml, media_type="application/xml")


def _continuar_agendado_ia_llamada(
    db: Session,
    empresa: Empresa,
    telefono: str,
    flujo: Conversacion,
    digits: str,
    speech: str,
):
    servicio = db.query(Servicio).filter(Servicio.id == flujo.servicio_id).first()
    marcador = flujo.mensaje

    if marcador == "ESPERANDO_SERVICIO":
        servicios = (
            db.query(Servicio)
            .filter(Servicio.empresa_id == empresa.id, Servicio.activo == True)
            .order_by(Servicio.id)
            .all()
        )

        opcion = digits.strip()
        servicio_elegido = None

        if opcion.isdigit():
            indice = int(opcion) - 1

            if 0 <= indice < len(servicios):
                servicio_elegido = servicios[indice]

        if not servicio_elegido:
            textos_servicios = [f"{i}. {s.nombre}" for i, s in enumerate(servicios, start=1)]

            voz_error, *bloques_servicios = construir_bloques_voz(
                "No entendí el servicio. Por favor presione un número válido.",
                *textos_servicios,
                empresa_id=empresa.id,
            )

            lista_servicios = "\n            ".join(bloques_servicios)

            twiml = f"""
<Response>
    <Gather
    input="dtmf"
    numDigits="1"
    action="/agendar-ia-continuar?telefono={telefono}"
    method="POST"
    timeout="10">

        {voz_error}

        {lista_servicios}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

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
        opcion = digits.strip()

        if opcion == "2":
            flujo.asignacion_automatica = True
            flujo.mensaje = None
            db.commit()
        elif opcion == "1":
            prestadores = obtener_prestadores_compatibles(db, empresa.id, servicio.id)

            if not prestadores:
                flujo.asignacion_automatica = True
                flujo.mensaje = None
                db.commit()
            else:
                textos_prestadores = [f"{i}. {p.nombre}" for i, p in enumerate(prestadores, start=1)]

                voz_intro, voz_instruccion, *bloques_prestadores = construir_bloques_voz(
                    "Seleccione uno de los siguientes profesionales.",
                    "Presione en su teléfono el número del profesional que desea.",
                    *textos_prestadores,
                    empresa_id=empresa.id,
                )

                lista_prestadores = "\n            ".join(bloques_prestadores)

                flujo.mensaje = "ESPERANDO_PRESTADOR_ESPECIFICO"
                db.commit()

                twiml = f"""
<Response>
    <Gather
    input="dtmf"
    numDigits="1"
    action="/agendar-ia-continuar?telefono={telefono}"
    method="POST"
    timeout="10">

        {voz_intro}

        {lista_prestadores}

        {voz_instruccion}

    </Gather>
</Response>
"""
                return Response(content=twiml, media_type="application/xml")
        else:
            voz_1 = construir_bloque_voz(
                "No entendí su respuesta. Presione 1 para elegir un profesional. Presione 2 "
                "para atenderse con cualquier profesional disponible.",
                empresa_id=empresa.id,
            )
            twiml = f"""
<Response>
    <Gather
        input="dtmf"
        numDigits="1"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="10">

        {voz_1}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

    elif marcador == "ESPERANDO_PRESTADOR_ESPECIFICO":
        prestadores = obtener_prestadores_compatibles(db, empresa.id, servicio.id)
        opcion = digits.strip()
        prestador_seleccionado = None

        if opcion.isdigit():
            indice = int(opcion) - 1

            if 0 <= indice < len(prestadores):
                prestador_seleccionado = prestadores[indice]

        if not prestador_seleccionado:
            textos_prestadores = [f"{i}. {p.nombre}" for i, p in enumerate(prestadores, start=1)]

            voz_error, *bloques_prestadores = construir_bloques_voz(
                "No encontré ese profesional. Por favor presione un número válido.",
                *textos_prestadores,
                empresa_id=empresa.id,
            )

            lista_prestadores = "\n            ".join(bloques_prestadores)

            twiml = f"""
<Response>
    <Gather
    input="dtmf"
    numDigits="1"
    action="/agendar-ia-continuar?telefono={telefono}"
    method="POST"
    timeout="10">

        {voz_error}

        {lista_prestadores}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

        flujo.prestador_id = prestador_seleccionado.id
        flujo.asignacion_automatica = False
        flujo.mensaje = None
        db.commit()

    elif marcador == "ESPERANDO_NOMBRE":
        nombre_limpio = speech.strip()

        if not nombre_limpio:
            voz_1 = construir_bloque_voz("¿Cuál es su nombre completo?", empresa_id=empresa.id)
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

        respuesta_consulta = responder_consulta_en_flujo(
            db=db,
            empresa=empresa,
            mensaje_usuario=nombre_limpio,
            action=f"/agendar-ia-continuar?telefono={telefono}",
            pregunta_pendiente="¿Cuál es su nombre completo?",
            paso_actual="PEDIR_NOMBRE",
        )

        if respuesta_consulta is not None:
            return respuesta_consulta

        flujo.nombre = nombre_limpio
        flujo.mensaje = None
        db.commit()

    elif marcador == "ESPERANDO_FECHA":
        fecha = normalizar_fecha(speech.strip())

        contexto = None
        resultado_ia = None

        if fecha is None:
            contexto = construir_contexto_empresa(db, empresa)
            resultado_ia = interpretar_mensaje(
                empresa=empresa,
                contexto=contexto,
                paso_actual="PEDIR_FECHA",
                mensaje_usuario=speech.strip(),
            )

            if resultado_ia and resultado_ia.fecha:
                fecha_ia = normalizar_fecha_iso(resultado_ia.fecha) or normalizar_fecha(
                    resultado_ia.fecha
                )

                if fecha_ia and not fecha_ya_paso(fecha_ia):
                    fecha = fecha_ia

        if fecha is None:
            respuesta_consulta = responder_consulta_en_flujo(
                db=db,
                empresa=empresa,
                mensaje_usuario=speech.strip(),
                action=f"/agendar-ia-continuar?telefono={telefono}",
                pregunta_pendiente="¿Qué fecha desea para su cita?",
                resultado_ia=resultado_ia,
                contexto=contexto,
            )

            if respuesta_consulta is not None:
                return respuesta_consulta

            voz_1 = construir_bloque_voz(
                "No entendí la fecha. Por favor diga una fecha como quince de junio, mañana o "
                "pasado mañana.",
                empresa_id=empresa.id,
            )
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

        if fecha_ya_paso(fecha):
            voz_1 = construir_bloque_voz(
                "La fecha indicada ya pasó. Por favor indique una fecha futura.", empresa_id=empresa.id
            )
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST">

        {voz_1}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

        flujo.fecha = fecha
        flujo.mensaje = None
        db.commit()

    elif marcador == "ESPERANDO_TIPO_HORA":
        opcion = digits.strip()

        if opcion == "2":
            flujo.sin_hora_especifica = True
            flujo.mensaje = None
            db.commit()
        elif opcion == "1":
            flujo.mensaje = "ESPERANDO_HORA"
            db.commit()

            voz_1 = construir_bloque_voz("¿A qué hora desea la cita?", empresa_id=empresa.id)
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")
        else:
            voz_1 = construir_bloque_voz(
                "No entendí su respuesta. Presione 1 para elegir una hora específica. Presione "
                "2 para agendar sin hora específica.",
                empresa_id=empresa.id,
            )
            twiml = f"""
<Response>
    <Gather
        input="dtmf"
        numDigits="1"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="10">

        {voz_1}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

    elif marcador == "ESPERANDO_HORA_ACLARACION":
        respuesta_consulta = responder_consulta_en_flujo(
            db=db,
            empresa=empresa,
            mensaje_usuario=speech.strip(),
            action=f"/agendar-ia-continuar?telefono={telefono}",
            pregunta_pendiente=f"¿Se refiere a las {flujo.hora} de la mañana o de la tarde?",
            paso_actual="ACLARAR_HORA",
        )

        if respuesta_consulta is not None:
            return respuesta_consulta

        hora_original = flujo.hora or ""
        respuesta_texto = speech.lower().strip()

        try:
            numero_hora = int("".join(filter(str.isdigit, hora_original)))
        except ValueError:
            flujo.mensaje = "ESPERANDO_HORA"
            flujo.hora = None
            db.commit()

            voz_1 = construir_bloque_voz(
                "No pude identificar la hora. ¿A qué hora desea la cita?", empresa_id=empresa.id
            )
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

        if "tarde" in respuesta_texto or "noche" in respuesta_texto:
            hora_final = f"{numero_hora + 12:02d}:00"
        else:
            hora_final = f"{numero_hora:02d}:00"

        resultado_hora = _validar_y_guardar_hora_ia(db, empresa, flujo, hora_final, telefono)

        if resultado_hora is not None:
            return resultado_hora

    elif marcador == "ESPERANDO_HORA":
        hora = normalizar_hora(speech.strip())

        if hora == "AMBIGUA":
            flujo.hora = speech.strip()
            flujo.mensaje = "ESPERANDO_HORA_ACLARACION"
            db.commit()

            voz_1 = construir_bloque_voz(
                f"¿Se refiere a las {speech} de la mañana o de la tarde?", empresa_id=empresa.id
            )
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

        if hora is None:
            contexto = construir_contexto_empresa(db, empresa)
            resultado_ia = interpretar_mensaje(
                empresa=empresa,
                contexto=contexto,
                paso_actual="PEDIR_HORA",
                mensaje_usuario=speech.strip(),
            )

            if resultado_ia and resultado_ia.hora:
                hora_ia = normalizar_hora_valida(resultado_ia.hora)

                if hora_ia:
                    hora = hora_ia

            if hora is None:
                if resultado_ia and resultado_ia.intencion in ("CONSULTAR_HORARIOS", "ELEGIR_HORA"):
                    return respuesta_disponibilidad_en_flujo(
                        db=db,
                        empresa=empresa,
                        fecha=flujo.fecha,
                        servicio_id=flujo.servicio_id,
                        prestador_id=flujo.prestador_id,
                        action=f"/agendar-ia-continuar?telefono={telefono}",
                        pregunta_pendiente="¿A qué hora desea la cita?",
                    )

                respuesta_consulta = responder_consulta_en_flujo(
                    db=db,
                    empresa=empresa,
                    mensaje_usuario=speech.strip(),
                    action=f"/agendar-ia-continuar?telefono={telefono}",
                    pregunta_pendiente="¿A qué hora desea la cita?",
                    resultado_ia=resultado_ia,
                    contexto=contexto,
                )

                if respuesta_consulta is not None:
                    return respuesta_consulta

                voz_1 = construir_bloque_voz(
                    "No entendí la hora. Por favor diga una hora como diez de la mañana, cinco "
                    "de la tarde o tres y media.",
                    empresa_id=empresa.id,
                )
                twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/agendar-ia-continuar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
                return Response(content=twiml, media_type="application/xml")

        resultado_hora = _validar_y_guardar_hora_ia(db, empresa, flujo, hora, telefono)

        if resultado_hora is not None:
            return resultado_hora

    return _preguntar_siguiente_o_crear_ia(db, empresa, telefono, flujo, servicio)


def iniciar_agendado_desde_ia_llamada(
    db: Session,
    empresa: Empresa,
    empresa_id: int,
    telefono: str,
    resultado_ia,
    mensaje_original: str,
):
    """Punto de entrada desde /procesar-agenda. Devuelve None si no pudo
    resolver ni siquiera el servicio contra el catálogo real de la
    empresa (el llamador debe usar el TwiML genérico de "no entendí").
    Si devuelve una Response, ya es la respuesta TwiML completa (con la
    siguiente pregunta que falte, o la cita ya creada)."""

    servicios_activos = (
        db.query(Servicio)
        .filter(Servicio.empresa_id == empresa_id, Servicio.activo == True)
        .all()
    )

    servicio = resolver_por_nombre(resultado_ia.servicio, servicios_activos)

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
            prestador = resolver_por_nombre(resultado_ia.prestador, candidatos)

            if prestador:
                prestador_id = prestador.id

    datos_utiles = bool(
        servicio or nombre or fecha or hora or prestador_id or asignacion_automatica
    )

    if not datos_utiles:
        return None

    db.query(Conversacion).filter(Conversacion.telefono == telefono).delete()
    db.commit()

    flujo = Conversacion(
        telefono=telefono,
        empresa_id=empresa_id,
        canal="LLAMADA",
        paso="AGENDAR_IA",
        nombre=nombre,
        fecha=fecha,
        hora=hora,
        servicio_id=servicio.id if servicio else None,
        prestador_id=prestador_id,
        asignacion_automatica=asignacion_automatica,
        sin_hora_especifica=sin_hora,
    )

    db.add(flujo)
    db.commit()
    db.refresh(flujo)

    return _preguntar_siguiente_o_crear_ia(db, empresa, telefono, flujo, servicio)


@router.post("/llamada")
async def llamada(
    From: str = Form(...), To: str = Form(...), db: Session = Depends(get_db)
):
    print(f"Llamada recibida de: {From}")
    print(f"Número Twilio recibido: {To}")

    telefono_cliente = normalizar_telefono_mexico(From)
    telefono_empresa = To.replace(" ", "")

    telefono_url = telefono_cliente

    empresa = (
        db.query(Empresa).filter(Empresa.telefono_twilio == telefono_empresa).first()
    )

    if not empresa:
        voz_1 = construir_bloque_voz("Este número no tiene una empresa configurada.")
        twiml = f"""
<Response>
    {voz_1}
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    print(f"Empresa encontrada: {empresa.nombre}")

    cita = (
        db.query(Cita)
        .filter(Cita.telefono == telefono_cliente)
        .filter(Cita.empresa_id == empresa.id)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

    if cita:
        voz_1, voz_2, voz_3, voz_4 = construir_bloques_voz(
            f"Hola {cita.nombre}. Encontré una cita agendada para usted en {empresa.nombre}.",
            f"Su cita es el día {cita.fecha} a las {cita.hora}.",
            "Diga cancelar o reprogramar.",
            "No recibí ninguna respuesta. Intente nuevamente.",
            empresa_id=empresa.id,
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/procesar-cita?telefono={telefono_url}&amp;empresa_id={empresa.id}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

        {voz_2}

        {voz_3}
    </Gather>

    {voz_4}
</Response>
"""
    else:
        voz_1, voz_2, voz_3 = construir_bloques_voz(
            f"Hola, bienvenido a {empresa.nombre}.",
            "No encontré ninguna cita activa.",
            "Si desea agendar una cita diga agendar.",
            empresa_id=empresa.id,
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/procesar-agenda?telefono={telefono_url}&amp;empresa_id={empresa.id}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

        {voz_2}

        {voz_3}
    </Gather>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/procesar-cita")
async def procesar_cita(
    telefono: str,
    empresa_id: int,
    SpeechResult: str = Form(""),
    db: Session = Depends(get_db),
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    respuesta = SpeechResult.lower().strip()
    print("RESPUESTA DETECTADA:")
    print(respuesta)

    cita = (
        db.query(Cita)
        .filter(Cita.telefono == telefono)
        .filter(Cita.empresa_id == empresa_id)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

    if not cita:
        voz_1 = construir_bloque_voz("No encontré ninguna cita activa.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    if "cancel" in respuesta:
        cancelar_cita(db, cita)

        voz_1 = construir_bloque_voz(
            "Su cita ha sido cancelada correctamente. Si desea agendar una nueva cita, por "
            "favor vuelva a llamar.",
            empresa_id=empresa_id,
        )
        twiml = f"""
<Response>
    {voz_1}
</Response>
"""

        print("=== CANCELACION ===")
        print(twiml)

        return Response(content=twiml, media_type="application/xml")

    elif "reprogramar" in respuesta:
        db.query(Conversacion).filter(Conversacion.telefono == telefono).delete()
        db.commit()

        empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

        nueva_conversacion = Conversacion(
            telefono=telefono,
            empresa_id=empresa_id,
            servicio_id=cita.servicio_id,
            prestador_id=cita.prestador_id,
            paso="REPROGRAMAR_FECHA",
        )

        db.add(nueva_conversacion)
        db.commit()

        if empresa and empresa.usa_prestadores:
            nueva_conversacion.paso = "REPROGRAMAR_TIPO_PRESTADOR"
            db.commit()

            if cita.prestador_id:
                voz_opciones = construir_bloque_voz(
                    "Presione 1 para mantener a su mismo profesional. Presione 2 para elegir "
                    "otro profesional.",
                    empresa_id=empresa_id,
                )
            else:
                voz_opciones = construir_bloque_voz(
                    "¿Desea atenderse con un profesional específico? Presione 1 para elegir un "
                    "profesional. Presione 2 para atenderse con cualquier profesional disponible.",
                    empresa_id=empresa_id,
                )

            twiml = f"""
<Response>
    <Gather
        input="dtmf"
        numDigits="1"
        action="/reprogramar-tipo-prestador?telefono={telefono}"
        method="POST"
        timeout="10">
        {voz_opciones}
    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

        voz_1 = construir_bloque_voz(
            "Perfecto. ¿Para qué nueva fecha desea reprogramar su cita?", empresa_id=empresa_id
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

    respuesta_consulta = responder_consulta_en_flujo(
        db=db,
        empresa=empresa,
        mensaje_usuario=SpeechResult.strip(),
        action=f"/procesar-cita?telefono={telefono}&amp;empresa_id={empresa_id}",
        pregunta_pendiente="Sobre su cita, diga cancelar o reprogramar.",
    )

    if respuesta_consulta is not None:
        return respuesta_consulta

    voz_1 = construir_bloque_voz("No entendí su respuesta.", empresa_id=empresa_id)
    return Response(
        content=f"""
<Response>
    {voz_1}
</Response>
""",
        media_type="application/xml",
    )


@router.post("/procesar-agenda")
async def procesar_agenda(
    telefono: str,
    empresa_id: int,
    SpeechResult: str = Form(""),
    db: Session = Depends(get_db),
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    print("ENTRO A PROCESAR_AGENDA")
    print(f"SpeechResult RAW: [{SpeechResult}]")
    print(f"Empresa ID: {empresa_id}")

    respuesta = SpeechResult.lower().strip()

    print(f"Teléfono: {telefono}")
    print(f"Respuesta usuario: {respuesta}")

    def _iniciar_flujo_determinista_servicio():
        db.query(Conversacion).filter(Conversacion.telefono == telefono).delete()
        db.commit()

        nueva_conversacion = Conversacion(
            telefono=telefono, empresa_id=empresa_id, paso="PEDIR_SERVICIO"
        )

        db.add(nueva_conversacion)
        db.commit()
        servicios = (
            db.query(Servicio)
            .filter(Servicio.empresa_id == empresa_id, Servicio.activo == True)
            .all()
        )

        textos_servicios = [f"{i}. {servicio.nombre}" for i, servicio in enumerate(servicios, start=1)]

        voz_intro, voz_instruccion, *bloques_servicios = construir_bloques_voz(
            "Perfecto. Seleccione uno de los siguientes servicios.",
            "Presione en su teléfono el número del servicio que desea.",
            *textos_servicios,
            empresa_id=empresa_id,
        )

        lista_servicios = "\n            ".join(bloques_servicios)

        twiml = f"""
<Response>

    <Gather
    input="dtmf"
    numDigits="1"
    action="/guardar-servicio?telefono={telefono}"
    method="POST"
    timeout="10">

        {voz_intro}

        {lista_servicios}

        {voz_instruccion}

    </Gather>

</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    empresa_para_ia = db.query(Empresa).filter(Empresa.id == empresa_id).first()

    # La IA tiene prioridad sobre el disparador por palabra clave: se
    # intenta primero interpretar el mensaje completo con OpenAI. El
    # disparador determinístico ("agendar"/"agenda"/"en") queda como
    # respaldo únicamente para cuando OpenAI no está disponible o no
    # devolvió nada (resultado_ia is None) — así la llamada nunca depende
    # por completo de un servicio externo.
    resultado_ia = None

    if empresa_para_ia and respuesta:
        contexto = construir_contexto_empresa(db, empresa_para_ia)
        resultado_ia = interpretar_mensaje(
            empresa=empresa_para_ia,
            contexto=contexto,
            paso_actual=None,
            mensaje_usuario=SpeechResult.strip(),
        )

        if resultado_ia and resultado_ia.dentro_del_dominio and resultado_ia.intencion == "AGENDAR":
            respuesta_ia = iniciar_agendado_desde_ia_llamada(
                db=db,
                empresa=empresa_para_ia,
                empresa_id=empresa_id,
                telefono=telefono,
                resultado_ia=resultado_ia,
                mensaje_original=SpeechResult.strip(),
            )

            if respuesta_ia is not None:
                return respuesta_ia

            # La IA detectó intención de agendar pero no logró reconocer
            # ningún servicio real (ej. el cliente solo dijo "quiero
            # agendar" sin más detalle) — continúa con el flujo clásico de
            # elegir servicio por número, igual que si hubiera disparado
            # por palabra clave.
            return _iniciar_flujo_determinista_servicio()

        if resultado_ia and resultado_ia.dentro_del_dominio and resultado_ia.intencion not in (
            "FUERA_DE_CONTEXTO",
            "NO_ENTENDIDO",
        ):
            texto_respuesta = responder_pregunta_empresa(
                empresa=empresa_para_ia,
                contexto=contexto,
                mensaje_usuario=SpeechResult.strip(),
            )

            voz_1, voz_2 = construir_bloques_voz(
                texto_respuesta,
                "Si desea agendar una cita, dígalo cuando guste.",
                empresa_id=empresa_id,
            )

            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/procesar-agenda?telefono={telefono}&amp;empresa_id={empresa_id}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

        {voz_2}

    </Gather>
</Response>
"""

            return Response(content=twiml, media_type="application/xml")

    if resultado_ia is None and (
        "agendar" in respuesta
        or "agenda" in respuesta
        or "agéndar" in respuesta
        or "en" in respuesta
    ):
        return _iniciar_flujo_determinista_servicio()

    voz_1 = construir_bloque_voz("No entendí su respuesta.", empresa_id=empresa_id)
    twiml = f"""
<Response>
    {voz_1}
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/guardar-nombre")
async def guardar_nombre(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    nombre = SpeechResult.strip()

    print("ENTRO A GUARDAR_NOMBRE")
    print(f"Teléfono: {telefono}")
    print(f"Nombre recibido: {nombre}")

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    print(f"Conversacion encontrada: {conversacion}")

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa. Intente llamar nuevamente.")
        twiml = f"""
<Response>
    {voz_1}
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    empresa = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    respuesta_consulta = responder_consulta_en_flujo(
        db=db,
        empresa=empresa,
        mensaje_usuario=nombre,
        action=f"/guardar-nombre?telefono={telefono}",
        pregunta_pendiente="¿Cuál es su nombre completo?",
        paso_actual="PEDIR_NOMBRE",
    )

    if respuesta_consulta is not None:
        return respuesta_consulta

    conversacion.nombre = nombre
    conversacion.paso = "PEDIR_FECHA"

    db.commit()

    voz_1, voz_2 = construir_bloques_voz(
        f"Gracias {nombre}. ¿Qué fecha desea para su cita?",
        "No recibí la fecha. Intente nuevamente.",
        empresa_id=conversacion.empresa_id,
    )
    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>

    {voz_2}
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/guardar-servicio")
async def guardar_servicio(
    telefono: str,
    Digits: str = Form(""),
    db: Session = Depends(get_db),
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    opcion = Digits.strip()
    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa. Intente llamar nuevamente.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    servicios = (
        db.query(Servicio)
        .filter(Servicio.empresa_id == conversacion.empresa_id, Servicio.activo == True)
        .all()
    )
    servicio_seleccionado = None

    if opcion.isdigit():
        indice = int(opcion) - 1

        if 0 <= indice < len(servicios):
            servicio_seleccionado = servicios[indice]

    if not servicio_seleccionado:
        textos_servicios = [f"{i}. {s.nombre}" for i, s in enumerate(servicios, start=1)]

        voz_error, *bloques_servicios = construir_bloques_voz(
            "No encontré ese servicio. Por favor presione un número válido.",
            *textos_servicios,
            empresa_id=conversacion.empresa_id,
        )

        lista_servicios = "\n            ".join(bloques_servicios)

        twiml = f"""
<Response>
    <Gather
    input="dtmf"
    numDigits="1"
    action="/guardar-servicio?telefono={telefono}"
    method="POST"
    timeout="10">

        {voz_error}

        {lista_servicios}

    </Gather>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    conversacion.servicio_id = servicio_seleccionado.id

    empresa = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    if empresa and empresa.usa_prestadores:
        conversacion.paso = "PEDIR_TIPO_PRESTADOR"
        db.commit()

        voz_1, voz_2 = construir_bloques_voz(
            f"Perfecto, seleccionó {servicio_seleccionado.nombre}.",
            "¿Desea atenderse con un profesional específico? Presione 1 para elegir un "
            "profesional. Presione 2 para atenderse con cualquier profesional disponible.",
            empresa_id=conversacion.empresa_id,
        )
        twiml = f"""
<Response>
    <Gather
        input="dtmf"
        numDigits="1"
        action="/guardar-tipo-prestador?telefono={telefono}"
        method="POST"
        timeout="10">

        {voz_1}

        {voz_2}

    </Gather>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    conversacion.paso = "PEDIR_NOMBRE"

    db.commit()

    voz_1, voz_2 = construir_bloques_voz(
        f"Perfecto, seleccionó {servicio_seleccionado.nombre}. ¿Cuál es su nombre completo?",
        "No recibí su nombre. Intente nuevamente.",
        empresa_id=conversacion.empresa_id,
    )
    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-nombre?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>

    {voz_2}
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/guardar-tipo-prestador")
async def guardar_tipo_prestador(
    telefono: str,
    Digits: str = Form(""),
    db: Session = Depends(get_db),
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa. Intente llamar nuevamente.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    opcion = Digits.strip()

    if opcion == "2":
        conversacion.prestador_id = None
        conversacion.asignacion_automatica = True
        conversacion.paso = "PEDIR_NOMBRE"
        db.commit()

        voz_1, voz_2 = construir_bloques_voz(
            "Perfecto, se atenderá con cualquier profesional disponible. ¿Cuál es su nombre completo?",
            "No recibí su nombre. Intente nuevamente.",
            empresa_id=conversacion.empresa_id,
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-nombre?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>

    {voz_2}
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    if opcion == "1":
        prestadores = obtener_prestadores_compatibles(
            db, conversacion.empresa_id, conversacion.servicio_id
        )

        if not prestadores:
            conversacion.prestador_id = None
            conversacion.asignacion_automatica = True
            conversacion.paso = "PEDIR_NOMBRE"
            db.commit()

            voz_1 = construir_bloque_voz(
                "Por el momento no hay profesionales específicos disponibles para ese "
                "servicio. Le atenderá cualquier profesional disponible. ¿Cuál es su nombre "
                "completo?",
                empresa_id=conversacion.empresa_id,
            )
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-nombre?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

        textos_prestadores = [f"{i}. {p.nombre}" for i, p in enumerate(prestadores, start=1)]

        voz_intro, voz_instruccion, *bloques_prestadores = construir_bloques_voz(
            "Seleccione uno de los siguientes profesionales.",
            "Presione en su teléfono el número del profesional que desea.",
            *textos_prestadores,
            empresa_id=conversacion.empresa_id,
        )

        lista_prestadores = "\n            ".join(bloques_prestadores)

        conversacion.paso = "PEDIR_PRESTADOR"
        db.commit()

        twiml = f"""
<Response>
    <Gather
    input="dtmf"
    numDigits="1"
    action="/guardar-prestador?telefono={telefono}"
    method="POST"
    timeout="10">

        {voz_intro}

        {lista_prestadores}

        {voz_instruccion}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    voz_1 = construir_bloque_voz(
        "No entendí su respuesta. Presione 1 para elegir un profesional. Presione 2 para "
        "atenderse con cualquier profesional disponible.",
        empresa_id=conversacion.empresa_id,
    )
    twiml = f"""
<Response>
    <Gather
        input="dtmf"
        numDigits="1"
        action="/guardar-tipo-prestador?telefono={telefono}"
        method="POST"
        timeout="10">

        {voz_1}

    </Gather>
</Response>
"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/guardar-prestador")
async def guardar_prestador(
    telefono: str,
    Digits: str = Form(""),
    db: Session = Depends(get_db),
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa. Intente llamar nuevamente.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    prestadores = obtener_prestadores_compatibles(
        db, conversacion.empresa_id, conversacion.servicio_id
    )

    opcion = Digits.strip()
    prestador_seleccionado = None

    if opcion.isdigit():
        indice = int(opcion) - 1

        if 0 <= indice < len(prestadores):
            prestador_seleccionado = prestadores[indice]

    if not prestador_seleccionado:
        textos_prestadores = [f"{i}. {p.nombre}" for i, p in enumerate(prestadores, start=1)]

        voz_error, *bloques_prestadores = construir_bloques_voz(
            "No encontré ese profesional. Por favor presione un número válido.",
            *textos_prestadores,
            empresa_id=conversacion.empresa_id,
        )

        lista_prestadores = "\n            ".join(bloques_prestadores)

        twiml = f"""
<Response>
    <Gather
    input="dtmf"
    numDigits="1"
    action="/guardar-prestador?telefono={telefono}"
    method="POST"
    timeout="10">

        {voz_error}

        {lista_prestadores}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    conversacion.prestador_id = prestador_seleccionado.id
    conversacion.asignacion_automatica = False
    conversacion.paso = "PEDIR_NOMBRE"

    db.commit()

    voz_1, voz_2 = construir_bloques_voz(
        f"Perfecto, se atenderá con {prestador_seleccionado.nombre}. ¿Cuál es su nombre completo?",
        "No recibí su nombre. Intente nuevamente.",
        empresa_id=conversacion.empresa_id,
    )
    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-nombre?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>

    {voz_2}
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/guardar-fecha")
async def guardar_fecha(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    fecha = normalizar_fecha(SpeechResult.strip())

    if fecha and fecha_ya_paso(fecha):
        voz_1 = construir_bloque_voz("La fecha indicada ya pasó. Por favor indique una fecha futura.")
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/guardar-fecha?telefono={telefono}"
            method="POST">

            {voz_1}

        </Gather>
    </Response>
    """
        return Response(content=twiml, media_type="application/xml")

    if fecha is None:
        conversacion_para_ia = (
            db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
        )
        empresa_para_ia = (
            db.query(Empresa).filter(Empresa.id == conversacion_para_ia.empresa_id).first()
            if conversacion_para_ia
            else None
        )

        contexto = None
        resultado_ia = None

        if empresa_para_ia:
            contexto = construir_contexto_empresa(db, empresa_para_ia)
            resultado_ia = interpretar_mensaje(
                empresa=empresa_para_ia,
                contexto=contexto,
                paso_actual="PEDIR_FECHA",
                mensaje_usuario=SpeechResult.strip(),
            )

            if resultado_ia and resultado_ia.fecha:
                fecha_ia = normalizar_fecha_iso(resultado_ia.fecha) or normalizar_fecha(
                    resultado_ia.fecha
                )

                if fecha_ia and not fecha_ya_paso(fecha_ia):
                    fecha = fecha_ia

        if fecha is None:
            respuesta_consulta = responder_consulta_en_flujo(
                db=db,
                empresa=empresa_para_ia,
                mensaje_usuario=SpeechResult.strip(),
                action=f"/guardar-fecha?telefono={telefono}",
                pregunta_pendiente="¿Qué fecha desea para su cita?",
                resultado_ia=resultado_ia,
                contexto=contexto,
            )

            if respuesta_consulta is not None:
                return respuesta_consulta

            voz_1 = construir_bloque_voz(
                "No entendí la fecha. Por favor diga una fecha como quince de junio, mañana o "
                "pasado mañana.",
                empresa_id=empresa_para_ia.id if empresa_para_ia else None,
            )
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""

            return Response(content=twiml, media_type="application/xml")

    print("ENTRO A GUARDAR_FECHA")
    print(f"Fecha recibida: {fecha}")

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    conversacion.fecha = fecha

    empresa_actual = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    if empresa_actual and empresa_actual.permite_citas_sin_hora:
        conversacion.paso = "PEDIR_TIPO_HORA"

        db.commit()

        voz_1 = construir_bloque_voz(
            "Puede elegir una hora específica o indicar que no tiene preferencia de horario. "
            "Presione 1 para elegir una hora específica. Presione 2 para agendar sin hora "
            "específica.",
            empresa_id=conversacion.empresa_id,
        )
        twiml = f"""
<Response>
    <Gather
        input="dtmf"
        numDigits="1"
        action="/guardar-tipo-hora?telefono={telefono}"
        method="POST"
        timeout="10">

        {voz_1}

    </Gather>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    conversacion.paso = "PEDIR_HORA"

    db.commit()

    voz_1 = construir_bloque_voz("Perfecto. ¿A qué hora desea la cita?", empresa_id=conversacion.empresa_id)
    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-hora?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/guardar-tipo-hora")
async def guardar_tipo_hora(
    telefono: str,
    Digits: str = Form(""),
    db: Session = Depends(get_db),
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa. Intente llamar nuevamente.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    opcion = Digits.strip()

    if opcion == "2":
        empresa = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()
        servicio = (
            db.query(Servicio).filter(Servicio.id == conversacion.servicio_id).first()
        )

        try:
            crear_solicitud_sin_hora(
                db=db,
                empresa=empresa,
                servicio=servicio,
                fecha=conversacion.fecha,
                nombre=conversacion.nombre,
                telefono=telefono,
                canal="LLAMADA",
                prestador_id=conversacion.prestador_id,
            )
        except ValueError as error:
            voz_1 = construir_bloque_voz(
                f"No fue posible registrar su solicitud: {error}.", empresa_id=conversacion.empresa_id
            )
            return Response(
                content=f"""
<Response>
    {voz_1}
</Response>
""",
                media_type="application/xml",
            )

        fecha_confirmada = conversacion.fecha
        empresa_id_conversacion = conversacion.empresa_id

        db.delete(conversacion)
        db.commit()

        voz_1 = construir_bloque_voz(
            f"Perfecto. Registraré su solicitud para el día {fecha_confirmada} sin una hora "
            "específica. La empresa podrá asignar el horario posteriormente. Gracias por usar "
            "nuestro sistema de citas.",
            empresa_id=empresa_id_conversacion,
        )
        twiml = f"""
<Response>
    {voz_1}
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    if opcion == "1":
        conversacion.paso = "PEDIR_HORA"
        db.commit()

        voz_1 = construir_bloque_voz("Perfecto. ¿A qué hora desea la cita?", empresa_id=conversacion.empresa_id)
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-hora?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    voz_1 = construir_bloque_voz(
        "No entendí su respuesta. Presione 1 para elegir una hora específica. Presione 2 "
        "para agendar sin hora específica.",
        empresa_id=conversacion.empresa_id,
    )
    twiml = f"""
<Response>
    <Gather
        input="dtmf"
        numDigits="1"
        action="/guardar-tipo-hora?telefono={telefono}"
        method="POST"
        timeout="10">

        {voz_1}

    </Gather>
</Response>
"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/agendar-ia-continuar")
async def agendar_ia_continuar(
    telefono: str,
    Digits: str = Form(""),
    SpeechResult: str = Form(""),
    db: Session = Depends(get_db),
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    flujo = db.query(Conversacion).filter(Conversacion.telefono == telefono).first()

    if not flujo:
        voz_1 = construir_bloque_voz("No encontré una conversación activa. Intente llamar nuevamente.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    empresa = db.query(Empresa).filter(Empresa.id == flujo.empresa_id).first()

    return _continuar_agendado_ia_llamada(
        db=db,
        empresa=empresa,
        telefono=telefono,
        flujo=flujo,
        digits=Digits,
        speech=SpeechResult,
    )


@router.post("/guardar-hora")
async def guardar_hora(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    hora = normalizar_hora(SpeechResult.strip())

    if hora == "AMBIGUA":
        conversacion = (
            db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
        )

        if not conversacion:
            voz_1 = construir_bloque_voz("No encontré una conversación activa.")
            return Response(
                content=f"""
<Response>
    {voz_1}
</Response>
""",
                media_type="application/xml",
            )

        conversacion.hora = SpeechResult.strip()
        conversacion.paso = "ACLARAR_HORA"
        db.commit()

        voz_1, voz_2 = construir_bloques_voz(
            f"¿Se refiere a las {SpeechResult} de la mañana o de la tarde?",
            "No recibí la aclaración. Intente nuevamente.",
            empresa_id=conversacion.empresa_id,
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/aclarar-hora?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>

    {voz_2}
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    if hora is None:
        conversacion_para_ia = (
            db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
        )
        empresa_para_ia = (
            db.query(Empresa).filter(Empresa.id == conversacion_para_ia.empresa_id).first()
            if conversacion_para_ia
            else None
        )

        contexto = None
        resultado_ia = None

        if empresa_para_ia and conversacion_para_ia:
            contexto = construir_contexto_empresa(db, empresa_para_ia)
            resultado_ia = interpretar_mensaje(
                empresa=empresa_para_ia,
                contexto=contexto,
                paso_actual="PEDIR_HORA",
                mensaje_usuario=SpeechResult.strip(),
            )

            if resultado_ia and resultado_ia.hora:
                hora_ia = normalizar_hora_valida(resultado_ia.hora)

                if hora_ia:
                    hora = hora_ia

        if hora is None:
            if (
                resultado_ia
                and conversacion_para_ia
                and resultado_ia.intencion in ("CONSULTAR_HORARIOS", "ELEGIR_HORA")
            ):
                return respuesta_disponibilidad_en_flujo(
                    db=db,
                    empresa=empresa_para_ia,
                    fecha=conversacion_para_ia.fecha,
                    servicio_id=conversacion_para_ia.servicio_id,
                    prestador_id=conversacion_para_ia.prestador_id,
                    action=f"/guardar-hora?telefono={telefono}",
                    pregunta_pendiente="¿A qué hora desea la cita?",
                )

            respuesta_consulta = responder_consulta_en_flujo(
                db=db,
                empresa=empresa_para_ia,
                mensaje_usuario=SpeechResult.strip(),
                action=f"/guardar-hora?telefono={telefono}",
                pregunta_pendiente="¿A qué hora desea la cita?",
                resultado_ia=resultado_ia,
                contexto=contexto,
            )

            if respuesta_consulta is not None:
                return respuesta_consulta

            voz_1, voz_2, voz_3 = construir_bloques_voz(
                "No entendí la hora.",
                "Por favor diga una hora como diez de la mañana, cinco de la tarde o tres y media.",
                "No recibí ninguna respuesta. Intente nuevamente.",
                empresa_id=empresa_para_ia.id if empresa_para_ia else None,
            )
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-hora?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

        {voz_2}

    </Gather>

    {voz_3}
</Response>
"""

            return Response(content=twiml, media_type="application/xml")

    conversacion = (
        db.query(Conversacion)
        .filter(Conversacion.telefono == telefono)
        .first()
    )

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    if hora_ya_paso(conversacion.fecha, hora):
        voz_1 = construir_bloque_voz(
            "Esa hora ya pasó. Por favor indique una hora futura.", empresa_id=conversacion.empresa_id
        )
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/guardar-hora?telefono={telefono}"
            method="POST">

            {voz_1}

        </Gather>
    </Response>
    """
        return Response(content=twiml, media_type="application/xml")

    print("ENTRO A GUARDAR_HORA")
    print(f"Hora recibida: {hora}")

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    conversacion.hora = hora

    empresa = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    if hora < empresa.horario_inicio or hora > empresa.horario_fin:
        voz_1, voz_2 = construir_bloques_voz(
            f"Lo sentimos. El horario de atención es de {empresa.horario_inicio} a "
            f"{empresa.horario_fin}. Por favor indique otra hora.",
            "No recibí la hora. Intente nuevamente.",
            empresa_id=conversacion.empresa_id,
        )
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST"
            timeout="8"
            speechTimeout="auto">

            {voz_1}

        </Gather>

        {voz_2}
    </Response>
    """

        return Response(content=twiml, media_type="application/xml")

    prestador_final = None

    if empresa.usa_prestadores:
        if conversacion.prestador_id:
            cita_ocupada = horario_choca_con_duracion(
                db=db,
                empresa_id=conversacion.empresa_id,
                fecha=conversacion.fecha,
                hora=hora,
                servicio_id=conversacion.servicio_id,
                prestador_id=conversacion.prestador_id,
            )

            if cita_ocupada:
                alternativas = obtener_horarios_disponibles(
                    db=db,
                    empresa=empresa,
                    fecha=conversacion.fecha,
                    servicio_id=conversacion.servicio_id,
                    prestador_id=conversacion.prestador_id,
                )
                return respuesta_prestador_ocupado(alternativas)

            prestador_final = conversacion.prestador_id
        else:
            prestador_seleccionado = seleccionar_prestador_automaticamente(
                db=db,
                empresa_id=conversacion.empresa_id,
                servicio_id=conversacion.servicio_id,
                fecha=conversacion.fecha,
                hora=hora,
            )

            if not prestador_seleccionado:
                alternativas = obtener_horarios_disponibles(
                    db=db,
                    empresa=empresa,
                    fecha=conversacion.fecha,
                    servicio_id=conversacion.servicio_id,
                )
                return respuesta_sin_prestadores(alternativas)

            prestador_final = prestador_seleccionado.id
    else:
        cita_ocupada = horario_choca_con_duracion(
            db=db,
            empresa_id=conversacion.empresa_id,
            fecha=conversacion.fecha,
            hora=hora,
            servicio_id=conversacion.servicio_id,
        )

        if cita_ocupada:
            return respuesta_horario_ocupado()

    nombre_cliente = conversacion.nombre
    fecha_cita = conversacion.fecha
    empresa_id_conversacion = conversacion.empresa_id

    nueva_cita = Cita(
        nombre=conversacion.nombre,
        telefono=telefono,
        fecha=conversacion.fecha,
        hora=hora,
        status="AGENDADA",
        empresa_id=conversacion.empresa_id,
        servicio_id=conversacion.servicio_id,
        prestador_id=prestador_final,
    )

    db.add(nueva_cita)
    db.delete(conversacion)
    db.commit()

    voz_1 = construir_bloque_voz(
        f"Perfecto {nombre_cliente}. Su cita fue agendada para el día {fecha_cita} a las "
        f"{hora} horas. Gracias por usar nuestro sistema de citas.",
        empresa_id=empresa_id_conversacion,
    )
    twiml = f"""
<Response>
    {voz_1}
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/aclarar-hora")
async def aclarar_hora(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    respuesta = SpeechResult.lower().strip()

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    if conversacion.paso == "ACLARAR_HORA_REPROGRAMAR":
        accion_consulta = f"/aclarar-hora-reprogramar?telefono={telefono}"
    else:
        accion_consulta = f"/aclarar-hora?telefono={telefono}"

    empresa_consulta = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    respuesta_consulta = responder_consulta_en_flujo(
        db=db,
        empresa=empresa_consulta,
        mensaje_usuario=SpeechResult.strip(),
        action=accion_consulta,
        pregunta_pendiente=f"¿Se refiere a las {conversacion.hora} de la mañana o de la tarde?",
        paso_actual="ACLARAR_HORA",
    )

    if respuesta_consulta is not None:
        return respuesta_consulta

    hora_original = conversacion.hora

    try:
        numero = int("".join(filter(str.isdigit, hora_original)))
    except:
        voz_1 = construir_bloque_voz("No pude identificar la hora.", empresa_id=conversacion.empresa_id)
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    if "tarde" in respuesta or "noche" in respuesta:
        hora_final = f"{numero + 12:02d}:00"
    else:
        hora_final = f"{numero:02d}:00"

    if hora_ya_paso(conversacion.fecha, hora_final):
        voz_1 = construir_bloque_voz(
            "Esa hora ya pasó. Por favor indique una hora futura.", empresa_id=conversacion.empresa_id
        )
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/guardar-hora?telefono={telefono}"
            method="POST">

            {voz_1}

        </Gather>
    </Response>
    """
        return Response(content=twiml, media_type="application/xml")

    empresa = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    if hora_final < empresa.horario_inicio or hora_final > empresa.horario_fin:
        voz_1, voz_2 = construir_bloques_voz(
            f"Lo sentimos. Nuestro horario de atención es de {empresa.horario_inicio} a "
            f"{empresa.horario_fin}. Por favor indique otra hora.",
            "No recibí ninguna respuesta.",
            empresa_id=conversacion.empresa_id,
        )
        twiml = f"""
    <Response>

        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST"
            timeout="8"
            speechTimeout="auto">

            {voz_1}

        </Gather>

        {voz_2}

    </Response>
    """
        return Response(content=twiml, media_type="application/xml")

    prestador_final = None

    if empresa.usa_prestadores:
        if conversacion.prestador_id:
            cita_ocupada = horario_choca_con_duracion(
                db=db,
                empresa_id=conversacion.empresa_id,
                fecha=conversacion.fecha,
                hora=hora_final,
                servicio_id=conversacion.servicio_id,
                prestador_id=conversacion.prestador_id,
            )

            if cita_ocupada:
                alternativas = obtener_horarios_disponibles(
                    db=db,
                    empresa=empresa,
                    fecha=conversacion.fecha,
                    servicio_id=conversacion.servicio_id,
                    prestador_id=conversacion.prestador_id,
                )
                return respuesta_prestador_ocupado(alternativas)

            prestador_final = conversacion.prestador_id
        else:
            prestador_seleccionado = seleccionar_prestador_automaticamente(
                db=db,
                empresa_id=conversacion.empresa_id,
                servicio_id=conversacion.servicio_id,
                fecha=conversacion.fecha,
                hora=hora_final,
            )

            if not prestador_seleccionado:
                alternativas = obtener_horarios_disponibles(
                    db=db,
                    empresa=empresa,
                    fecha=conversacion.fecha,
                    servicio_id=conversacion.servicio_id,
                )
                return respuesta_sin_prestadores(alternativas)

            prestador_final = prestador_seleccionado.id
    else:
        cita_ocupada = horario_choca_con_duracion(
            db=db,
            empresa_id=conversacion.empresa_id,
            fecha=conversacion.fecha,
            hora=hora_final,
            servicio_id=conversacion.servicio_id,
        )

        if cita_ocupada:
            return respuesta_horario_ocupado()

    empresa_id_conversacion = conversacion.empresa_id

    nueva_cita = Cita(
        nombre=conversacion.nombre,
        telefono=telefono,
        fecha=conversacion.fecha,
        hora=hora_final,
        status="AGENDADA",
        empresa_id=conversacion.empresa_id,
        servicio_id=conversacion.servicio_id,
        prestador_id=prestador_final,
    )

    db.add(nueva_cita)

    db.delete(conversacion)

    db.commit()

    voz_1 = construir_bloque_voz(
        f"Perfecto. Su cita fue agendada para el día {nueva_cita.fecha} a las "
        f"{hora_final} horas.",
        empresa_id=empresa_id_conversacion,
    )
    twiml = f"""
<Response>
    {voz_1}
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/reprogramar-fecha")
async def reprogramar_fecha(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    fecha = normalizar_fecha(SpeechResult.strip())

    if fecha and fecha_ya_paso(fecha):
        voz_1 = construir_bloque_voz("La fecha indicada ya pasó. Por favor indique una fecha futura.")
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-fecha?telefono={telefono}"
            method="POST">

            {voz_1}

        </Gather>
    </Response>
    """
        return Response(content=twiml, media_type="application/xml")

    if fecha is None:
        conversacion_para_ia = (
            db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
        )
        empresa_para_ia = (
            db.query(Empresa).filter(Empresa.id == conversacion_para_ia.empresa_id).first()
            if conversacion_para_ia
            else None
        )

        respuesta_consulta = responder_consulta_en_flujo(
            db=db,
            empresa=empresa_para_ia,
            mensaje_usuario=SpeechResult.strip(),
            action=f"/reprogramar-fecha?telefono={telefono}",
            pregunta_pendiente="¿Para qué nueva fecha desea reprogramar su cita?",
            paso_actual="REPROGRAMAR_FECHA",
        )

        if respuesta_consulta is not None:
            return respuesta_consulta

        voz_1 = construir_bloque_voz(
            "No entendí la fecha. Por favor diga una fecha como quince de junio, mañana o "
            "pasado mañana.",
            empresa_id=empresa_para_ia.id if empresa_para_ia else None,
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    conversacion.fecha = fecha
    conversacion.paso = "REPROGRAMAR_HORA"

    db.commit()

    voz_1, voz_2 = construir_bloques_voz(
        "Perfecto. ¿A qué nueva hora desea reprogramar su cita?",
        "No recibí la hora. Intente nuevamente.",
        empresa_id=conversacion.empresa_id,
    )
    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-hora?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>

    {voz_2}
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/reprogramar-tipo-prestador")
async def reprogramar_tipo_prestador(
    telefono: str,
    Digits: str = Form(""),
    db: Session = Depends(get_db),
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa. Intente llamar nuevamente.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    opcion = Digits.strip()

    if opcion == "1" and conversacion.prestador_id:
        conversacion.asignacion_automatica = False
        conversacion.paso = "REPROGRAMAR_FECHA"
        db.commit()

        voz_1 = construir_bloque_voz(
            "Perfecto. ¿Para qué nueva fecha desea reprogramar su cita?", empresa_id=conversacion.empresa_id
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    if opcion == "2" and not conversacion.prestador_id:
        conversacion.prestador_id = None
        conversacion.asignacion_automatica = True
        conversacion.paso = "REPROGRAMAR_FECHA"
        db.commit()

        voz_1 = construir_bloque_voz(
            "Perfecto, se atenderá con cualquier profesional disponible. ¿Para qué nueva fecha "
            "desea reprogramar su cita?",
            empresa_id=conversacion.empresa_id,
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    if (opcion == "2" and conversacion.prestador_id) or (
        opcion == "1" and not conversacion.prestador_id
    ):
        prestadores = obtener_prestadores_compatibles(
            db, conversacion.empresa_id, conversacion.servicio_id
        )

        if not prestadores:
            conversacion.prestador_id = None
            conversacion.asignacion_automatica = True
            conversacion.paso = "REPROGRAMAR_FECHA"
            db.commit()

            voz_1 = construir_bloque_voz(
                "Por el momento no hay profesionales específicos disponibles para ese "
                "servicio. Le atenderá cualquier profesional disponible. ¿Para qué nueva fecha "
                "desea reprogramar su cita?",
                empresa_id=conversacion.empresa_id,
            )
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""
            return Response(content=twiml, media_type="application/xml")

        textos_prestadores = [f"{i}. {p.nombre}" for i, p in enumerate(prestadores, start=1)]

        voz_intro, voz_instruccion, *bloques_prestadores = construir_bloques_voz(
            "Seleccione uno de los siguientes profesionales.",
            "Presione en su teléfono el número del profesional que desea.",
            *textos_prestadores,
            empresa_id=conversacion.empresa_id,
        )

        lista_prestadores = "\n            ".join(bloques_prestadores)

        conversacion.paso = "REPROGRAMAR_PRESTADOR"
        db.commit()

        twiml = f"""
<Response>
    <Gather
    input="dtmf"
    numDigits="1"
    action="/reprogramar-prestador?telefono={telefono}"
    method="POST"
    timeout="10">

        {voz_intro}

        {lista_prestadores}

        {voz_instruccion}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    voz_1 = construir_bloque_voz(
        "No entendí su respuesta. Presione 1 o presione 2.", empresa_id=conversacion.empresa_id
    )
    twiml = f"""
<Response>
    <Gather
        input="dtmf"
        numDigits="1"
        action="/reprogramar-tipo-prestador?telefono={telefono}"
        method="POST"
        timeout="10">

        {voz_1}

    </Gather>
</Response>
"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/reprogramar-prestador")
async def reprogramar_prestador(
    telefono: str,
    Digits: str = Form(""),
    db: Session = Depends(get_db),
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa. Intente llamar nuevamente.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    prestadores = obtener_prestadores_compatibles(
        db, conversacion.empresa_id, conversacion.servicio_id
    )

    opcion = Digits.strip()
    prestador_seleccionado = None

    if opcion.isdigit():
        indice = int(opcion) - 1

        if 0 <= indice < len(prestadores):
            prestador_seleccionado = prestadores[indice]

    if not prestador_seleccionado:
        textos_prestadores = [f"{i}. {p.nombre}" for i, p in enumerate(prestadores, start=1)]

        voz_error, *bloques_prestadores = construir_bloques_voz(
            "No encontré ese profesional. Por favor presione un número válido.",
            *textos_prestadores,
            empresa_id=conversacion.empresa_id,
        )

        lista_prestadores = "\n            ".join(bloques_prestadores)

        twiml = f"""
<Response>
    <Gather
    input="dtmf"
    numDigits="1"
    action="/reprogramar-prestador?telefono={telefono}"
    method="POST"
    timeout="10">

        {voz_error}

        {lista_prestadores}

    </Gather>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    conversacion.prestador_id = prestador_seleccionado.id
    conversacion.asignacion_automatica = False
    conversacion.paso = "REPROGRAMAR_FECHA"

    db.commit()

    voz_1 = construir_bloque_voz(
        f"Perfecto, se atenderá con {prestador_seleccionado.nombre}. ¿Para qué nueva fecha "
        "desea reprogramar su cita?",
        empresa_id=conversacion.empresa_id,
    )
    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/reprogramar-hora")
async def reprogramar_hora(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    hora = normalizar_hora(SpeechResult.strip())

    print("ENTRO A REPROGRAMAR_HORA")
    print(f"Hora normalizada: {hora}")

    if hora == "AMBIGUA":
        conversacion = (
            db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
        )

        if not conversacion:
            voz_1 = construir_bloque_voz("No encontré una conversación activa.")
            return Response(
                content=f"""
<Response>
    {voz_1}
</Response>
""",
                media_type="application/xml",
            )

        conversacion.hora = SpeechResult.strip()
        conversacion.paso = "ACLARAR_HORA_REPROGRAMAR"
        db.commit()

        voz_1, voz_2 = construir_bloques_voz(
            f"¿Se refiere a las {SpeechResult} de la mañana o de la tarde?",
            "No recibí la aclaración. Intente nuevamente.",
            empresa_id=conversacion.empresa_id,
        )
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/aclarar-hora-reprogramar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

    </Gather>

    {voz_2}
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    if hora is None:
        conversacion_para_ia = (
            db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
        )
        empresa_para_ia = (
            db.query(Empresa).filter(Empresa.id == conversacion_para_ia.empresa_id).first()
            if conversacion_para_ia
            else None
        )

        contexto = None
        resultado_ia = None

        if empresa_para_ia:
            contexto = construir_contexto_empresa(db, empresa_para_ia)
            resultado_ia = interpretar_mensaje(
                empresa=empresa_para_ia,
                contexto=contexto,
                paso_actual="REPROGRAMAR_HORA",
                mensaje_usuario=SpeechResult.strip(),
            )

            if resultado_ia and resultado_ia.hora:
                hora_ia = normalizar_hora_valida(resultado_ia.hora)

                if hora_ia:
                    hora = hora_ia

        if hora is None and resultado_ia and conversacion_para_ia and resultado_ia.intencion in (
            "CONSULTAR_HORARIOS",
            "ELEGIR_HORA",
        ):
            return respuesta_disponibilidad_en_flujo(
                db=db,
                empresa=empresa_para_ia,
                fecha=conversacion_para_ia.fecha,
                servicio_id=conversacion_para_ia.servicio_id,
                prestador_id=conversacion_para_ia.prestador_id,
                action=f"/reprogramar-hora?telefono={telefono}",
                pregunta_pendiente="¿A qué nueva hora desea reprogramar su cita?",
            )

        if hora is None:
            respuesta_consulta = responder_consulta_en_flujo(
                db=db,
                empresa=empresa_para_ia,
                mensaje_usuario=SpeechResult.strip(),
                action=f"/reprogramar-hora?telefono={telefono}",
                pregunta_pendiente="¿A qué nueva hora desea reprogramar su cita?",
                resultado_ia=resultado_ia,
                contexto=contexto,
            )

            if respuesta_consulta is not None:
                return respuesta_consulta

            voz_1, voz_2, voz_3 = construir_bloques_voz(
                "No entendí la hora.",
                "Por favor diga una hora como diez de la mañana, cinco de la tarde o tres y media.",
                "No recibí ninguna respuesta. Intente nuevamente.",
                empresa_id=empresa_para_ia.id if empresa_para_ia else None,
            )
            twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-hora?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        {voz_1}

        {voz_2}

    </Gather>

    {voz_3}
</Response>
"""

            return Response(content=twiml, media_type="application/xml")

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )
    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa.")
        return Response(
            content=f"""
    <Response>
        {voz_1}
    </Response>
    """,
            media_type="application/xml",
        )

    if hora_ya_paso(conversacion.fecha, hora):
        voz_1 = construir_bloque_voz(
            "Esa hora ya pasó. Por favor indique una hora futura.", empresa_id=conversacion.empresa_id
        )
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST">

            {voz_1}

        </Gather>
    </Response>
    """
        return Response(content=twiml, media_type="application/xml")

    cita_anterior = (
        db.query(Cita)
        .filter(Cita.telefono == telefono)
        .filter(Cita.empresa_id == conversacion.empresa_id)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

    if not cita_anterior:
        voz_1 = construir_bloque_voz(
            "No encontré una cita activa para reprogramar.", empresa_id=conversacion.empresa_id
        )
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    empresa = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    if hora < empresa.horario_inicio or hora > empresa.horario_fin:
        voz_1, voz_2 = construir_bloques_voz(
            f"Lo sentimos. Nuestro horario de atención es de {empresa.horario_inicio} a "
            f"{empresa.horario_fin}. Por favor indique otra hora.",
            "No recibí ninguna respuesta.",
            empresa_id=conversacion.empresa_id,
        )
        twiml = f"""
    <Response>

        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST"
            timeout="8"
            speechTimeout="auto">

            {voz_1}

        </Gather>

        {voz_2}

    </Response>
    """
        return Response(content=twiml, media_type="application/xml")

    prestador_final = conversacion.prestador_id

    if empresa.usa_prestadores and conversacion.asignacion_automatica:
        prestador_seleccionado = seleccionar_prestador_automaticamente(
            db=db,
            empresa_id=conversacion.empresa_id,
            servicio_id=conversacion.servicio_id,
            fecha=conversacion.fecha,
            hora=hora,
            cita_ignorar_id=cita_anterior.id,
        )

        if not prestador_seleccionado:
            alternativas = obtener_horarios_disponibles(
                db=db,
                empresa=empresa,
                fecha=conversacion.fecha,
                servicio_id=conversacion.servicio_id,
                cita_ignorar_id=cita_anterior.id,
            )
            return respuesta_sin_prestadores(alternativas)

        prestador_final = prestador_seleccionado.id
    else:
        cita_ocupada = horario_choca_con_duracion(
            db=db,
            empresa_id=conversacion.empresa_id,
            fecha=conversacion.fecha,
            hora=hora,
            servicio_id=conversacion.servicio_id,
            prestador_id=prestador_final,
            cita_ignorar_id=cita_anterior.id,
        )

        if cita_ocupada:
            if prestador_final:
                alternativas = obtener_horarios_disponibles(
                    db=db,
                    empresa=empresa,
                    fecha=conversacion.fecha,
                    servicio_id=conversacion.servicio_id,
                    prestador_id=prestador_final,
                    cita_ignorar_id=cita_anterior.id,
                )
                return respuesta_prestador_ocupado(alternativas)

            return respuesta_horario_ocupado()

    empresa_id_conversacion = conversacion.empresa_id

    nueva_cita = reprogramar_cita(
        db=db,
        cita_anterior=cita_anterior,
        nueva_fecha=conversacion.fecha,
        nueva_hora=hora,
        canal="LLAMADA",
        prestador_id=prestador_final,
    )

    db.delete(conversacion)
    db.commit()

    voz_1 = construir_bloque_voz(
        f"Su cita fue reprogramada correctamente para el día {nueva_cita.fecha} a las "
        f"{nueva_cita.hora} horas.",
        empresa_id=empresa_id_conversacion,
    )
    twiml = f"""
<Response>
    {voz_1}
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/aclarar-hora-reprogramar")
async def aclarar_hora_reprogramar(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = normalizar_telefono_mexico(telefono.replace("%2B", "+"))

    respuesta = SpeechResult.lower().strip()

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        voz_1 = construir_bloque_voz("No encontré una conversación activa.")
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    if conversacion.paso == "ACLARAR_HORA_REPROGRAMAR":
        accion_consulta = f"/aclarar-hora-reprogramar?telefono={telefono}"
    else:
        accion_consulta = f"/aclarar-hora?telefono={telefono}"

    empresa_consulta = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    respuesta_consulta = responder_consulta_en_flujo(
        db=db,
        empresa=empresa_consulta,
        mensaje_usuario=SpeechResult.strip(),
        action=accion_consulta,
        pregunta_pendiente=f"¿Se refiere a las {conversacion.hora} de la mañana o de la tarde?",
        paso_actual="ACLARAR_HORA",
    )

    if respuesta_consulta is not None:
        return respuesta_consulta

    hora_original = conversacion.hora

    try:
        numero = int("".join(filter(str.isdigit, hora_original)))
    except:
        voz_1 = construir_bloque_voz("No pude identificar la hora.", empresa_id=conversacion.empresa_id)
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    if "tarde" in respuesta or "noche" in respuesta:
        hora_final = f"{numero + 12:02d}:00"
    else:
        hora_final = f"{numero:02d}:00"

    if hora_ya_paso(conversacion.fecha, hora_final):
        voz_1 = construir_bloque_voz(
            "Esa hora ya pasó. Por favor indique una hora futura.", empresa_id=conversacion.empresa_id
        )
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST">

            {voz_1}

        </Gather>
    </Response>
    """
        return Response(content=twiml, media_type="application/xml")

    cita_anterior = (
        db.query(Cita)
        .filter(Cita.telefono == telefono)
        .filter(Cita.empresa_id == conversacion.empresa_id)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

    if not cita_anterior:
        voz_1 = construir_bloque_voz(
            "No encontré una cita activa para reprogramar.", empresa_id=conversacion.empresa_id
        )
        return Response(
            content=f"""
<Response>
    {voz_1}
</Response>
""",
            media_type="application/xml",
        )

    empresa = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    if hora_final < empresa.horario_inicio or hora_final > empresa.horario_fin:
        voz_1, voz_2 = construir_bloques_voz(
            f"Lo sentimos. Nuestro horario de atención es de {empresa.horario_inicio} a "
            f"{empresa.horario_fin}. Por favor indique otra hora.",
            "No recibí ninguna respuesta.",
            empresa_id=conversacion.empresa_id,
        )
        twiml = f"""
    <Response>

        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST"
            timeout="8"
            speechTimeout="auto">

            {voz_1}

        </Gather>

        {voz_2}

    </Response>
    """
        return Response(content=twiml, media_type="application/xml")

    prestador_final = conversacion.prestador_id

    if empresa.usa_prestadores and conversacion.asignacion_automatica:
        prestador_seleccionado = seleccionar_prestador_automaticamente(
            db=db,
            empresa_id=conversacion.empresa_id,
            servicio_id=conversacion.servicio_id,
            fecha=conversacion.fecha,
            hora=hora_final,
            cita_ignorar_id=cita_anterior.id,
        )

        if not prestador_seleccionado:
            alternativas = obtener_horarios_disponibles(
                db=db,
                empresa=empresa,
                fecha=conversacion.fecha,
                servicio_id=conversacion.servicio_id,
                cita_ignorar_id=cita_anterior.id,
            )
            return respuesta_sin_prestadores(alternativas)

        prestador_final = prestador_seleccionado.id
    else:
        cita_ocupada = horario_choca_con_duracion(
            db=db,
            empresa_id=conversacion.empresa_id,
            fecha=conversacion.fecha,
            hora=hora_final,
            servicio_id=conversacion.servicio_id,
            prestador_id=prestador_final,
            cita_ignorar_id=cita_anterior.id,
        )

        if cita_ocupada:
            if prestador_final:
                alternativas = obtener_horarios_disponibles(
                    db=db,
                    empresa=empresa,
                    fecha=conversacion.fecha,
                    servicio_id=conversacion.servicio_id,
                    prestador_id=prestador_final,
                    cita_ignorar_id=cita_anterior.id,
                )
                return respuesta_prestador_ocupado(alternativas)

            return respuesta_horario_ocupado()

    empresa_id_conversacion = conversacion.empresa_id

    nueva_cita = reprogramar_cita(
        db=db,
        cita_anterior=cita_anterior,
        nueva_fecha=conversacion.fecha,
        nueva_hora=hora_final,
        canal="LLAMADA",
        prestador_id=prestador_final,
    )
    db.delete(conversacion)
    db.commit()

    voz_1 = construir_bloque_voz(
        f"Su cita fue reprogramada correctamente para el día {nueva_cita.fecha} a las "
        f"{hora_final} horas.",
        empresa_id=empresa_id_conversacion,
    )
    twiml = f"""
<Response>
    {voz_1}
</Response>
"""

    return Response(content=twiml, media_type="application/xml")