import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./citas.db",
)

# ElevenLabs (texto a voz para las llamadas). Nunca se imprimen ni se
# registran en logs. Si ELEVENLABS_API_KEY o PUBLIC_BASE_URL faltan, el
# sistema usa automáticamente <Say> de Twilio como respaldo.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "b2htR0pMe28pYwCY9gnP")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
ELEVENLABS_OUTPUT_FORMAT = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_22050_32")
ELEVENLABS_STABILITY = float(os.getenv("ELEVENLABS_STABILITY", "0.5"))
ELEVENLABS_SIMILARITY_BOOST = float(os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.75"))
ELEVENLABS_TIMEOUT_SECONDS = float(os.getenv("ELEVENLABS_TIMEOUT_SECONDS", "10"))

# URL pública del backend (necesaria para que Twilio pueda descargar los
# audios generados vía <Play>). Sin esta variable, no se llama a ElevenLabs.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# Almacenamiento temporal de audios generados.
AUDIO_CACHE_DIR = os.getenv("AUDIO_CACHE_DIR", "app/static/audio")
AUDIO_TTL_SECONDS = int(os.getenv("AUDIO_TTL_SECONDS", "3600"))