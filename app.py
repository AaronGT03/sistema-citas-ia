from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from fastapi import Form
from fastapi.responses import Response


from database import engine, SessionLocal
from models import Base, Cita

Base.metadata.create_all(bind=engine)

app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
        action="/procesar-cita"
        method="POST"
        timeout="5">

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

</Response>
"""
    else:
        twiml = """
<Response>

    <Gather
        input="speech"
        language="es-MX"
        action="/procesar-agenda"
        method="POST"
        timeout="5">

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
    SpeechResult: str = Form("")
):
    respuesta = SpeechResult.lower()

    print(f"Respuesta usuario: {respuesta}")

    if "cancelar" in respuesta:
        mensaje = "Perfecto. Iniciaremos la cancelación de su cita."

    elif "reprogramar" in respuesta:
        mensaje = "Perfecto. Iniciaremos la reprogramación de su cita."

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
    SpeechResult: str = Form("")
):
    respuesta = SpeechResult.lower()

    print(f"Respuesta usuario: {respuesta}")

    if "agendar" in respuesta:
        mensaje = "Perfecto. Vamos a crear una nueva cita."

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