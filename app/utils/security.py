from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta

SECRET_KEY = "clave_super_secreta_cambiala_despues"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def generar_hash(password):
    return pwd_context.hash(password)


def verificar_password(password, password_hash):
    return pwd_context.verify(password, password_hash)


def crear_token(data: dict):
    datos = data.copy()

    expiracion = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    datos.update({"exp": expiracion})

    token = jwt.encode(datos, SECRET_KEY, algorithm=ALGORITHM)

    return token


def verificar_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload

    except JWTError:
        return None
