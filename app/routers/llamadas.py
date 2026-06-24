from fastapi import APIRouter, Depends, Form
from sqlalchemy.orm import Session
from fastapi.responses import Response

from app.database import get_db
from app.models import Cita, Conversacion, Empresa, Servicio
from app.utils import normalizar_fecha, normalizar_hora
from app.services.citas_service import (
    existe_cita_en_horario,
    crear_cita,
    cancelar_cita,
    reprogramar_cita,
    fecha_ya_paso,
    hora_ya_paso,
    horario_choca_con_duracion,
    obtener_horarios_disponibles,
)

router = APIRouter()



def respuesta_horario_ocupado():
    twiml = """
<Response>
    <Say language="es-MX">
        Ya existe una cita programada para esa fecha y hora.
        Por favor seleccione otro horario.
    </Say>
</Response>
"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/llamada")
async def llamada(
    From: str = Form(...), To: str = Form(...), db: Session = Depends(get_db)
):
    print(f"Llamada recibida de: {From}")
    print(f"Número Twilio recibido: {To}")

    telefono_cliente = From.replace(" ", "")
    telefono_empresa = To.replace(" ", "")

    telefono_url = telefono_cliente.replace("+", "%2B")

    empresa = (
        db.query(Empresa).filter(Empresa.telefono_twilio == telefono_empresa).first()
    )

    if not empresa:
        twiml = """
<Response>
    <Say language="es-MX">
        Este número no tiene una empresa configurada.
    </Say>
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
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/procesar-cita?telefono={telefono_url}&amp;empresa_id={empresa.id}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            Hola {cita.nombre}. Encontré una cita agendada para usted en {empresa.nombre}.
        </Say>

        <Say language="es-MX">
            Su cita es el día {cita.fecha} a las {cita.hora}.
        </Say>

        <Say language="es-MX">
            Diga cancelar o reprogramar.
        </Say>
    </Gather>

    <Say language="es-MX">
        No recibí ninguna respuesta. Intente nuevamente.
    </Say>
</Response>
"""
    else:
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/procesar-agenda?telefono={telefono_url}&amp;empresa_id={empresa.id}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            Hola, bienvenido a {empresa.nombre}.
        </Say>

        <Say language="es-MX">
            No encontré ninguna cita activa.
        </Say>

        <Say language="es-MX">
            Si desea agendar una cita diga agendar.
        </Say>
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
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

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
        return Response(
            content="""
<Response>
    <Say language="es-MX">
        No encontré ninguna cita activa.
    </Say>
</Response>
""",
            media_type="application/xml",
        )

    if "cancel" in respuesta:
        cancelar_cita(db, cita)

        twiml = """
<Response>
    <Say language="es-MX">
        Su cita ha sido cancelada correctamente.
        Si desea agendar una nueva cita, por favor vuelva a llamar.
    </Say>
</Response>
"""

        print("=== CANCELACION ===")
        print(twiml)

        return Response(content=twiml, media_type="application/xml")

    elif "reprogramar" in respuesta:
        conversacion_existente = (
            db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
        )

        if conversacion_existente:
            db.delete(conversacion_existente)
            db.commit()

        nueva_conversacion = Conversacion(
            telefono=telefono, empresa_id=empresa_id, paso="REPROGRAMAR_FECHA"
        )

        db.add(nueva_conversacion)
        db.commit()

        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            Perfecto. ¿Para qué nueva fecha desea reprogramar su cita?
        </Say>

    </Gather>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    return Response(
        content="""
<Response>
    <Say language="es-MX">
        No entendí su respuesta.
    </Say>
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
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    print("ENTRO A PROCESAR_AGENDA")
    print(f"SpeechResult RAW: [{SpeechResult}]")
    print(f"Empresa ID: {empresa_id}")

    respuesta = SpeechResult.lower().strip()

    print(f"Teléfono: {telefono}")
    print(f"Respuesta usuario: {respuesta}")

    if (
        "agendar" in respuesta
        or "agenda" in respuesta
        or "agéndar" in respuesta
        or "en" in respuesta
    ):
        conversacion_existente = (
            db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
        )

        if conversacion_existente:
            db.delete(conversacion_existente)
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

        lista_servicios = ""

        for i, servicio in enumerate(servicios, start=1):
            lista_servicios += f"""
            <Say language="es-MX">
                {i}. {servicio.nombre}
            </Say>
            """

        twiml = f"""
<Response>

    <Gather
    input="dtmf"
    numDigits="1"
    action="/guardar-servicio?telefono={telefono}"
    method="POST"
    timeout="10">

        <Say language="es-MX">
            Perfecto.
            Seleccione uno de los siguientes servicios.
        </Say>

        {lista_servicios}

        <Say language="es-MX">
    Presione en su teléfono el número del servicio que desea.
</Say>

    </Gather>

</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    twiml = """
<Response>
    <Say language="es-MX">
        No entendí su respuesta.
    </Say>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/guardar-nombre")
async def guardar_nombre(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    nombre = SpeechResult.strip()

    print("ENTRO A GUARDAR_NOMBRE")
    print(f"Teléfono: {telefono}")
    print(f"Nombre recibido: {nombre}")

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    print(f"Conversacion encontrada: {conversacion}")

    if not conversacion:
        twiml = """
<Response>
    <Say language="es-MX">
        No encontré una conversación activa. Intente llamar nuevamente.
    </Say>
</Response>
"""
        return Response(content=twiml, media_type="application/xml")

    conversacion.nombre = nombre
    conversacion.paso = "PEDIR_FECHA"

    db.commit()

    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            Gracias {nombre}. ¿Qué fecha desea para su cita?
        </Say>

    </Gather>

    <Say language="es-MX">
        No recibí la fecha. Intente nuevamente.
    </Say>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/guardar-servicio")
async def guardar_servicio(
    telefono: str,
    Digits: str = Form(""),
    db: Session = Depends(get_db),
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    opcion = Digits.strip()
    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        return Response(
            content="""
<Response>
    <Say language="es-MX">
        No encontré una conversación activa. Intente llamar nuevamente.
    </Say>
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
        lista_servicios = ""

        for i, servicio in enumerate(servicios, start=1):
            lista_servicios += f"""
            <Say language="es-MX">
                {i}. {servicio.nombre}
            </Say>
            """

        twiml = f"""
<Response>
    <Gather
    input="dtmf"
    numDigits="1"
    action="/guardar-servicio?telefono={telefono}"
    method="POST"
    timeout="10">

        No encontré ese servicio. Por favor presione un número válido.

        {lista_servicios}

    </Gather>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    conversacion.servicio_id = servicio_seleccionado.id
    conversacion.paso = "PEDIR_NOMBRE"

    db.commit()

    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-nombre?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            Perfecto, seleccionó {servicio_seleccionado.nombre}.
            ¿Cuál es su nombre completo?
        </Say>

    </Gather>

    <Say language="es-MX">
        No recibí su nombre. Intente nuevamente.
    </Say>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/guardar-fecha")
async def guardar_fecha(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    fecha = normalizar_fecha(SpeechResult.strip())

    if fecha and fecha_ya_paso(fecha):
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/guardar-fecha?telefono={telefono}"
            method="POST">

            <Say language="es-MX">
                La fecha indicada ya pasó.
                Por favor indique una fecha futura.
            </Say>

        </Gather>
    </Response>
    """
        return Response(content=twiml, media_type="application/xml")

    if fecha is None:
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            No entendí la fecha. Por favor diga una fecha como quince de junio, mañana o pasado mañana.
        </Say>

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
        return Response(
            content="""
<Response>
    <Say language="es-MX">
        No encontré una conversación activa.
    </Say>
</Response>
""",
            media_type="application/xml",
        )

    conversacion.fecha = fecha
    conversacion.paso = "PEDIR_HORA"

    db.commit()

    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-hora?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            Perfecto. ¿A qué hora desea la cita?
        </Say>

    </Gather>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/guardar-hora")
async def guardar_hora(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    hora = normalizar_hora(SpeechResult.strip())

    if hora == "AMBIGUA":
        conversacion = (
            db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
        )

        if not conversacion:
            return Response(
                content="""
<Response>
    <Say language="es-MX">
        No encontré una conversación activa.
    </Say>
</Response>
""",
                media_type="application/xml",
            )

        conversacion.hora = SpeechResult.strip()
        conversacion.paso = "ACLARAR_HORA"
        db.commit()

        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/aclarar-hora?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            ¿Se refiere a las {SpeechResult} de la mañana o de la tarde?
        </Say>

    </Gather>

    <Say language="es-MX">
        No recibí la aclaración. Intente nuevamente.
    </Say>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    if hora is None:
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/guardar-hora?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            No entendí la hora.
        </Say>

        <Say language="es-MX">
            Por favor diga una hora como diez de la mañana, cinco de la tarde o tres y media.
        </Say>

    </Gather>

    <Say language="es-MX">
        No recibí ninguna respuesta. Intente nuevamente.
    </Say>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")
    
    conversacion = (
        db.query(Conversacion)
        .filter(Conversacion.telefono == telefono)
        .first()
    )

    if not conversacion:
        return Response(
            content="""
<Response>
    <Say language="es-MX">
        No encontré una conversación activa.
    </Say>
</Response>
""",
            media_type="application/xml",
        )

    if hora_ya_paso(conversacion.fecha, hora):
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/guardar-hora?telefono={telefono}"
            method="POST">

            <Say language="es-MX">
                Esa hora ya pasó.
                Por favor indique una hora futura.
            </Say>

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
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST"
            timeout="8"
            speechTimeout="auto">

            <Say language="es-MX">
                Lo sentimos.
                El horario de atención es de {empresa.horario_inicio} a {empresa.horario_fin}.
                Por favor indique otra hora.
            </Say>

        </Gather>

        <Say language="es-MX">
            No recibí la hora. Intente nuevamente.
        </Say>
    </Response>
    """

        return Response(content=twiml, media_type="application/xml")
    
    cita_ocupada = horario_choca_con_duracion(
        db=db,
        empresa_id=conversacion.empresa_id,
        fecha=conversacion.fecha,
        hora=hora,
        servicio_id=conversacion.servicio_id,
    )

    if cita_ocupada:
        return respuesta_horario_ocupado()

    nueva_cita = Cita(
        nombre=conversacion.nombre,
        telefono=telefono,
        fecha=conversacion.fecha,
        hora=hora,
        status="AGENDADA",
        empresa_id=conversacion.empresa_id,
        servicio_id=conversacion.servicio_id,
    )

    db.add(nueva_cita)
    db.delete(conversacion)
    db.commit()

    twiml = f"""
<Response>
    <Say language="es-MX">
        Perfecto {conversacion.nombre}.
        Su cita fue agendada para el día {conversacion.fecha}
        a las {hora} horas.
        Gracias por usar nuestro sistema de citas.
    </Say>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/aclarar-hora")
async def aclarar_hora(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    respuesta = SpeechResult.lower().strip()

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        return Response(
            content="""
<Response>
    <Say language="es-MX">
        No encontré una conversación activa.
    </Say>
</Response>
""",
            media_type="application/xml",
        )

    hora_original = conversacion.hora

    try:
        numero = int("".join(filter(str.isdigit, hora_original)))
    except:
        return Response(
            content="""
<Response>
    <Say language="es-MX">
        No pude identificar la hora.
    </Say>
</Response>
""",
            media_type="application/xml",
        )

    if "tarde" in respuesta or "noche" in respuesta:
        hora_final = f"{numero + 12:02d}:00"
    else:
        hora_final = f"{numero:02d}:00"

    if hora_ya_paso(conversacion.fecha, hora_final):
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/guardar-hora?telefono={telefono}"
            method="POST">

            <Say language="es-MX">
                Esa hora ya pasó.
                Por favor indique una hora futura.
            </Say>

        </Gather>
    </Response>
    """
        return Response(content=twiml, media_type="application/xml")
    
    empresa = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    if hora_final < empresa.horario_inicio or hora_final > empresa.horario_fin:
        twiml = f"""
    <Response>

        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST"
            timeout="8"
            speechTimeout="auto">

            <Say language="es-MX">
                Lo sentimos.
                Nuestro horario de atención es de
                {empresa.horario_inicio}
                a
                {empresa.horario_fin}.
                Por favor indique otra hora.
            </Say>

        </Gather>

        <Say language="es-MX">
            No recibí ninguna respuesta.
        </Say>

    </Response>
    """
        return Response(content=twiml, media_type="application/xml")
    cita_ocupada = horario_choca_con_duracion(
        db=db,
        empresa_id=conversacion.empresa_id,
        fecha=conversacion.fecha,
        hora=hora_final,
        servicio_id=conversacion.servicio_id,
    )

    if cita_ocupada:
        return respuesta_horario_ocupado()

    nueva_cita = Cita(
        nombre=conversacion.nombre,
        telefono=telefono,
        fecha=conversacion.fecha,
        hora=hora_final,
        status="AGENDADA",
        empresa_id=conversacion.empresa_id,
        servicio_id=conversacion.servicio_id,
    )

    db.add(nueva_cita)

    db.delete(conversacion)

    db.commit()

    twiml = f"""
<Response>
    <Say language="es-MX">
        Perfecto.
        Su cita fue agendada para el día
        {nueva_cita.fecha}
        a las
        {hora_final} horas.
    </Say>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/reprogramar-fecha")
async def reprogramar_fecha(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    fecha = normalizar_fecha(SpeechResult.strip())

    if fecha and fecha_ya_paso(fecha):
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-fecha?telefono={telefono}"
            method="POST">

            <Say language="es-MX">
                La fecha indicada ya pasó.
                Por favor indique una fecha futura.
            </Say>

        </Gather>
    </Response>
    """
        return Response(content=twiml, media_type="application/xml")


    if fecha is None:
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-fecha?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            No entendí la fecha. Por favor diga una fecha como quince de junio, mañana o pasado mañana.
        </Say>

    </Gather>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        return Response(
            content="""
<Response>
    <Say language="es-MX">
        No encontré una conversación activa.
    </Say>
</Response>
""",
            media_type="application/xml",
        )

    conversacion.fecha = fecha
    conversacion.paso = "REPROGRAMAR_HORA"

    db.commit()

    twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-hora?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            Perfecto. ¿A qué nueva hora desea reprogramar su cita?
        </Say>

    </Gather>

    <Say language="es-MX">
        No recibí la hora. Intente nuevamente.
    </Say>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/reprogramar-hora")
async def reprogramar_hora(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    hora = normalizar_hora(SpeechResult.strip())

    print("ENTRO A REPROGRAMAR_HORA")
    print(f"Hora normalizada: {hora}")

    if hora == "AMBIGUA":
        conversacion = (
            db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
        )

        if not conversacion:
            return Response(
                content="""
<Response>
    <Say language="es-MX">
        No encontré una conversación activa.
    </Say>
</Response>
""",
                media_type="application/xml",
            )

        conversacion.hora = SpeechResult.strip()
        conversacion.paso = "ACLARAR_HORA_REPROGRAMAR"
        db.commit()

        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/aclarar-hora-reprogramar?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            ¿Se refiere a las {SpeechResult} de la mañana o de la tarde?
        </Say>

    </Gather>

    <Say language="es-MX">
        No recibí la aclaración. Intente nuevamente.
    </Say>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    if hora is None:
        twiml = f"""
<Response>
    <Gather
        input="speech"
        language="es-MX"
        action="/reprogramar-hora?telefono={telefono}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            No entendí la hora.
        </Say>

        <Say language="es-MX">
            Por favor diga una hora como diez de la mañana, cinco de la tarde o tres y media.
        </Say>

    </Gather>

    <Say language="es-MX">
        No recibí ninguna respuesta. Intente nuevamente.
    </Say>
</Response>
"""

        return Response(content=twiml, media_type="application/xml")

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )
    if not conversacion:
        return Response(
            content="""
    <Response>
        <Say language="es-MX">
            No encontré una conversación activa.
        </Say>
    </Response>
    """,
            media_type="application/xml",
        )

    if hora_ya_paso(conversacion.fecha, hora):
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST">

            <Say language="es-MX">
                Esa hora ya pasó.
                Por favor indique una hora futura.
            </Say>

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
        return Response(
            content="""
<Response>
    <Say language="es-MX">
        No encontré una cita activa para reprogramar.
    </Say>
</Response>
""",
            media_type="application/xml",
        )

    empresa = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    if hora < empresa.horario_inicio or hora > empresa.horario_fin:
        twiml = f"""
    <Response>

        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST"
            timeout="8"
            speechTimeout="auto">

            <Say language="es-MX">
                Lo sentimos.
                Nuestro horario de atención es de
                {empresa.horario_inicio}
                a
                {empresa.horario_fin}.
                Por favor indique otra hora.
            </Say>

        </Gather>

        <Say language="es-MX">
            No recibí ninguna respuesta.
        </Say>

    </Response>
    """
        return Response(content=twiml, media_type="application/xml")
    
    cita_ocupada = horario_choca_con_duracion(
        db=db,
        empresa_id=conversacion.empresa_id,
        fecha=conversacion.fecha,
        hora=hora,
        servicio_id=conversacion.servicio_id,
        cita_ignorar_id=cita_anterior.id,
    )

    if cita_ocupada:
        return respuesta_horario_ocupado()
    
    nueva_cita = reprogramar_cita(
        db=db,
        cita_anterior=cita_anterior,
        nueva_fecha=conversacion.fecha,
        nueva_hora=hora,
        canal="LLAMADA",
    )

    db.delete(conversacion)
    db.commit()

    twiml = f"""
<Response>
    <Say language="es-MX">
        Su cita fue reprogramada correctamente para el día
        {nueva_cita.fecha}
        a las
        {nueva_cita.hora} horas.
    </Say>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/aclarar-hora-reprogramar")
async def aclarar_hora_reprogramar(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    respuesta = SpeechResult.lower().strip()

    conversacion = (
        db.query(Conversacion).filter(Conversacion.telefono == telefono).first()
    )

    if not conversacion:
        return Response(
            content="""
<Response>
    <Say language="es-MX">
        No encontré una conversación activa.
    </Say>
</Response>
""",
            media_type="application/xml",
        )

    hora_original = conversacion.hora

    try:
        numero = int("".join(filter(str.isdigit, hora_original)))
    except:
        return Response(
            content="""
<Response>
    <Say language="es-MX">
        No pude identificar la hora.
    </Say>
</Response>
""",
            media_type="application/xml",
        )

    if "tarde" in respuesta or "noche" in respuesta:
        hora_final = f"{numero + 12:02d}:00"
    else:
        hora_final = f"{numero:02d}:00"

    if hora_ya_paso(conversacion.fecha, hora_final):
        twiml = f"""
    <Response>
        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST">

            <Say language="es-MX">
                Esa hora ya pasó.
                Por favor indique una hora futura.
            </Say>

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
        return Response(
            content="""
<Response>
    <Say language="es-MX">
        No encontré una cita activa para reprogramar.
    </Say>
</Response>
""",
            media_type="application/xml",
        )

    empresa = db.query(Empresa).filter(Empresa.id == conversacion.empresa_id).first()

    if hora_final < empresa.horario_inicio or hora_final > empresa.horario_fin:
        twiml = f"""
    <Response>

        <Gather
            input="speech"
            language="es-MX"
            action="/reprogramar-hora?telefono={telefono}"
            method="POST"
            timeout="8"
            speechTimeout="auto">

            <Say language="es-MX">
                Lo sentimos.
                Nuestro horario de atención es de
                {empresa.horario_inicio}
                a
                {empresa.horario_fin}.
                Por favor indique otra hora.
            </Say>

        </Gather>

        <Say language="es-MX">
            No recibí ninguna respuesta.
        </Say>

    </Response>
    """
        return Response(content=twiml, media_type="application/xml")
    cita_ocupada = horario_choca_con_duracion(
        db=db,
        empresa_id=conversacion.empresa_id,
        fecha=conversacion.fecha,
        hora=hora_final,
        servicio_id=conversacion.servicio_id,
        cita_ignorar_id=cita_anterior.id,
    )

    if cita_ocupada:
        return respuesta_horario_ocupado()
    nueva_cita = reprogramar_cita(
        db=db,
        cita_anterior=cita_anterior,
        nueva_fecha=conversacion.fecha,
        nueva_hora=hora_final,
        canal="LLAMADA",
)
    db.delete(conversacion)
    db.commit()
    twiml = f"""
<Response>
    <Say language="es-MX">
        Su cita fue reprogramada correctamente para el día
        {nueva_cita.fecha}
        a las
        {hora_final} horas.
    </Say>
</Response>
"""

    return Response(content=twiml, media_type="application/xml")