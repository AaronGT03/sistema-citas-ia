from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from openai import OpenAI

from app.core.config import OPENAI_API_KEY

client = OpenAI(
    api_key=OPENAI_API_KEY
)

MODELO_INTERPRETACION = "gpt-4.1-mini"


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


# =====================================================================
# Capa conversacional/interpretativa de OpenAI (llamadas y WhatsApp).
#
# OpenAI nunca crea, modifica, cancela ni reprograma citas directamente:
# solo interpreta texto libre y devuelve datos estructurados y validados.
# El backend decide qué hacer con esos datos usando las funciones de
# app/services/citas_service.py. Si OpenAI falla, no responde, o la
# salida no valida, todas las funciones de aquí devuelven None (o el
# mensaje de respaldo indicado), y el llamador debe conservar su propia
# lógica de respaldo existente.
# =====================================================================

INTENCIONES_PERMITIDAS = Literal[
    "SALUDO",
    "INFORMACION_EMPRESA",
    "CONSULTAR_SERVICIOS",
    "CONSULTAR_PRECIOS",
    "CONSULTAR_HORARIOS",
    "CONSULTAR_UBICACION",
    "CONSULTAR_PROMOCIONES",
    "AGENDAR",
    "CONSULTAR_CITA",
    "CANCELAR",
    "REPROGRAMAR",
    "SELECCIONAR_PRESTADOR",
    "ELEGIR_HORA",
    "SIN_HORA_ESPECIFICA",
    "CONFIRMAR",
    "CORREGIR_DATO",
    "FUERA_DE_CONTEXTO",
    "NO_ENTENDIDO",
]

FRANJAS_PERMITIDAS = Literal["MANANA", "TARDE", "NOCHE", "CUALQUIER_HORA"]


class SalidaOpenAI(BaseModel):
    dentro_del_dominio: bool
    intencion: INTENCIONES_PERMITIDAS
    nombre: str | None = None
    servicio: str | None = None
    fecha: str | None = None
    hora: str | None = None
    sin_hora_especifica: bool = False
    franja_preferida: FRANJAS_PERMITIDAS | None = None
    prestador: str | None = None
    cualquier_prestador: bool = False
    mensaje_respuesta: str | None = None


def _describir_servicio(servicio: dict) -> str:
    partes = [f"- {servicio['nombre']}"]

    detalles = []
    if servicio.get("duracion_minutos"):
        detalles.append(f"{servicio['duracion_minutos']} min")
    if servicio.get("precio") is not None:
        detalles.append(f"${servicio['precio']}")

    if detalles:
        partes.append(f" ({', '.join(detalles)})")

    if servicio.get("descripcion"):
        partes.append(f": {servicio['descripcion']}")

    return "".join(partes)


def _describir_prestador(prestador: dict) -> str:
    texto = f"- {prestador['nombre']}"

    if prestador.get("servicios"):
        texto += f" (realiza: {', '.join(prestador['servicios'])})"

    return texto


def construir_prompt_sistema(empresa, contexto: dict) -> str:
    # Import local para evitar import circular (citas_service no importa
    # nada de openai_service, así que esta dirección es segura en runtime,
    # pero el import a nivel de módulo crearía un ciclo con los routers).
    from app.services.citas_service import vocabulario_por_giro

    servicios_texto = "\n".join(
        _describir_servicio(s) for s in contexto.get("servicios", [])
    ) or "Sin servicios registrados."

    prestadores_texto = "\n".join(
        _describir_prestador(p) for p in contexto.get("prestadores", [])
    ) or "No aplica (esta empresa no usa prestadores)."

    instrucciones_negocio = ""
    if empresa.prompt_base:
        instrucciones_negocio = f"\nInstrucciones adicionales del negocio:\n{empresa.prompt_base}\n"

    vocabulario = vocabulario_por_giro(contexto.get("giro"))

    adaptacion_giro = f"""
Adaptación al giro del negocio (solo cambia tu forma de hablar, nunca la lógica):
- Al prestador llámalo "{vocabulario['prestador']}" (nunca "prestador" a secas).
- A la cita llámala "{vocabulario['cita']}" cuando suene más natural para este giro
  (ej. "agendaré tu {vocabulario['cita']}").
- Adapta tus preguntas y respuestas al contexto de este giro: usa el vocabulario
  típico del sector y, si ayuda a concretar la cita, haz preguntas naturales de
  ese contexto (ej. en bienes raíces: qué zona le interesa; en un taller: qué
  problema presenta el vehículo; en un consultorio: el motivo de la consulta).
  Lo que el cliente responda a esas preguntas es solo conversación: los campos
  estructurados que devuelves son siempre los mismos (servicio, fecha, hora,
  prestador), sin campos nuevos.
"""

    return f"""
Eres el asistente virtual de {empresa.nombre}.

Datos reales de la empresa (nunca inventes datos distintos a estos):
- Giro: {contexto.get('giro') or 'No especificado'}
- Horario de atención: {contexto.get('horario_inicio')} a {contexto.get('horario_fin')}
- Usa varios prestadores/profesionales: {'sí' if contexto.get('usa_prestadores') else 'no'}
- Permite citas sin hora específica: {'sí' if contexto.get('permite_citas_sin_hora') else 'no'}

Servicios disponibles:
{servicios_texto}

Prestadores/profesionales disponibles:
{prestadores_texto}
{instrucciones_negocio}{adaptacion_giro}
Reglas estrictas:
- Solo puedes responder preguntas relacionadas con esta empresa, sus servicios, precios,
  horarios, ubicación, promociones, políticas, prestadores y gestión de citas.
- No respondas preguntas de cultura general ni asuntos ajenos a la empresa (política,
  deportes, noticias, programación, historia, tareas escolares, medicina, otras empresas, etc.).
- Si el usuario pregunta algo fuera de ese ámbito, marca dentro_del_dominio=false,
  intencion=FUERA_DE_CONTEXTO, y responde exactamente:
  "Solo puedo ayudarte con información, servicios y citas de {empresa.nombre}."
- Utiliza únicamente la información proporcionada arriba. No inventes servicios, precios,
  promociones, horarios, prestadores ni disponibilidad que no estén en estos datos.
- Nunca confirmes que una cita fue creada, cancelada o reprogramada: eso solo lo hace el
  backend después de validar la base de datos real. Tú solo interpretas y sugieres.
- Nunca afirmes que existe disponibilidad en un horario: eso solo lo determina el backend.
""".strip()


REGLAS_CLASIFICACION = """
Reglas de clasificación de intención (solo para tu salida estructurada, nunca las
menciones ni las incluyas en mensaje_respuesta):
- Usa AGENDAR únicamente cuando el cliente exprese de forma clara que quiere agendar,
  reservar o pedir una cita (ej. "quiero una cita", "agéndame un corte", "me gustaría
  reservar para mañana").
- Si el cliente solo pide información — aunque mencione un servicio o un prestador —
  NO es AGENDAR: usa la intención de consulta que corresponda:
  - CONSULTAR_SERVICIOS: qué servicios ofrecen.
  - CONSULTAR_PRECIOS: cuánto cuesta un servicio.
  - CONSULTAR_HORARIOS: a qué hora abren/cierran, qué horarios manejan, o qué
    horarios/horas tienen disponibles ("¿a qué hora tienes disponible?",
    "¿qué horarios hay?"). Esto aplica incluso si se le acaba de pedir la hora:
    preguntar por disponibilidad nunca es dar una hora.
  - CONSULTAR_UBICACION, CONSULTAR_PROMOCIONES según el caso.
  - INFORMACION_EMPRESA: cualquier otra pregunta informativa, incluyendo qué
    prestadores/profesionales hay disponibles o quién realiza un servicio.
- "¿Qué prestadores/profesionales tienen disponibles?" es una consulta de información
  (INFORMACION_EMPRESA), nunca AGENDAR ni SELECCIONAR_PRESTADOR.
- SELECCIONAR_PRESTADOR y ELEGIR_HORA solo aplican cuando hay un paso de conversación
  activo que espera esa respuesta.
- cualquier_prestador=true ÚNICAMENTE cuando el cliente diga de forma explícita que
  no le importa quién lo atienda ("con cualquiera", "el que sea", "quien esté
  disponible", "no tengo preferencia"). Si el cliente simplemente no menciona ningún
  prestador, deja cualquier_prestador=false y prestador=null: el sistema le
  preguntará después si desea un profesional específico.
- Si hay un paso de conversación activo y el mensaje es la respuesta esperada de ese
  paso (dice su nombre cuando se le pidió el nombre, una fecha cuando se le pidió la
  fecha, una hora cuando se le pidió la hora), clasifícalo como CONFIRMAR y nunca como
  consulta. Usa las intenciones CONSULTAR_* o INFORMACION_EMPRESA solo cuando el
  cliente claramente está haciendo una pregunta en lugar de dar el dato pedido.
""".strip()


def _mensajes_interpretacion(empresa, contexto: dict, paso_actual: str | None, mensaje_usuario: str):
    prompt_sistema = construir_prompt_sistema(empresa, contexto) + "\n\n" + REGLAS_CLASIFICACION

    contexto_paso = (
        f"\nPaso actual de la conversación: {paso_actual}. "
        "Extrae únicamente lo relevante para ese paso; deja el resto en null."
        if paso_actual
        else "\nNo hay un paso de conversación activo (mensaje libre)."
    )

    contexto_fecha = (
        f"\nHoy es {datetime.now().strftime('%Y-%m-%d')} (formato AAAA-MM-DD). "
        "Usa esta fecha como referencia para resolver expresiones relativas "
        "('mañana', 'el próximo viernes', fechas sin año, etc.) y para el "
        "campo 'fecha' de tu respuesta, siempre en formato AAAA-MM-DD y en el "
        "futuro respecto a hoy."
    )

    return [
        {"role": "system", "content": prompt_sistema + contexto_paso + contexto_fecha},
        {"role": "user", "content": mensaje_usuario},
    ]


def interpretar_mensaje(
    empresa,
    contexto: dict,
    paso_actual: str | None,
    mensaje_usuario: str,
) -> SalidaOpenAI | None:
    if not OPENAI_API_KEY or not mensaje_usuario or not mensaje_usuario.strip():
        return None

    try:
        respuesta = client.responses.parse(
            model=MODELO_INTERPRETACION,
            input=_mensajes_interpretacion(empresa, contexto, paso_actual, mensaje_usuario),
            text_format=SalidaOpenAI,
        )

        return respuesta.output_parsed
    except Exception as error:
        print(f"[openai_service] interpretar_mensaje falló (empresa_id={empresa.id}): {type(error).__name__}")
        return None


def responder_pregunta_empresa(empresa, contexto: dict, mensaje_usuario: str) -> str:
    respaldo = f"Solo puedo ayudarte con información, servicios y citas de {empresa.nombre}."

    if not OPENAI_API_KEY or not mensaje_usuario or not mensaje_usuario.strip():
        return respaldo

    try:
        prompt_sistema = construir_prompt_sistema(empresa, contexto)

        respuesta = client.responses.create(
            model=MODELO_INTERPRETACION,
            input=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": mensaje_usuario},
            ],
        )

        texto = (respuesta.output_text or "").strip()

        return texto or respaldo
    except Exception as error:
        print(f"[openai_service] responder_pregunta_empresa falló (empresa_id={empresa.id}): {type(error).__name__}")
        return respaldo
