def normalizar_telefono_mexico(telefono: str):
    telefono = telefono.replace("+", "").replace(" ", "").replace("-", "")

    if telefono.startswith("521") and len(telefono) == 13:
        telefono = "52" + telefono[3:]

    return telefono