from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from fastapi import Form
from fastapi.responses import Response


from database import engine, SessionLocal
from models import Base, Cita, Conversacion
from datetime import datetime

Base.metadata.create_all(bind=engine)

app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

MESES = {
    "enero": "01",
    "febrero": "02",
    "marzo": "03",
    "abril": "04",
    "mayo": "05",
    "junio": "06",
    "julio": "07",
    "agosto": "08",
    "septiembre": "09",
    "setiembre": "09",
    "octubre": "10",
    "noviembre": "11",
    "diciembre": "12",
}

NUMEROS = {
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "dieciséis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiuno": 21,
    "veintidos": 22,
    "veintidós": 22,
    "veintitres": 23,
    "veintitrés": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintiséis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
    "treinta": 30,
    "treinta y uno": 31
}

def normalizar_fecha(texto):
    texto = texto.lower().strip()

    dia = None
    mes = None

    for palabra, numero in NUMEROS.items():
        if palabra in texto:
            dia = str(numero).zfill(2)

    for p in texto.split():
        if p.isdigit():
            dia = p.zfill(2)

    for nombre_mes, numero_mes in MESES.items():
        if nombre_mes in texto:
            mes = numero_mes

    if dia and mes:
        return f"{dia}/{mes}/{datetime.now().year}"

    return texto
    
def normalizar_hora(texto):
    texto = texto.lower().strip()

    hora = None

    for palabra, numero in NUMEROS.items():
        if palabra in texto:
            hora = numero

    for p in texto.split():
        if p.isdigit():
            hora = int(p)

    if hora is None:
        return texto

    if "tarde" in texto:
        if hora < 12:
            hora += 12

    elif "noche" in texto:
        if hora < 12:
            hora += 12

    return f"{hora:02d}:00"


@app.get("/")
def home():
    return {
        "status": "ok",
        "mensaje": "Sistema de citas IA funcionando"
    }


@app.post("/citas")
def crear_cita(
    nombre: str,
    telefono: str,
    fecha: str,
    hora: str,
    db: Session = Depends(get_db)
):
    cita_existente = (
        db.query(Cita)
        .filter(Cita.telefono == telefono)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

    if cita_existente:
        return {
            "error": "Este teléfono ya tiene una cita agendada",
            "acciones_disponibles": [
                "CANCELAR",
                "REPROGRAMAR"
            ],
            "cita_existente": cita_existente
        }

    nueva_cita = Cita(
        nombre=nombre,
        telefono=telefono,
        fecha=fecha,
        hora=hora,
        status="AGENDADA"
    )

    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)

    return {
        "mensaje": "Cita creada correctamente",
        "cita": nueva_cita
    }


@app.get("/citas")
def listar_citas(db: Session = Depends(get_db)):
    citas = db.query(Cita).all()
    return citas

@app.get("/citas/activa/{telefono}")
def obtener_cita_activa(
    telefono: str,
    db: Session = Depends(get_db)
):
    cita = (
        db.query(Cita)
        .filter(Cita.telefono == telefono)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

    if not cita:
        return {
            "tiene_cita": False,
            "mensaje": "No hay citas activas"
        }

    return {
        "tiene_cita": True,
        "cita": cita
    }
@app.get("/citas/{cita_id}")
def obtener_cita(
    cita_id: int,
    db: Session = Depends(get_db)
):
    cita = db.query(Cita).filter(Cita.id == cita_id).first()

    if not cita:
        return {"error": "Cita no encontrada"}

    return cita
@app.put("/citas/{cita_id}/cancelar")
def cancelar_cita(
    cita_id: int,
    db: Session = Depends(get_db)
):
    cita = db.query(Cita).filter(Cita.id == cita_id).first()

    if not cita:
        return {"error": "Cita no encontrada"}

    if cita.status == "CANCELADA":
        return {"error": "La cita ya está cancelada"}

    cita.status = "CANCELADA"

    db.commit()
    db.refresh(cita)

    return {
        "mensaje": "Cita cancelada correctamente",
        "cita": cita
    }


@app.post("/citas/{cita_id}/reprogramar")
def reprogramar_cita(
    cita_id: int,
    nueva_fecha: str,
    nueva_hora: str,
    db: Session = Depends(get_db)
):
    cita_anterior = db.query(Cita).filter(Cita.id == cita_id).first()

    if not cita_anterior:
        return {"error": "Cita no encontrada"}

    if cita_anterior.status == "CANCELADA":
        return {"error": "No se puede reprogramar una cita cancelada"}

    cita_anterior.status = "CANCELADA"

    nueva_cita = Cita(
        nombre=cita_anterior.nombre,
        telefono=cita_anterior.telefono,
        fecha=nueva_fecha,
        hora=nueva_hora,
        status="AGENDADA"
    )

    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)

    return {
        "mensaje": "Cita reprogramada correctamente",
        "cita_cancelada_id": cita_anterior.id,
        "nueva_cita": nueva_cita
    }

@app.post("/llamada")
async def llamada(
    From: str = Form(...),
    db: Session = Depends(get_db)
):
    print(f"Llamada recibida de: {From}")
    telefono_url = From.replace("+", "%2B")

    cita = (
        db.query(Cita)
        .filter(Cita.telefono == From)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

    if cita:
        twiml = f"""
<Response>

    <Gather
        input="speech"
    language="es-MX"
    action="/procesar-cita?telefono={telefono_url}"
    method="POST"
    timeout="8"
    speechTimeout="auto">
        <Say language="es-MX">
            Hola {cita.nombre}.
        </Say>

        <Say language="es-MX">
            Encontré una cita agendada para usted.
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
        action="/procesar-agenda?telefono={telefono_url}"
        method="POST"
        timeout="8"
        speechTimeout="auto">

        <Say language="es-MX">
            HOLA BIENVENIDO AL SISTEMA DE CITAS AUTOMÁTICO.
        </Say>

        <Say language="es-MX">
            No encontré ninguna cita activa.
        </Say>

        <Say language="es-MX">
            Si desea agendar una cita diga:
            agendar.
        </Say>

    </Gather>

</Response>
"""

    return Response(
        content=twiml,
        media_type="application/xml"
    )

@app.post("/procesar-cita")
async def procesar_cita(
    telefono: str,
    SpeechResult: str = Form(""),
    db: Session = Depends(get_db)
):

    telefono = telefono.replace("%2B", "+")
    telefono = telefono if telefono.startswith("+") else "+" + telefono


    print("ENTRO A PROCESAR_CITA")
    print(f"SpeechResult RAW: [{SpeechResult}]")

    respuesta = SpeechResult.lower().strip()

    print(f"Teléfono: {telefono}")
    print(f"Respuesta usuario: {respuesta}")

    cita = (
        db.query(Cita)
        .filter(Cita.telefono == telefono)
        .filter(Cita.status == "AGENDADA")
        .first()
    )

    if not cita:
        mensaje = "No encontré ninguna cita activa."

    elif "cancelar" in respuesta:

        cita.status = "CANCELADA"

        db.commit()
        db.refresh(cita)

        mensaje = "Su cita ha sido cancelada correctamente."

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
@app.post("/procesar-agenda")
async def procesar_agenda(
    telefono: str,
    SpeechResult: str = Form(""),
    db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    print("ENTRO A PROCESAR_AGENDA")
    print(f"SpeechResult RAW: [{SpeechResult}]")

    respuesta = SpeechResult.lower().strip()

    print(f"Teléfono: {telefono}")
    print(f"Respuesta usuario: {respuesta}")

    # resto del código...

    if "agendar" in respuesta or "agenda" in respuesta or "agéndar" in respuesta or "en" in respuesta:

        conversacion_existente = (
            db.query(Conversacion)
            .filter(Conversacion.telefono == telefono)
            .first()
        )

        if conversacion_existente:
            db.delete(conversacion_existente)
            db.commit()

        nueva_conversacion = Conversacion(
            telefono=telefono,
            paso="PEDIR_NOMBRE"
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

        return Response(
            content=twiml,
            media_type="application/xml"
        )

    twiml = """
<Response>
    <Say language="es-MX">
        No entendí su respuesta.
    </Say>
</Response>
"""

    return Response(
        content=twiml,
        media_type="application/xml"
    )
@app.post("/guardar-nombre")
async def guardar_nombre(
    telefono: str,
    SpeechResult: str = Form(""),
    db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    nombre = SpeechResult.strip()

    print("ENTRO A GUARDAR_NOMBRE")
    print(f"Teléfono: {telefono}")
    print(f"Nombre recibido: {nombre}")

    conversacion = (
        db.query(Conversacion)
        .filter(Conversacion.telefono == telefono)
        .first()
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
        return Response(
            content=twiml,
            media_type="application/xml"
        )

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

    return Response(
        content=twiml,
        media_type="application/xml"
    )
@app.post("/guardar-fecha")
async def guardar_fecha(
    telefono: str,
    SpeechResult: str = Form(""),
    db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    fecha = normalizar_fecha(SpeechResult.strip())

    print("ENTRO A GUARDAR_FECHA")
    print(f"Fecha recibida: {fecha}")

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
            media_type="application/xml"
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

    return Response(
        content=twiml,
        media_type="application/xml"
    )
@app.post("/guardar-hora")
async def guardar_hora(
    telefono: str,
    SpeechResult: str = Form(""),
    db: Session = Depends(get_db)
):
    telefono = telefono.replace("%2B", "+")
    telefono = telefono.replace(" ", "")
    telefono = telefono if telefono.startswith("+") else "+" + telefono

    hora = normalizar_hora(SpeechResult.strip())

    print("ENTRO A GUARDAR_HORA")
    print(f"Hora recibida: {hora}")

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
            media_type="application/xml"
        )

    conversacion.hora = hora

    nueva_cita = Cita(
        nombre=conversacion.nombre,
        telefono=telefono,
        fecha=conversacion.fecha,
        hora=hora,
        status="AGENDADA"
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

    return Response(
        content=twiml,
        media_type="application/xml"
    )
