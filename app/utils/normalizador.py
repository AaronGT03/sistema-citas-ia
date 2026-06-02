from datetime import datetime

MESES = {
    "enero": "01",
    "febrero": "02",
    "marzo": "03",
    "abril": "04",
    "mayo": "05",
    "junio": "06",
    "julio": "07",
    "agosto": "08",
    "septiembre": "09",
    "setiembre": "09",
    "octubre": "10",
    "noviembre": "11",
    "diciembre": "12",
}

NUMEROS = {
    "uno": 1,
    "una": 1,
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
    "dieciseis": 16,
    "dieciséis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiuno": 21,
    "veintidos": 22,
    "veintidós": 22,
    "veintitres": 23,
    "veintitrés": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintiséis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
    "treinta": 30,
    "treinta y uno": 31
}

def normalizar_fecha(texto):
    texto = texto.lower().strip()

    dia = None
    mes = None

    for palabra, numero in NUMEROS.items():
        if palabra in texto:
            dia = str(numero).zfill(2)

    for p in texto.split():
        if p.isdigit():
            dia = p.zfill(2)

    for nombre_mes, numero_mes in MESES.items():
        if nombre_mes in texto:
            mes = numero_mes

    if dia and mes:
        return f"{dia}/{mes}/{datetime.now().year}"

    return texto
    
def normalizar_hora(texto):
    texto = texto.lower().strip()

    hora = None

    for palabra, numero in NUMEROS.items():
        if palabra in texto:
            hora = numero

    for p in texto.split():
        if p.isdigit():
            hora = int(p)

    if hora is None:
        return texto

    if "tarde" in texto:
        if hora < 12:
            hora += 12

    elif "noche" in texto:
        if hora < 12:
            hora += 12

    return f"{hora:02d}:00"
