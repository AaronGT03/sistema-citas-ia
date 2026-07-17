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
)
from app.utils import normalizar_fecha, normalizar_hora, normalizar_telefono_mexico
from app.core.config import META_VERIFY_TOKEN

router = APIRouter(tags=["WhatsApp"])

_SIN_VALOR = object()


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
                        "¿Deseas mantener a tu mismo barbero o elegir otro?"
                    )
                    botones = [
                        {"id": "MISMO_PRESTADOR", "title": "Mismo barbero"},
                        {"id": "ELEGIR_PRESTADOR", "title": "Elegir otro"},
                    ]
                else:
                    respuesta = (
                        "Encontré tu cita actual:\n\n"
                        f"Fecha: {cita.fecha}\n"
                        f"Hora: {cita.hora}\n\n"
                        "¿Deseas atenderte con un barbero específico o con "
                        "cualquiera disponible?"
                    )
                    botones = [
                        {"id": "ELEGIR_PRESTADOR", "title": "Elegir barbero"},
                        {"id": "CUALQUIER_PRESTADOR", "title": "Cualquier barbero"},
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
        if "agendar" in mensaje_lower and "cita" in mensaje_lower:
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
                    "Perfecto, te atenderá cualquier barbero disponible.\n\n"
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
                        "Por el momento no hay barberos específicos disponibles "
                        "para ese servicio. Te atenderá cualquier barbero "
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
                    respuesta="Selecciona un barbero",
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
                    texto="Selecciona el barbero con el que deseas atenderte:",
                    boton_texto="Ver barberos",
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
                respuesta = "No reconocí ese barbero. Por favor selecciónalo de la lista."

                filas = [
                    {"id": f"PRESTADOR_{prestador.id}", "title": prestador.nombre}
                    for prestador in prestadores
                ]

                enviar_lista_whatsapp(
                    phone_number_id=numero.phone_number_id,
                    token=numero.token,
                    telefono_cliente=telefono_cliente,
                    texto=respuesta,
                    boton_texto="Ver barberos",
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
                    "¿Deseas atenderte con un barbero específico o con cualquiera "
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
                        {"id": "ELEGIR_PRESTADOR", "title": "Elegir barbero"},
                        {"id": "CUALQUIER_PRESTADOR", "title": "Cualquier barbero"},
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
                    "Perfecto, te atenderá cualquier barbero disponible.\n\n"
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
                        "Por el momento no hay barberos específicos disponibles "
                        "para ese servicio. Te atenderá cualquier barbero "
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
                    respuesta="Selecciona un barbero",
                    paso="PEDIR_PRESTADOR",
                    servicio_id=flujo.servicio_id,
                )

                enviar_lista_whatsapp(
                    phone_number_id=numero.phone_number_id,
                    token=numero.token,
                    telefono_cliente=telefono_cliente,
                    texto="Selecciona el barbero con el que deseas atenderte:",
                    boton_texto="Ver barberos",
                    filas=filas,
                )

                return {"status": "PEDIR_PRESTADOR"}

            respuesta = (
                "¿Deseas atenderte con un barbero específico o con cualquiera "
                "disponible?"
            )

            enviar_botones_whatsapp(
                phone_number_id=numero.phone_number_id,
                token=numero.token,
                telefono_cliente=telefono_cliente,
                texto=respuesta,
                botones=[
                    {"id": "ELEGIR_PRESTADOR", "title": "Elegir barbero"},
                    {"id": "CUALQUIER_PRESTADOR", "title": "Cualquier barbero"},
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
                respuesta = "No reconocí ese barbero. Por favor selecciónalo de la lista."

                filas = [
                    {"id": f"PRESTADOR_{prestador.id}", "title": prestador.nombre}
                    for prestador in prestadores
                ]

                enviar_lista_whatsapp(
                    phone_number_id=numero.phone_number_id,
                    token=numero.token,
                    telefono_cliente=telefono_cliente,
                    texto=respuesta,
                    boton_texto="Ver barberos",
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
        # RESPUESTA DEFAULT SIN OPENAI
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
