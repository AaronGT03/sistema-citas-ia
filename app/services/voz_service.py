from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape

from app.services.elevenlabs_service import generar_audio_elevenlabs
from app.utils import normalizar_texto_voz

# =====================================================================
# Puente entre el texto de una respuesta y el TwiML que la reproduce.
#
# Intenta ElevenLabs primero (<Play>); si generar_audio_elevenlabs no pudo
# generar el audio por cualquier motivo, cae automáticamente a <Say> de
# Twilio con el mismo texto — la llamada nunca se interrumpe por un fallo
# de ElevenLabs.
# =====================================================================


def construir_bloque_voz(texto: str, empresa_id: int | None = None) -> str:
    """Devuelve el fragmento TwiML (<Play> o <Say> de respaldo) para decir
    `texto`. `texto` debe ir sin escapar — el escapado ocurre una sola vez
    aquí adentro. El respaldo <Say> también usa el texto normalizado para
    voz (precios/horas en palabras), no el texto crudo."""
    audio_url = generar_audio_elevenlabs(texto, empresa_id=empresa_id)

    if audio_url:
        return f"<Play>{escape(audio_url)}</Play>"

    texto_hablado = normalizar_texto_voz(texto) or texto

    return f'<Say language="es-MX" voice="Polly.Mia-Neural">{escape(texto_hablado)}</Say>'


def construir_bloques_voz(*textos: str, empresa_id: int | None = None) -> tuple[str, ...]:
    """Igual que construir_bloque_voz pero para varias frases de una misma
    respuesta: las genera en paralelo (una llamada HTTP a ElevenLabs por
    frase, todas a la vez) en vez de una tras otra, así la latencia total
    es la de la más lenta y no la suma de todas."""
    if len(textos) <= 1:
        return tuple(construir_bloque_voz(texto, empresa_id=empresa_id) for texto in textos)

    with ThreadPoolExecutor(max_workers=len(textos)) as executor:
        return tuple(
            executor.map(lambda texto: construir_bloque_voz(texto, empresa_id=empresa_id), textos)
        )
