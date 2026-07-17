# Sistema Citas IA

Sistema de gestión de citas por llamada telefónica utilizando inteligencia artificial.

## Tecnologías

- Python 3.14
- FastAPI
- SQLAlchemy
- SQLite
- Twilio
- Ngrok

## Funcionalidades

- Crear citas
- Consultar citas por teléfono
- Cancelar citas
- Reprogramar citas
- Recepción de llamadas mediante Twilio
- Reconocimiento de voz
- Consulta automática de citas

## Instalación

```bash
pip install -r requirements.txt
```

## Ejecutar

```bash
uvicorn app:app --reload
```

## Migraciones (Alembic)

El esquema de base de datos se versiona con Alembic (`migrations/`). `Base.metadata.create_all` en `app/main.py` solo crea tablas nuevas en una base vacía; para aplicar cambios de esquema en una base ya existente (agregar columnas/tablas) se debe usar Alembic.

```bash
# Aplicar todas las migraciones pendientes (dev y producción)
alembic upgrade head

# Generar una nueva migración a partir de cambios en app/models
alembic revision --autogenerate -m "descripcion del cambio"
```

`migrations/env.py` toma `DATABASE_URL` de `app.core.config`, por lo que usa la misma base configurada por variable de entorno (SQLite en desarrollo, PostgreSQL en producción).

## Autor

Aaron Galarza


## Autor

Ivan Montelongo Jimenez
