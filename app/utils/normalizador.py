from datetime import datetime, timedelta

MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

NUMEROS = {
    "uno": 1, "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16, "dieciséis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiuno": 21,
    "veintidos": 22, "veintidós": 22,
    "veintitres": 23, "veintitrés": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26, "veintiséis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
    "treinta": 30,
    "treinta y uno": 31,
}


def _obtener_numero(texto: str):
    texto = texto.lower()

    for palabra in sorted(NUMEROS, key=len, reverse=True):
        if palabra in texto:
            return NUMEROS[palabra]

    for parte in texto.split():
        if parte.isdigit():
            return int(parte)

    return None


def normalizar_fecha(texto: str) -> str | None:
    texto = texto.lower().strip()
    hoy = datetime.now()

    if texto == "pasado mañana":
        fecha = hoy + timedelta(days=2)
        return fecha.strftime("%d/%m/%Y")

    if texto == "mañana":
        fecha = hoy + timedelta(days=1)
        return fecha.strftime("%d/%m/%Y")

    dia = _obtener_numero(texto)
    mes = None

    for nombre_mes, numero_mes in MESES.items():
        if nombre_mes in texto:
            mes = numero_mes
            break

    if dia and mes and 1 <= dia <= 31:
        return f"{dia:02d}/{mes:02d}/{hoy.year}"

    return None


def normalizar_hora(texto: str) -> str | None:
    texto = texto.lower().strip()

    hora = _obtener_numero(texto)
    minutos = 0

    if hora is None:
        return None

    if "media" in texto:
        minutos = 30

    if "cuarto" in texto and "para" not in texto:
        minutos = 15

    if "para" in texto and "cuarto" in texto:
        hora -= 1
        minutos = 45

    if "tarde" in texto or "noche" in texto:
        if hora < 12:
            hora += 12

    if "mañana" in texto:
        if hora == 12:
            hora = 0

    if 1 <= hora <= 11:
        if (
            "mañana" not in texto
            and "tarde" not in texto
            and "noche" not in texto
        ):
            return "AMBIGUA"

    if hora < 0 or hora > 23:
        return None

    return f"{hora:02d}:{minutos:02d}"