from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import NumeroWhatsApp, Empresa, Conversacion
from app.services.whatsapp_service import enviar_mensaje_whatsapp
from app.services.openai_service import generar_respuesta

router = APIRouter(tags=["WhatsApp"])


@router.post("/webhook-whatsapp")
async def recibir_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()

    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        metadata = value["metadata"]

        numero_receptor = metadata["display_phone_number"]
        if "messages" not in value:
            print("Evento de estado ignorado")
            return {"status": "evento ignorado"}

        mensaje_data = value["messages"][0]
                
        telefono_cliente = mensaje_data["from"]
        mensaje = mensaje_data["text"]["body"]

        numero = (
            db.query(NumeroWhatsApp)
            .filter(
                NumeroWhatsApp.telefono == numero_receptor
            )
            .first()
        )

        if not numero:
            print(
                "Número no registrado:",
                numero_receptor
            )

            return {
                "status": "error",
                "mensaje": "Número no registrado"
            }

        empresa = (
            db.query(Empresa)
            .filter(
                Empresa.id == numero.empresa_id
            )
            .first()
        )

        if not empresa:
            print("Empresa no encontrada")

            return {
                "status": "error",
                "mensaje": "Empresa no encontrada"
            }

        print("\n=== MENSAJE RECIBIDO ===")
        print("EMPRESA:", empresa.nombre)
        print("CLIENTE:", telefono_cliente)
        print("MENSAJE:", mensaje)
        conversacion = Conversacion(
            empresa_id=empresa.id,
            telefono=telefono_cliente,
            canal="WHATSAPP",
            mensaje=mensaje,
            respuesta=None,
            paso=None,
        )

        db.add(conversacion)
        db.commit()
        db.refresh(conversacion)

        print("CONVERSACIÓN GUARDADA:", conversacion.id)
        respuesta = generar_respuesta(
            prompt_base=empresa.prompt_base,
            mensaje_usuario=mensaje
)
        conversacion.respuesta = respuesta

        db.commit()

        print("RESPUESTA:", respuesta)
        enviar_mensaje_whatsapp(
            phone_number_id=numero.phone_number_id,
            token=numero.token,
            telefono_cliente=telefono_cliente,
            mensaje=respuesta
        )

    except Exception as e:
        print("Evento ignorado:", str(e))

        return {
            "status": "evento ignorado"
        }

    return {
        "status": "ok"
    }