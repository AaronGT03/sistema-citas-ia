import re

# =====================================================================
# Normalización de texto para el sintetizador de voz (ElevenLabs).
#
# Convierte precios, horas, fechas, teléfonos, abreviaciones y números
# sueltos a su forma hablada en español. Esto SOLO afecta el texto que se
# envía a ElevenLabs — nunca modifica lo que se guarda en Cita,
# Conversacion, Prestador o Servicio, ni lo que ve el dashboard.
# =====================================================================

_UNIDADES = [
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete",
    "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidós", "veintitrés",
    "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve",
]

_DECENAS = {
    30: "treinta", 40: "cuarenta", 50: "cincuenta", 60: "sesenta",
    70: "setenta", 80: "ochenta", 90: "noventa",
}

_CENTENAS = {
    100: "cien", 200: "doscientos", 300: "trescientos", 400: "cuatrocientos",
    500: "quinientos", 600: "seiscientos", 700: "setecientos", 800: "ochocientos",
    900: "novecientos",
}

_MESES_NOMBRE = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


def _menor_100(n: int) -> str:
    if n < 30:
        return _UNIDADES[n]

    decena = (n // 10) * 10
    resto = n % 10

    if resto == 0:
        return _DECENAS[decena]

    return f"{_DECENAS[decena]} y {_UNIDADES[resto]}"


def _menor_1000(n: int) -> str:
    if n < 100:
        return _menor_100(n)

    if n == 100:
        return "cien"

    centena = (n // 100) * 100
    resto = n % 100
    base = _CENTENAS[centena]

    if resto == 0:
        return base

    if centena == 100:
        base = "ciento"

    return f"{base} {_menor_100(resto)}"


def numero_a_texto_es(n: int) -> str:
    """Convierte un entero a su forma hablada en español. Autocontenido
    (no depende de num2words); cubre el rango realista de precios de
    servicios y horas de una cita (0 a 999,999)."""
    if n < 0:
        return f"menos {numero_a_texto_es(-n)}"

    if n == 0:
        return "cero"

    if n < 1000:
        return _menor_1000(n)

    if n < 1_000_000:
        miles = n // 1000
        resto = n % 1000

        texto_miles = "mil" if miles == 1 else f"{_forma_apocopada(_menor_1000(miles))} mil"

        if resto == 0:
            return texto_miles

        return f"{texto_miles} {_menor_1000(resto)}"

    # Fuera del rango esperado; se deja el número tal cual como respaldo
    # seguro en vez de fallar.
    return str(n)


def _forma_apocopada(texto_numero: str) -> str:
    """"uno" -> "un", "veintiuno" -> "veintiún", "treinta y uno" -> "treinta
    y un" — la forma natural antes de un sustantivo masculino como "pesos"."""
    if texto_numero == "uno":
        return "un"

    if texto_numero.endswith("veintiuno"):
        return texto_numero[: -len("veintiuno")] + "veintiún"

    if texto_numero.endswith(" y uno"):
        return texto_numero[: -len("uno")] + "un"

    return texto_numero


_RE_TELEFONO = re.compile(r"(?:\+?52\s?)?\b(\d{10})\b")


def _telefono_a_texto(match: re.Match) -> str:
    digitos = match.group(1)
    return " ".join(_UNIDADES[int(d)] for d in digitos)


_ABREVIACIONES = [
    (re.compile(r"\bAv\.\s?", re.IGNORECASE), "Avenida "),
    (re.compile(r"\bCol\.\s?", re.IGNORECASE), "Colonia "),
    (re.compile(r"\bC\.P\.\s?", re.IGNORECASE), "código postal "),
    (re.compile(r"\bNo\.\s?", re.IGNORECASE), "número "),
    (re.compile(r"#\s?"), "número "),
    (re.compile(r"\bkm\b", re.IGNORECASE), "kilómetros"),
    (re.compile(r"\bkg\b", re.IGNORECASE), "kilogramos"),
    (re.compile(r"%"), " por ciento"),
    (re.compile(r"&"), " y "),
]

_RE_PRECIO = re.compile(r"\$\s?(\d+)(?:\.(\d{1,2}))?")


def _precio_a_texto(match: re.Match) -> str:
    entero = int(match.group(1))
    centavos_texto = match.group(2)

    texto_entero = _forma_apocopada(numero_a_texto_es(entero))
    resultado = f"{texto_entero} peso" if entero == 1 else f"{texto_entero} pesos"

    if centavos_texto:
        centavos = int(centavos_texto.ljust(2, "0"))

        if centavos > 0:
            texto_centavos = _forma_apocopada(numero_a_texto_es(centavos))
            sufijo = "centavo" if centavos == 1 else "centavos"
            resultado += f" con {texto_centavos} {sufijo}"

    return resultado


_RE_HORA = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def _hora_a_texto(match: re.Match) -> str:
    hora = int(match.group(1))
    minutos = int(match.group(2))

    if hora == 0:
        base, periodo = "doce", "de la noche"
    elif hora == 12:
        base, periodo = "doce", "del día"
    elif hora < 12:
        base, periodo = numero_a_texto_es(hora), "de la mañana"
    elif hora < 20:
        base, periodo = numero_a_texto_es(hora - 12), "de la tarde"
    else:
        base, periodo = numero_a_texto_es(hora - 12), "de la noche"

    if minutos == 0:
        return f"{base} {periodo}"

    return f"{base} {numero_a_texto_es(minutos)} {periodo}"


_RE_FECHA = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def _fecha_a_texto(match: re.Match) -> str:
    dia = int(match.group(1))
    mes = int(match.group(2))
    nombre_mes = _MESES_NOMBRE.get(mes)

    if not nombre_mes:
        return match.group(0)

    return f"{numero_a_texto_es(dia)} de {nombre_mes}"


# "a las diez de la mañana horas" -> "a las diez de la mañana" (la palabra
# "horas" queda redundante una vez que _hora_a_texto ya agregó "de la
# mañana/tarde/noche"/"del día").
_RE_HORAS_REDUNDANTE = re.compile(
    r"\b(de la (?:mañana|tarde|noche)|del día)\s+horas\b", re.IGNORECASE
)

_RE_NUMERO_SUELTO = re.compile(r"\b\d{1,3}\b")

# "2x Corte de cabello" -> "2 Corte de cabello" antes de convertir el
# número a palabras, por si algún resumen usa esa notación.
_RE_CANTIDAD_X = re.compile(r"\b(\d+)\s*x\b", re.IGNORECASE)


def _numero_suelto_a_texto(match: re.Match) -> str:
    return numero_a_texto_es(int(match.group(0)))


def normalizar_texto_voz(texto: str) -> str:
    """Adapta un texto en español para que se escuche natural al
    pronunciarlo por teléfono: precios, horas, fechas, teléfonos,
    abreviaciones y cantidades sueltas. No modifica el texto original en
    la base de datos — solo lo que se manda a ElevenLabs."""
    if not texto:
        return texto

    resultado = texto

    resultado = _RE_TELEFONO.sub(_telefono_a_texto, resultado)

    for patron, reemplazo in _ABREVIACIONES:
        resultado = patron.sub(reemplazo, resultado)

    resultado = _RE_CANTIDAD_X.sub(r"\1", resultado)
    resultado = _RE_PRECIO.sub(_precio_a_texto, resultado)
    resultado = _RE_HORA.sub(_hora_a_texto, resultado)
    resultado = _RE_HORAS_REDUNDANTE.sub(r"\1", resultado)
    resultado = _RE_FECHA.sub(_fecha_a_texto, resultado)
    resultado = _RE_NUMERO_SUELTO.sub(_numero_suelto_a_texto, resultado)

    return re.sub(r"\s+", " ", resultado).strip()
