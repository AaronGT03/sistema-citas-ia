from fastapi import APIRouter, Request, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import NumeroWhatsApp, Empresa, Conversacion, Servicio, Cita
from app.services.whatsapp_service import (
    enviar_mensaje_whatsapp,
    enviar_botones_whatsapp,
)
from app.services.openai_service import generar_respuesta
from app.services.citas_service import (
    crear_cita,
    existe_cita_en_horario,
    cancelar_cita,
    reprogramar_cita,
)
from app.utils import normalizar_fecha, normalizar_hora, normalizar_telefono_mexico
from app.core.config import META_VERIFY_TOKEN

router = APIRouter(tags=["WhatsApp"])


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
    flujos = (
        db.query(Conversacion)
        .filter(
            Conversacion.empresa_id == empresa_id,
            Conversacion.telefono == telefono_cliente,
            Conversacion.canal == "WHATSAPP",
            Conversacion.paso != None,
        )
        .all()
    )

    for flujo in flujos:
        flujo.paso = None

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
):
    conversacion = Conversacion(
        empresa_id=empresa_id,
        telefono=telefono_cliente,
        canal="WHATSAPP",
        mensaje=mensaje,
        respuesta=respuesta,
        paso=paso,
        nombre=nombre,
        fecha=fecha,
        hora=hora,
        servicio_id=servicio_id,
    )

    db.add(conversacion)
    db.commit()
    db.refresh(conversacion)

    return conversacion


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
            else:
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

            flujo.paso = None
            db.commit()

            enviar_respuesta(numero, telefono_cliente, respuesta)
            enviar_menu_principal(
                db=db,
                empresa=empresa,
                numero=numero,
                telefono_cliente=telefono_cliente,
            )
            return {"status": "CANCELACION_PROCESADA"}

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
            else:
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

                flujo.paso = None
                db.commit()

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

                flujo.paso = None
                db.commit()

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

            elif existe_cita_en_horario(db, empresa.id, flujo.fecha, nueva_hora):
                respuesta = (
                    "Ya existe una cita programada para esa fecha y hora.\n"
                    "Por favor selecciona otro horario."
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

            flujo.paso = None
            db.commit()

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
            else:
                nueva_cita = reprogramar_cita(
                    db=db,
                    cita_anterior=cita_activa,
                    nueva_fecha=flujo.fecha,
                    nueva_hora=nueva_hora,
                    canal="WHATSAPP",
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

            flujo.paso = None
            db.commit()

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

                flujo.paso = None
                db.commit()

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

            flujo.paso = None
            db.commit()

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
            else:
                respuesta = (
                    f"Perfecto. Registré la fecha {fecha}.\n\n"
                    "¿A qué hora deseas tu cita?"
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

                flujo.paso = None
                db.commit()

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

                flujo.paso = None
                db.commit()

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

            if existe_cita_en_horario(db, empresa.id, flujo.fecha, hora):
                respuesta = (
                    "Ya existe una cita programada para esa fecha y hora.\n"
                    "Por favor selecciona otro horario."
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

            flujo.paso = None
            db.commit()

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

            cita = crear_cita(
                db=db,
                nombre=flujo.nombre,
                telefono=telefono_cliente,
                fecha=flujo.fecha,
                hora=hora,
                empresa_id=empresa.id,
                servicio_id=flujo.servicio_id,
                canal="WHATSAPP",
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

            flujo.paso = None
            db.commit()

            enviar_respuesta(numero, telefono_cliente, respuesta)
            return {"status": "CITA_AGENDADA", "cita_id": cita.id}

        # =========================
        # OPENAI NORMAL
        # =========================
        respuesta = generar_respuesta(
            prompt_base=empresa.prompt_base,
            mensaje_usuario=mensaje,
        )

        guardar_conversacion(
            db=db,
            empresa_id=empresa.id,
            telefono_cliente=telefono_cliente,
            mensaje=mensaje,
            respuesta=respuesta,
        )

        print("RESPUESTA:", respuesta)

        enviar_respuesta(numero, telefono_cliente, respuesta)
        return {"status": "ok"}

    except Exception as e:
        print("Evento ignorado:", str(e))
        return {"status": "evento ignorado"}
