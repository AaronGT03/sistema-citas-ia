from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import DATABASE_URL

import os
print("DATABASE_URL USADA:", DATABASE_URL, "| CWD:", os.getcwd())

connect_args = {}
engine_kwargs = {}

if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

    # Evita que SQLAlchemy reutilice conexiones pooleadas de SQLite con una
    # transacción/snapshot vieja abierta (síntoma: queries que no ven filas
    # recién escritas por otra conexión). Cada sesión abre su propia conexión.
    from sqlalchemy.pool import NullPool

    engine_kwargs["poolclass"] = NullPool

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    **engine_kwargs,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()