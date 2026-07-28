import hashlib
import logging
import os
import time
import uuid

import requests

from app.core.config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_VOICE_ID,
    ELEVENLABS_MODEL_ID,
    ELEVENLABS_OUTPUT_FORMAT,
    ELEVENLABS_STABILITY,
    ELEVENLABS_SIMILARITY_BOOST,
    ELEVENLABS_TIMEOUT_SECONDS,
    PUBLIC_BASE_URL,
    AUDIO_CACHE_DIR,
    AUDIO_TTL_SECONDS,
)
from app.utils import normalizar_texto_voz

# =====================================================================
# Servicio central de texto a voz (ElevenLabs).
#
# ElevenLabs solo convierte texto ya generado (por reglas fijas o por
# app/services/openai_service.py) en audio. Nunca interpreta la llamada, ni
# consulta la base de datos, ni decide el flujo — eso sigue siendo
# responsabilidad exclusiva de llamadas.py/openai_service.py/citas_service.py.
#
# Cualquier fallo (key inválida, timeout, red, respuesta vacía, error al
# guardar el archivo) se captura aquí y la función devuelve None: el
# llamador (app/services/voz_service.py) cae automáticamente a <Say> de
# Twilio. Esta función nunca propaga una excepción.
# =====================================================================

logger = logging.getLogger("elevenlabs_service")

_ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# Índice de caché en memoria del proceso: cache_key -> {"archivo", "creado"}.
# La clave considera el texto ya normalizado + voz + modelo + parámetros de
# voz + formato de salida — si cualquiera de esos valores cambia, se genera
# un audio nuevo.
_cache: dict[str, dict] = {}

_ultima_limpieza = 0.0
_INTERVALO_LIMPIEZA_SEGUNDOS = 600  # como mucho una vez cada 10 minutos


def _ruta_audio(nombre_archivo: str) -> str:
    return os.path.join(AUDIO_CACHE_DIR, nombre_archivo)


def _url_publica(nombre_archivo: str) -> str:
    return f"{PUBLIC_BASE_URL}/static/audio/{nombre_archivo}"


def _limpiar_audios_antiguos() -> None:
    """Borra archivos de audio con más de AUDIO_TTL_SECONDS de antigüedad.
    Una llamada telefónica dura minutos, muy por debajo del TTL (1 hora por
    defecto), así que nunca se borra un audio que Twilio pueda estar
    todavía descargando."""
    global _ultima_limpieza

    ahora = time.time()

    if ahora - _ultima_limpieza < _INTERVALO_LIMPIEZA_SEGUNDOS:
        return

    _ultima_limpieza = ahora

    try:
        if not os.path.isdir(AUDIO_CACHE_DIR):
            return

        for nombre_archivo in os.listdir(AUDIO_CACHE_DIR):
            ruta = os.path.join(AUDIO_CACHE_DIR, nombre_archivo)

            try:
                antiguedad = ahora - os.path.getmtime(ruta)
            except OSError:
                continue

            if antiguedad > AUDIO_TTL_SECONDS:
                try:
                    os.remove(ruta)
                except OSError:
                    continue

        claves_vencidas = [
            clave
            for clave, entrada in _cache.items()
            if not os.path.exists(_ruta_audio(entrada["archivo"]))
        ]

        for clave in claves_vencidas:
            _cache.pop(clave, None)

    except Exception as error:
        logger.warning("[elevenlabs_service] limpieza de audios falló: %s", type(error).__name__)


def _calcular_cache_key(texto_normalizado: str, voice_id: str) -> str:
    partes = "|".join(
        [
            texto_normalizado,
            voice_id,
            ELEVENLABS_MODEL_ID,
            str(ELEVENLABS_STABILITY),
            str(ELEVENLABS_SIMILARITY_BOOST),
            ELEVENLABS_OUTPUT_FORMAT,
        ]
    )
    return hashlib.sha256(partes.encode("utf-8")).hexdigest()


def generar_audio_elevenlabs(
    texto: str,
    empresa_id: int | None = None,
    voice_id: str | None = None,
) -> str | None:
    """Genera (o reutiliza de caché) un audio con ElevenLabs para `texto` y
    devuelve su URL pública temporal, o None si no se pudo generar (el
    llamador debe usar <Say> como respaldo en ese caso).

    `voice_id` permite en el futuro una voz distinta por empresa; hoy todas
    usan ELEVENLABS_VOICE_ID salvo que se pase explícitamente."""
    _limpiar_audios_antiguos()

    if not texto or not texto.strip():
        return None

    if not ELEVENLABS_API_KEY or not PUBLIC_BASE_URL:
        return None

    voice_id = voice_id or ELEVENLABS_VOICE_ID
    texto_normalizado = normalizar_texto_voz(texto)
    cache_key = _calcular_cache_key(texto_normalizado, voice_id)

    entrada_cacheada = _cache.get(cache_key)

    if entrada_cacheada and os.path.exists(_ruta_audio(entrada_cacheada["archivo"])):
        return _url_publica(entrada_cacheada["archivo"])

    inicio = time.time()

    try:
        respuesta = requests.post(
            _ELEVENLABS_URL.format(voice_id=voice_id),
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            params={"output_format": ELEVENLABS_OUTPUT_FORMAT},
            json={
                "text": texto_normalizado,
                "model_id": ELEVENLABS_MODEL_ID,
                "voice_settings": {
                    "stability": ELEVENLABS_STABILITY,
                    "similarity_boost": ELEVENLABS_SIMILARITY_BOOST,
                },
            },
            timeout=ELEVENLABS_TIMEOUT_SECONDS,
        )

        if respuesta.status_code != 200:
            raise RuntimeError(f"status_code={respuesta.status_code}")

        contenido = respuesta.content

        if not contenido:
            raise RuntimeError("respuesta vacía")

        os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)

        nombre_archivo = f"{uuid.uuid4().hex}.mp3"
        ruta = _ruta_audio(nombre_archivo)

        with open(ruta, "wb") as archivo:
            archivo.write(contenido)

        _cache[cache_key] = {"archivo": nombre_archivo, "creado": time.time()}

        return _url_publica(nombre_archivo)

    except Exception as error:
        duracion_ms = int((time.time() - inicio) * 1000)
        logger.warning(
            "[elevenlabs_service] fallo generando audio (empresa_id=%s, tipo=%s, duracion_ms=%s)",
            empresa_id,
            type(error).__name__,
            duracion_ms,
        )
        return None
