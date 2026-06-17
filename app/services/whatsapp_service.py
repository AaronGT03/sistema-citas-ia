import requests


def normalizar_destinatario_meta(telefono: str):
    telefono = telefono.replace("+", "").replace(" ", "").replace("-", "")

    if telefono.startswith("521") and len(telefono) == 13:
        telefono = "52" + telefono[3:]

    return telefono


def enviar_mensaje_whatsapp(
    phone_number_id: str,
    token: str,
    telefono_cliente: str,
    mensaje: str
):
    telefono_cliente = normalizar_destinatario_meta(telefono_cliente)

    url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": telefono_cliente,
        "type": "text",
        "text": {
            "body": mensaje
        }
    }

    print("DESTINATARIO NORMALIZADO:", telefono_cliente)

    response = requests.post(url, headers=headers, json=payload)

    print("META STATUS:", response.status_code)
    print("META RESPONSE:", response.text)

    return response.json()