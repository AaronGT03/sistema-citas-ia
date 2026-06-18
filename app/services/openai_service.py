from openai import OpenAI

from app.core.config import OPENAI_API_KEY

client = OpenAI(
    api_key=OPENAI_API_KEY
)


def generar_respuesta(prompt_base: str | None, mensaje_usuario: str):
    if not prompt_base:
        prompt_base = "Eres un asistente virtual amable y profesional. Responde de forma breve."

    respuesta = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "system",
                "content": prompt_base
            },
            {
                "role": "user",
                "content": mensaje_usuario
            }
        ]
    )

    return respuesta.output_text