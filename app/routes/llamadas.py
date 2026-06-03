from fastapi import APIRouter, Depends, Form
from sqlalchemy.orm import Session
from fastapi.responses import Response

from app.database import get_db
from app.models import Cita, Conversacion, Empresa
from app.utils.normalizador import normalizar_fecha, normalizar_hora


router = APIRouter()


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
    db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    print("ENTRO A PROCESAR_CITA")
    print(f"SpeechResult RAW: [{SpeechResult}]")
    print(f"Empresa ID: {empresa_id}")

    respuesta = SpeechResult.lower().strip()

    print(f"Teléfono: {telefono}")
    print(f"Respuesta usuario: {respuesta}")

    cita = (
        db.query(Cita)
        .filter(Cita.telefono == telefono)
        .filter(Cita.empresa_id == empresa_id)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

    if not cita:
        mensaje = "No encontré ninguna cita activa."

    elif "cancelar" in respuesta:
        cita.status = "CANCELADA"

        db.commit()
        db.refresh(cita)

        mensaje = "Su cita ha sido cancelada correctamente. Para poder realizar otra cita, llama nuevamente"

    elif "reprogramar" in respuesta:
        mensaje = "Perfecto. Próximamente iniciaremos la reprogramación."

    else:
        mensaje = "No entendí su respuesta."

    twiml = f"""
<Response>
    <Say language="es-MX">
        {mensaje}
    </Say>
</Response>
"""

    return Response(
        content=twiml,
        media_type="application/xml"
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
            telefono=telefono, empresa_id=empresa_id, paso="PEDIR_NOMBRE"
        )

        db.add(nueva_conversacion)
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
            Perfecto. ¿Cuál es su nombre completo?
        </Say>

    </Gather>

    <Say language="es-MX">
        No recibí su nombre. Intente nuevamente.
    </Say>
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


@router.post("/guardar-fecha")
async def guardar_fecha(
    telefono: str, SpeechResult: str = Form(""), db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    fecha = normalizar_fecha(SpeechResult.strip())

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

    print("ENTRO A GUARDAR_HORA")
    print(f"Hora recibida: {hora}")

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

    conversacion.hora = hora

    nueva_cita = Cita(
        nombre=conversacion.nombre,
        telefono=telefono,
        fecha=conversacion.fecha,
        hora=hora,
        status="AGENDADA",
        empresa_id=conversacion.empresa_id,
    )

    db.add(nueva_cita)

    db.delete(conversacion)

    db.commit()

    twiml = f"""
<Response>

    <Say language="es-MX">
        Perfecto {conversacion.nombre}.
        Su cita fue agendada para el día
        {conversacion.fecha}
        a las
        {hora} horas. Gracias por usar nuestro sistema de citas. 
    </Say>

</Response>
"""

    return Response(content=twiml, media_type="application/xml")
