from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routers.citas import router as citas_router
from app.routers.llamadas import router as llamadas_router
from app.routers.empresas import router as empresas_router
from app.routers.auth import router as auth_router
from app.routers.usuarios import router as usuarios_router
from app.routers.servicios import router as servicios_router
from app.routers.whatsapp import router as whatsapp_router

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(usuarios_router)
app.include_router(empresas_router)
app.include_router(servicios_router)
app.include_router(citas_router)
app.include_router(llamadas_router)
app.include_router(whatsapp_router)


@app.get("/")
def home():
    return {"status": "ok"}